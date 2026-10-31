"""
PTZ 自动跟踪闭环服务

目标被锁定后，云台自动转动把它保持在画面中心。

职责边界：
- 只做 pan/tilt，不做自动变焦（变焦会改变目标尺度，跨帧匹配更难稳定）
- 不认识"谁是谁"，只认 PTZMetrics 里那个被锁定的目标
- 不负责选择目标：目标由事件进程的 autotrack 回调锁定（第一个出现的合格目标）

跨进程数据流：
    任务子进程 TrackedObjectProcessor
      → PTZMetrics.update_target()  写归一化目标位置
    主进程本服务（单线程，固定周期 tick）
      → 读目标位置 → 死区 + 比例控制 → PTZController.move_velocity()
      → PTZMetrics.set_motor_moving()  告诉跟踪侧"云台在动"
    任务子进程 NorfairTracker
      → 读 motor_moving，转动期间放宽 IoU 匹配，避免目标 ID 断裂

为什么控制放在主进程：
    ONVIF 客户端（OnvifCameraClient / PTZController）持有 socket 连接，不能跨进程共享；
    任务进程是独立 multiprocessing.Process，只能在主进程这一侧驱动真正的云台。
"""

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from src.tracking.ptz_metrics import PTZMetrics

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CONFIG_PATH = _PROJECT_ROOT / 'config' / 'ptz_autotrack.yaml'

_DEFAULT_CONFIG: Dict[str, object] = {
    'enabled': True,
    'tick_interval': 0.2,
    'deadzone_x': 0.12,
    'deadzone_y': 0.12,
    'speed_min': 0.12,
    'speed_max': 0.55,
    'speed_full_offset': 0.35,
    'pan_sign': 1,
    'tilt_sign': -1,
    'lock_labels': [],
    'min_frames': 3,
    'lost_timeout': 1.5,
    'min_target_height': 0.02,
    'max_continuous_move': 20.0,
    'cooldown_after_max': 5.0,
    'controller_retry_interval': 60.0,
}

_config_cache: Optional[dict] = None
_config_lock = threading.Lock()


# ========================================
# 配置
# ========================================

def _load_config() -> dict:
    cfg = dict(_DEFAULT_CONFIG)
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            section = data.get('ptz_autotrack') or {}
            if isinstance(section, dict):
                for key, value in section.items():
                    if value is not None and key in cfg:
                        cfg[key] = value
    except Exception as e:
        logger.warning(f"[PTZAutoTrack] 读取配置失败，使用默认值: {e}")
    return cfg


def get_autotrack_config() -> dict:
    """读取配置（带缓存，热更新用 reload_autotrack_config）"""
    global _config_cache
    with _config_lock:
        if _config_cache is None:
            _config_cache = _load_config()
        return _config_cache


def reload_autotrack_config() -> dict:
    """强制重新读取配置文件"""
    global _config_cache
    with _config_lock:
        _config_cache = None
    return get_autotrack_config()


def is_autotrack_enabled() -> bool:
    return bool(get_autotrack_config().get('enabled', True))


# ========================================
# 单相机运行态
# ========================================

@dataclass
class _CameraRuntime:
    """单相机控制状态（仅主进程内使用，不需要跨进程共享）"""
    metrics: PTZMetrics
    moving: bool = False              # 本服务是否已下发转动
    move_started_at: float = 0.0      # 本次连续转动的开始时间（wall clock）
    cooldown_until: float = 0.0       # 冷却截止时间
    last_reason: str = ''


# ========================================
# 服务
# ========================================

