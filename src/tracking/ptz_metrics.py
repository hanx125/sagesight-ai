"""
PTZ 指标共享状态

用于三个进程/线程之间共享 PTZ 自动跟踪状态:

    TrackedObjectProcessor（任务子进程）
        └─ 写：目标归一化位置、可见/丢失计数
    NorfairTracker（同一子进程）
        └─ 读：motor_moving 判断云台是否在转，决定是否放宽匹配
    PTZAutoTrackService（主进程控制线程）
        └─ 读：目标位置 → 算速度 → 驱动 PTZController
        └─ 写：motor_moving、移动起止时间

⚠️ 时间基准说明（踩过的坑）：
    frame_time 是"视频相对时间"（从视频开始的秒数），而控制线程只有 wall clock。
    两者相减没有意义，早期用 start_time/stop_time 与 frame_time 比较判断
    "云台是否在移动"永远得到错误结果。因此改为用 motor_moving 这个显式标志位，
    start_time/stop_time 降级为诊断用途（wall clock，仅用于日志和前端展示）。
"""

import multiprocessing as mp
import time
from typing import Dict, Optional

# 跨进程字符串字段长度（字节）
_LABEL_MAX = 32
_OBJ_ID_MAX = 64
_TARGET_KEY_MAX = 64


def _write_str(arr, text) -> None:
    """写入固定长度共享字符数组（超长截断，补 \\0）"""
    size = len(arr)
    raw = str(text or '').encode('utf-8')[:_TARGET_KEY_MAX][: size - 1]
    try:
        arr.raw = raw.ljust(size, b'\x00')
    except Exception:
        # 个别平台 raw 赋值不可用，退回逐字节写入
        for i in range(size):
            arr[i] = raw[i:i + 1] if i < len(raw) else b'\x00'


def _read_str(arr) -> str:
    """读取固定长度共享字符数组"""
    try:
        raw = bytes(arr.raw)
    except Exception:
        raw = b''.join(bytes(arr[i]) for i in range(len(arr)))
    return raw.split(b'\x00', 1)[0].decode('utf-8', errors='ignore')