class PTZAutoTrackService:
    """PTZ 自动跟踪闭环（进程内单例，单线程控制）"""

    def __init__(self):
        self._lock = threading.RLock()
        self._cameras: Dict[str, _CameraRuntime] = {}
        self._controllers: Dict[str, object] = {}
        self._controller_retry_at: Dict[str, float] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._move_total = 0
        # 时间基准，默认 wall clock。仿真器会替换成虚拟时钟，
        # 否则"加速回放"时 lost_timeout / 冷却都会按真实秒数计算而失真。
        self._clock = time.time

    # ---------- 生命周期 ----------

    def start(self) -> bool:
        """启动控制线程（幂等）"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            if not is_autotrack_enabled():
                logger.info("[PTZAutoTrack] 配置未启用，服务不启动")
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name='PTZAutoTrack', daemon=True)
            self._thread.start()
        logger.info("[PTZAutoTrack] 自动跟踪服务已启动")
        return True

    def stop(self) -> None:
        """停止控制线程，并让所有云台停转"""
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        with self._lock:
            runtimes = list(self._cameras.items())
        for camera_id, rt in runtimes:
            self._halt(camera_id, rt, reason='service_stop')
        logger.info("[PTZAutoTrack] 自动跟踪服务已停止")

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------- 注册 ----------

    def ensure_metrics(
        self, camera_id: str, *, autotracker_enabled: Optional[bool] = None
    ) -> PTZMetrics:
        """
        取得（或创建）相机的共享状态对象

        同一个 camera_id 多次调用返回同一实例——云台是物理资源，
        即使多任务共用同一相机也只能有一个控制状态。
        """
        camera_id = str(camera_id)
        with self._lock:
            rt = self._cameras.get(camera_id)
            if rt is not None:
                if autotracker_enabled is not None:
                    rt.metrics.autotracker_enabled.value = int(bool(autotracker_enabled))
                return rt.metrics

            enabled = (
                bool(get_autotrack_config().get('enabled', True))
                if autotracker_enabled is None
                else bool(autotracker_enabled)
            )
            metrics = PTZMetrics(autotracker_enabled=enabled)
            self._cameras[camera_id] = _CameraRuntime(metrics=metrics)
            logger.info(f"[PTZAutoTrack] 注册相机 {camera_id}（autotracker={enabled}）")
            return metrics

    def get_metrics(self, camera_id: str) -> Optional[PTZMetrics]:
        with self._lock:
            rt = self._cameras.get(str(camera_id))
            return rt.metrics if rt else None

    def unregister(self, camera_id: str) -> None:
        """相机关联的任务全部停止时调用"""
        camera_id = str(camera_id)
        with self._lock:
            rt = self._cameras.pop(camera_id, None)
            self._controllers.pop(camera_id, None)
            self._controller_retry_at.pop(camera_id, None)
        if rt is not None:
            self._halt(camera_id, rt, reason='unregister')
            logger.info(f"[PTZAutoTrack] 注销相机 {camera_id}")

    def snapshot(self) -> Dict[str, object]:
        """全部相机状态（供 API）"""
        with self._lock:
            items = list(self._cameras.items())
        result = {}
        for camera_id, rt in items:
            snap = rt.metrics.snapshot()
            snap['service_moving'] = rt.moving
            snap['last_reason'] = rt.last_reason
            result[camera_id] = snap
        return {
            'running': self.is_running,
            'cameras': result,
            'move_total': self._move_total,
        }

    # ---------- 控制循环 ----------

    def _loop(self) -> None:
        logger.info("[PTZAutoTrack] 控制线程进入主循环")
        while not self._stop.is_set():
            cfg = get_autotrack_config()
            interval = float(cfg.get('tick_interval') or 0.2)
            try:
                self._tick(cfg)
            except Exception as e:
                logger.warning(f"[PTZAutoTrack] 控制循环异常: {e}")
            self._stop.wait(max(0.05, interval))
        logger.info("[PTZAutoTrack] 控制线程退出")

    def _tick(self, cfg: dict) -> None:
        now = self._clock()
        with self._lock:
            runtimes = list(self._cameras.items())
        for camera_id, rt in runtimes:
            try:
                self._tick_one(camera_id, rt, cfg, now)
            except Exception as e:
                logger.debug(f"[PTZAutoTrack] {camera_id} 单路控制异常: {e}")

    def _tick_one(self, camera_id: str, rt: _CameraRuntime, cfg: dict, now: float) -> None:
        metrics = rt.metrics

        # 开关关闭：确保停转
        if not metrics.is_tracking_enabled():
            if rt.moving:
                self._halt(camera_id, rt, reason='disabled')
            return

        # 冷却中
        if now < rt.cooldown_until:
            if rt.moving:
                self._halt(camera_id, rt, reason='cooldown')
            return

        target = metrics.read_target()
        lost_timeout = float(cfg.get('lost_timeout') or 1.5)
        if target is None or (now - float(target['seen_ts'])) > lost_timeout:
            if rt.moving:
                self._halt(camera_id, rt, reason='target_lost')
            return

        # 目标刚出现，等几帧稳定再动手
        if int(target['frames']) < int(cfg.get('min_frames') or 3):
            return

        # 目标太小，转过去也看不清，不追
        if float(target['h']) < float(cfg.get('min_target_height') or 0.0):
            if rt.moving:
                self._halt(camera_id, rt, reason='target_too_small')
            return

        # 安全阀：连续转动超时，强制停转后冷却
        max_move = float(cfg.get('max_continuous_move') or 20.0)
        if rt.moving and (now - rt.move_started_at) > max_move:
            logger.warning(
                f"[{camera_id}] PTZ 连续转动超过 {max_move:.0f}s，强制停转并冷却 "
                f"{float(cfg.get('cooldown_after_max') or 5.0):.0f}s"
            )
            self._halt(camera_id, rt, reason='max_move_exceeded')
            rt.cooldown_until = now + float(cfg.get('cooldown_after_max') or 5.0)
            return

        offset_x = float(target['cx']) - 0.5
        offset_y = float(target['cy']) - 0.5

        pan = self._speed_for(offset_x, cfg.get('deadzone_x'), cfg) * float(
            cfg.get('pan_sign') or 1)
        tilt = self._speed_for(offset_y, cfg.get('deadzone_y'), cfg) * float(
            cfg.get('tilt_sign') or -1)

        # 目标已在死区内，停转（避免持续微调导致画面抖）
        if pan == 0.0 and tilt == 0.0:
            if rt.moving:
                self._halt(camera_id, rt, reason='centered')
            return

        controller = self._resolve_controller(camera_id, cfg, now)
        if controller is None:
            return

        if not controller.move_velocity(pan, tilt, 0.0):
            return

        if not rt.moving:
            rt.moving = True
            rt.move_started_at = now
            self._move_total += 1
            logger.info(
                f"[{camera_id}] PTZ 开始跟随 label={target['label']} "
                f"id={target['obj_id'] or '-'} 偏移=({offset_x:+.2f},{offset_y:+.2f}) "
                f"速度=({pan:+.2f},{tilt:+.2f})"
            )
        rt.last_reason = 'following'
        metrics.set_motor_moving(True)

    @staticmethod
    def _speed_for(offset: float, deadzone, cfg: dict) -> float:
        """
        偏移量 → 速度（死区外线性映射）

        Args:
            offset: 目标中心相对画面中心的偏移（-0.5 ~ 0.5）
            deadzone: 死区半宽（归一化）
            cfg: 配置

        Returns:
            float: 带符号速度，0 表示不动
        """
        dz = max(0.0, float(deadzone or 0.0))
        magnitude = abs(float(offset))
        if magnitude <= dz:
            return 0.0

        full = max(dz + 1e-3, float(cfg.get('speed_full_offset') or 0.35))
        ratio = min(1.0, (magnitude - dz) / (full - dz))
        v_min = max(0.0, float(cfg.get('speed_min') or 0.0))
        v_max = max(v_min, float(cfg.get('speed_max') or 0.5))
        speed = min(v_max, v_min + ratio * (v_max - v_min))
        return math.copysign(speed, offset)

    def _halt(self, camera_id: str, rt: _CameraRuntime, reason: str = '') -> None:
        """停转并复位状态"""
        controller = self._controllers.get(camera_id)
        if controller is not None:
            try:
                controller.stop_move()
            except Exception as e:
                logger.debug(f"[PTZAutoTrack] {camera_id} 停止指令失败: {e}")

        if rt.moving:
            logger.info(f"[{camera_id}] PTZ 停止转动（{reason or '-'}）")
        rt.moving = False
        rt.last_reason = reason

        try:
            rt.metrics.set_motor_moving(False)
        except Exception:
            pass

    # ---------- 测试/仿真钩子 ----------

    def set_clock(self, clock) -> None:
        """
        替换时间基准（仿真用）

        控制逻辑全部以 now 判断超时与冷却。仿真按虚拟时间推进时，
        必须让服务读同一个时钟，否则 lost_timeout 会按真实秒数误判。
        """
        self._clock = clock if callable(clock) else time.time

    def attach_controller(self, camera_id: str, controller) -> None:
        """
        直接挂载 PTZ 控制器，跳过 ONVIF 连接

        两个用途：
        - 仿真/测试：注入假云台，无需真实设备
        - 生产：复用外部已建立的控制器实例，避免重复建连
        """
        camera_id = str(camera_id)
        with self._lock:
            self._cameras.setdefault(
                camera_id, _CameraRuntime(metrics=self.ensure_metrics(camera_id))
            )
            self._controllers[camera_id] = controller

    def tick_once(self) -> None:
        """同步执行一次控制迭代（仿真/测试用，不依赖后台线程）"""
        self._tick(get_autotrack_config())

    def _resolve_controller(self, camera_id: str, cfg: dict, now: float):
        """
        惰性获取 PTZ 控制器

        首次访问要建立 ONVIF 连接（可能几百毫秒），因此失败后按
        controller_retry_interval 冷却，避免每个 tick 都去连一台不支持 PTZ 的相机。
        """
        controller = self._controllers.get(camera_id)
        if controller is not None:
            return controller

        retry_interval = float(cfg.get('controller_retry_interval') or 60.0)
        if now < self._controller_retry_at.get(camera_id, 0.0):
            return None

        try:
            from src.cameras.ptz_api import get_ptz_controller_for_camera
            controller = get_ptz_controller_for_camera(camera_id)
        except Exception as e:
            logger.warning(f"[PTZAutoTrack] {camera_id} 获取 PTZ 控制器异常: {e}")
            controller = None

        if controller is None:
            self._controller_retry_at[camera_id] = now + retry_interval
            logger.info(
                f"[PTZAutoTrack] {camera_id} 暂无可用 PTZ 控制器，"
                f"{retry_interval:.0f}s 后重试"
            )
            return None

        self._controllers[camera_id] = controller
        logger.info(f"[PTZAutoTrack] {camera_id} PTZ 控制器就绪")
        return controller


# ========================================
# 进程内单例
# ========================================

_service: Optional[PTZAutoTrackService] = None
_service_lock = threading.Lock()


def get_ptz_autotrack_service() -> PTZAutoTrackService:
    global _service
    with _service_lock:
        if _service is None:
            _service = PTZAutoTrackService()
        return _service


def build_metrics_map(camera_ids: List[str]) -> Dict[str, PTZMetrics]:
    """
    为任务涉及的相机准备 PTZMetrics（主进程调用，注入任务进程）

    只注册声明支持云台（support_move / support_zoom）的相机：
    没有云台能力的相机如果也注册，控制线程会周期性地尝试建立 ONVIF 连接，
    一次超时就能把整个控制线程卡住，连带影响其他真正需要跟拍的相机。

    Returns:
        {camera_id: PTZMetrics}；总开关关闭时返回空 dict
    """
    if not is_autotrack_enabled():
        return {}

    service = get_ptz_autotrack_service()

    try:
        from src.cameras.camera_manager import get_camera_manager
        camera_manager = get_camera_manager()
    except Exception as e:
        logger.warning(f"[PTZAutoTrack] 无法获取相机管理器，跳过云台能力过滤: {e}")
        camera_manager = None

    result: Dict[str, PTZMetrics] = {}
    for camera_id in camera_ids or []:
        if not camera_id:
            continue
        camera_id = str(camera_id)

        if camera_manager is not None:
            try:
                camera = camera_manager.get_camera(camera_id)
            except Exception:
                camera = None
            if camera is not None and not (
                getattr(camera, 'support_move', False)
                or getattr(camera, 'support_zoom', False)
            ):
                # 明确不支持云台，不注册，避免无谓的 ONVIF 连接尝试
                continue

        try:
            result[camera_id] = service.ensure_metrics(camera_id)
        except Exception as e:
            logger.warning(f"[PTZAutoTrack] 为相机 {camera_id} 创建共享状态失败: {e}")
    return result


def start_ptz_autotrack_service() -> bool:
    """启动服务（供 run.py 调用）"""
    return get_ptz_autotrack_service().start()


def stop_ptz_autotrack_service() -> None:
    global _service
    with _service_lock:
        service = _service
    if service is not None:
        service.stop()