class PTZMetrics:
    """
    PTZ 自动跟踪跨进程共享状态

    所有字段使用 multiprocessing 共享对象，可在任务子进程、
    主进程控制线程之间共享（通过 task_config['_ptz_metrics'] 注入）。

    使用方式:
        ptz_metrics = PTZMetrics(autotracker_enabled=True)
        # 事件进程侧
        tracker = NorfairTracker(..., ptz_metrics=ptz_metrics)
        # 主进程侧
        service.register(camera_id, ptz_metrics)
    """

    def __init__(self, *, autotracker_enabled: bool = False):
        # 部署级总开关（0=禁用, 1=启用）
        self.autotracker_enabled = mp.Value("i", int(autotracker_enabled))

        # 运行期开关：允许按相机单独启停，不影响部署配置
        self.enabled = mp.Value("i", 1)

        # 云台移动起止时间（wall clock 秒，仅诊断/展示，不参与控制判断）
        self.start_time = mp.Value("d", 0.0)
        self.stop_time = mp.Value("d", 0.0)

        # 当前帧时间（视频相对时间，由事件进程写入）
        self.frame_time = mp.Value("d", 0.0)

        # 变焦级别
        self.zoom_level = mp.Value("d", 0.0)
        self.max_zoom = mp.Value("d", 0.0)
        self.min_zoom = mp.Value("d", 0.0)

        # 云台是否正在移动（控制线程写，跟踪进程读）
        # 这是跨进程判断"要不要放宽匹配阈值"的唯一可靠依据，见模块 docstring。
        self.motor_moving = mp.Value("i", 0)

        # ========================================
        # 跟踪目标（归一化坐标 0~1，相对画面宽高）
        # ========================================
        # 用归一化而非像素，避免控制端需要知道分辨率
        self.target_cx = mp.Value("d", 0.5)
        self.target_cy = mp.Value("d", 0.5)
        self.target_w = mp.Value("d", 0.0)
        self.target_h = mp.Value("d", 0.0)
        self.target_label = mp.Array("c", _LABEL_MAX)
        self.target_obj_id = mp.Array("c", _OBJ_ID_MAX)

        # 最后一次收到目标的 wall clock 时间（控制线程用来判断超时）
        self.target_seen_ts = mp.Value("d", 0.0)
        # 连续可见帧数 / 连续丢失帧数
        self.target_frames = mp.Value("i", 0)
        self.lost_frames = mp.Value("i", 0)

        # 跟踪状态事件
        self.tracking_active = mp.Event()
        self.motor_stopped = mp.Event()
        self.reset = mp.Event()

        # 初始状态:马达已停止
        self.motor_stopped.set()

    # ========================================
    # 开关状态
    # ========================================

    def is_autotracker_enabled(self) -> bool:
        """部署级开关是否打开"""
        return bool(self.autotracker_enabled.value)

    def is_tracking_enabled(self) -> bool:
        """部署级 + 运行期开关是否都打开"""
        return self.is_autotracker_enabled() and bool(self.enabled.value)

    def set_runtime_enabled(self, enabled: bool) -> None:
        """设置运行期开关（API 调用）"""
        self.enabled.value = int(bool(enabled))

    def is_ptz_moving_at(self, frame_time: float = 0.0) -> bool:
        """
        云台当前是否正在移动

        Args:
            frame_time: 保留参数以兼容旧调用（不再参与判断，
                        视频相对时间与 wall clock 不可比）

        Returns:
            bool: 云台是否正在移动
        """
        return bool(self.motor_moving.value)

    def set_motor_moving(self, moving: bool) -> None:
        """
        标记云台移动状态（仅控制线程调用）

        维护 motor_moving 标志，并顺带记录 wall clock 起止时间用于诊断。
        """
        if moving:
            self.motor_moving.value = 1
            self.motor_stopped.clear()
            # 从静止转为移动：开启新的移动窗口
            if self.start_time.value == 0.0 or self.stop_time.value != 0.0:
                self.start_time.value = time.time()
                self.stop_time.value = 0.0
        else:
            if self.motor_moving.value:
                self.stop_time.value = time.time()
            self.motor_moving.value = 0
            self.motor_stopped.set()

    # ========================================
    # 目标位置
    # ========================================

    def update_target(
        self,
        cx: float,
        cy: float,
        w: float = 0.0,
        h: float = 0.0,
        *,
        label: str = '',
        obj_id: str = '',
        ts: Optional[float] = None,
        frame_time: Optional[float] = None,
        refresh_seen: bool = True,
    ) -> None:
        """
        更新跟踪目标位置（事件进程调用）

        Args:
            cx/cy: 目标框中心点，归一化到 0~1
            w/h: 目标框宽高，归一化到 0~1
            label: 目标类别（仅日志用）
            obj_id: 跟踪对象 ID（仅日志用）
            ts: wall clock 时间戳，默认取当前时间
            frame_time: 视频相对时间（写回 frame_time 字段）
            refresh_seen: 是否刷新"最后真实可见时间"

                ⚠️ 本帧由 Kalman 外推出来的框（tracker.disappeared > 0）必须传
                False。位置照常写入，让控制端在运动模糊的几帧里仍有平滑输入；
                但 seen_ts 不刷新，否则目标已经走出画面、轨迹还要 1.8s 才注销，
                这期间云台会一直追着一个不存在的目标转到限位。
                lost_timeout 必须从"最后一次真实检测"算起。
        """
        self.target_cx.value = float(cx)
        self.target_cy.value = float(cy)
        self.target_w.value = float(w)
        self.target_h.value = float(h)
        if refresh_seen:
            self.target_seen_ts.value = float(ts if ts is not None else time.time())
            self.target_frames.value = int(self.target_frames.value) + 1
            self.tracking_active.set()
        self.lost_frames.value = 0
        if frame_time is not None:
            self.frame_time.value = float(frame_time)
        _write_str(self.target_label, label)
        _write_str(self.target_obj_id, obj_id)

    def read_target(self) -> Optional[Dict[str, object]]:
        """
        读取当前跟踪目标（控制线程调用）

        注意：这里不因 lost_frames>0 就返回 None。云台转动时目标可能因为
        运动模糊/编码延迟短暂丢一两帧，若立即返回 None 会让控制端频繁
        起停抖动。是否真的跟丢由调用方用 seen_ts 超时判断。

        Returns:
            dict 或 None（从未有过目标，或已被 clear_target 清空）
        """
        if self.target_w.value <= 0.0 or self.target_h.value <= 0.0:
            return None
        return {
            'cx': float(self.target_cx.value),
            'cy': float(self.target_cy.value),
            'w': float(self.target_w.value),
            'h': float(self.target_h.value),
            'label': _read_str(self.target_label),
            'obj_id': _read_str(self.target_obj_id),
            'seen_ts': float(self.target_seen_ts.value),
            'frames': int(self.target_frames.value),
            'lost_frames': int(self.lost_frames.value),
        }

    def mark_lost(self) -> None:
        """标记目标丢失一帧（事件进程调用）"""
        self.lost_frames.value = int(self.lost_frames.value) + 1
        if self.lost_frames.value > 0:
            self.tracking_active.clear()

    def clear_target(self) -> None:
        """清空跟踪目标（事件进程调用，目标结束或取消锁定时）"""
        self.tracking_active.clear()
        self.target_frames.value = 0
        self.lost_frames.value = 0
        self.target_w.value = 0.0
        self.target_h.value = 0.0
        _write_str(self.target_label, '')
        _write_str(self.target_obj_id, '')

    def snapshot(self) -> Dict[str, object]:
        """当前状态快照（供 API 返回给前端）"""
        return {
            'autotracker_enabled': self.is_autotracker_enabled(),
            'enabled': bool(self.enabled.value),
            'tracking_active': self.tracking_active.is_set(),
            'motor_moving': bool(self.motor_moving.value),
            'target': self.read_target(),
            'target_frames': int(self.target_frames.value),
            'lost_frames': int(self.lost_frames.value),
            'start_time': float(self.start_time.value),
            'stop_time': float(self.stop_time.value),
            'zoom_level': float(self.zoom_level.value),
        }
