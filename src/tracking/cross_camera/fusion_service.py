"""
跨相机融合服务

一个后台线程，每秒扫一次"还没归属身份"的最终事件，把它挂到某个身份上。
之所以放在事件落库之后（而不是帧处理里）：
- 事件只在轨迹结束时落库，融合天然拿到"完整的一段轨迹"
- 不占检测/解码的 CPU，也不需要跨进程共享状态
- 重启后靠 events.cross_identity_id 的 NULL 状态自愈，不丢也不重

线程安全：单线程串行处理，匹配器与配置都是只读快照。
"""

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from src.tracking.cross_camera.config import (
    get_cross_camera_config,
    is_cross_camera_enabled,
)
from src.tracking.cross_camera.matcher import (
    CrossCameraMatcher,
    MatchResult,
    Tracklet,
    parse_event_time,
    strong_id_from_metadata,
)
from src.tracking.cross_camera import store

logger = logging.getLogger(__name__)

# 同一事件处理失败多少次后放弃（防止一条坏数据卡住整个批次）
_MAX_FAILURES = 3
# 过期身份收敛周期（秒）
_CLOSE_STALE_INTERVAL = 300


def _json_list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        data = json.loads(value)
        return [str(v) for v in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


class CrossCameraFusionService:
    """跨相机身份融合（进程内单例）"""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._socketio = None
        self._matcher = CrossCameraMatcher(get_cross_camera_config())
        self._failures: Dict[str, int] = {}
        self._last_stale_check = 0.0
        self._was_enabled = False
        self._processed_total = 0

    # ========================================
    # 生命周期
    # ========================================

    def start(self, socketio=None) -> bool:
        """启动后台线程（幂等）"""
        with self._lock:
            self._socketio = socketio
            if self._thread and self._thread.is_alive():
                return True
            if not is_cross_camera_enabled():
                logger.info("[CrossCamera] 跨相机跟踪未启用，融合服务不启动")
                return False
            try:
                store.ensure_schema()
                from src.system.system_manager import get_system_manager
                manager = get_system_manager()
                store.mark_history_excluded(manager.get_setting, manager.set_setting)
            except Exception as e:
                logger.warning(f"[CrossCamera] 初始化存储失败: {e}")

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="CrossCameraFusion", daemon=True
            )
            self._thread.start()
            self._was_enabled = True
            logger.info("[CrossCamera] 融合服务已启动")
            return True

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        logger.info("[CrossCamera] 融合服务已停止")

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ========================================
    # 主循环
    # ========================================

    def _loop(self) -> None:
        logger.info("[CrossCamera] 融合线程进入主循环")
        while not self._stop_event.is_set():
            try:
                cfg = get_cross_camera_config()
                enabled = bool(cfg.get('enabled'))
                if not enabled:
                    if self._was_enabled:
                        logger.info("[CrossCamera] 配置已关闭，暂停融合（线程保留）")
                        self._was_enabled = False
                    self._stop_event.wait(5.0)
                    continue
                if not self._was_enabled:
                    # 从关闭切回开启：补齐历史标记，避免把旧事件重新串一遍
                    try:
                        from src.system.system_manager import get_system_manager
                        manager = get_system_manager()
                        store.mark_history_excluded(manager.get_setting, manager.set_setting)
                    except Exception as e:
                        logger.debug(f"[CrossCamera] 历史标记失败: {e}")
                    self._was_enabled = True

                self._matcher.update_config(cfg)
                batch_size = int(cfg.get('batch_size') or 100)
                poll_interval = float(cfg.get('poll_interval') or 1.0)

                processed = self._process_batch(batch_size)
                self._maybe_close_stale(cfg)

                if processed < batch_size:
                    self._stop_event.wait(poll_interval)
                # 满批次说明还有积压，直接继续下一批，不等待
            except Exception as e:
                logger.warning(f"[CrossCamera] 融合循环异常: {e}")
                self._stop_event.wait(2.0)
        logger.info("[CrossCamera] 融合线程退出")

    def _maybe_close_stale(self, cfg: Dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_stale_check < _CLOSE_STALE_INTERVAL:
            return
        self._last_stale_check = now
        closed = store.close_stale_identities(float(cfg.get('identity_ttl_minutes') or 30))
        if closed:
            logger.debug(f"[CrossCamera] {closed} 个身份因长时间未出现转为 closed")

    # ========================================
    # 批处理
    # ========================================

    def _process_batch(self, batch_size: int) -> int:
        rows = store.fetch_pending_events(batch_size)
        if not rows:
            return 0

        processed = 0
        for row in rows:
            if self._stop_event.is_set():
                break
            event_id = row.get('event_id') or ''
            try:
                tracklet = self._build_tracklet(row)
                if tracklet is None:
                    # 数据不可用（缺时间等）：直接排除，避免反复占用批次
                    store.set_event_identity([event_id], store.EXCLUDED_ID)
                    processed += 1
                    continue
                self._match_and_link(tracklet)
                self._failures.pop(event_id, None)
                processed += 1
                self._processed_total += 1
            except Exception as e:
                count = self._failures.get(event_id, 0) + 1
                self._failures[event_id] = count
                logger.warning(
                    f"[CrossCamera] 事件融合失败({count}/{_MAX_FAILURES}) "
                    f"event={event_id[:8]}: {e}"
                )
                if count >= _MAX_FAILURES:
                    store.set_event_identity([event_id], store.EXCLUDED_ID)
                    self._failures.pop(event_id, None)
                    processed += 1
        return processed

    # ========================================
    # 单条事件融合
    # ========================================

    def _build_tracklet(self, row: Dict[str, Any]) -> Optional[Tracklet]:
        start_time = parse_event_time(row.get('start_time'))
        end_time = parse_event_time(row.get('end_time')) or start_time
        if start_time <= 0:
            return None

        metadata = row.get('metadata')
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        zones = _json_list(row.get('entered_zones'))
        zone_name = (row.get('zone_name') or '').strip()
        if zone_name:
            zones.extend([z.strip() for z in zone_name.split(',') if z.strip()])

        return Tracklet(
            event_id=row.get('event_id') or '',
            camera_id=str(row.get('camera_id') or ''),
            class_name=(row.get('class_name') or '').strip(),
            start_time=start_time,
            end_time=end_time,
            feature=row.get('cross_feature'),
            strong_id=strong_id_from_metadata(metadata),
            image_url=row.get('local_image_url') or '',
            confidence=float(row.get('confidence') or 0.0),
            task_id=row.get('task_id'),
            zones=zones,
        )

    def _anchor_of(self, identity: Dict[str, Any]) -> Tracklet:
        """身份最近一次轨迹片段（作为下一轮匹配的锚点）"""
        return Tracklet(
            event_id=identity.get('anchor_event_id') or '',
            camera_id=str(identity.get('anchor_camera_id') or ''),
            class_name=identity.get('class_name') or '',
            start_time=float(identity.get('anchor_start') or 0.0),
            end_time=float(identity.get('anchor_end') or 0.0),
            feature=identity.get('anchor_feature') or None,
            strong_id=identity.get('strong_id') or None,
        )

    def _match_and_link(self, tracklet: Tracklet) -> None:
        cfg = self._matcher.cfg
        family = self._matcher.family_of(tracklet.class_name)
        threshold = self._matcher.threshold_of(family)
        max_gap = float(threshold.get('max_gap_seconds') or cfg.get('max_gap_seconds') or 120)
        max_cameras = int(cfg.get('max_cameras_per_identity') or 32)

        # 候选身份：同类别、最近活跃窗口内（窗口放宽到时间窗的 2 倍 + 1 分钟）
        since = tracklet.start_time - max_gap * 2 - 60
        exclude_camera = '' if self._matcher.link_same_camera else str(tracklet.camera_id)
        identities = store.fetch_candidate_identities(
            tracklet.class_name, since, limit=60, exclude_camera=exclude_camera)

        scored: List[Tuple[Dict[str, Any], MatchResult]] = []
        for identity in identities:
            cameras = _json_list(identity.get('cameras'))
            if (str(tracklet.camera_id) not in cameras
                    and len(cameras) >= max_cameras):
                continue
            anchor = self._anchor_of(identity)
            try:
                result = self._matcher.evaluate(anchor, tracklet)
            except Exception as e:
                logger.debug(f"[CrossCamera] 候选评估异常: {e}")
                continue
            if result is not None:
                scored.append((identity, result))

        scored.sort(key=lambda item: item[1].score, reverse=True)
        best_identity, best_result = (scored[0] if scored else (None, None))
        second_result = scored[1][1] if len(scored) > 1 else None

        min_margin = float(threshold.get('min_margin') or 0.08)
        strong_hit = bool(best_result and best_result.components.get('strong_id'))
        if (best_result is not None and second_result is not None and not strong_hit
                and (best_result.score - second_result.score) < min_margin):
            logger.debug(
                f"[CrossCamera] 候选过于接近，改为新建身份 event={tracklet.event_id[:8]} "
                f"best={best_result.score:.3f} second={second_result.score:.3f}"
            )
            best_identity, best_result = None, None

        if best_identity is not None and best_result is not None:
            ok = store.attach_to_identity(
                best_identity['identity_id'], tracklet,
                best_result.score, best_result.reason,
            )
            if ok:
                store.set_event_identity([tracklet.event_id], best_identity['identity_id'])
                logger.info(
                    f"[CrossCamera] 关联到已有身份 {best_identity['identity_id'][:8]} "
                    f"event={tracklet.event_id[:8]} cam={tracklet.camera_id[:8]} "
                    f"class={tracklet.class_name} score={best_result.score:.3f} "
                    f"({best_result.reason})"
                )
                self._emit(best_identity['identity_id'], tracklet.event_id, created=False)
                return
            # 关联失败（并发/写库异常）走新建，保证事件不丢

        identity_id = store.create_identity(tracklet, family)
        if identity_id:
            store.set_event_identity([tracklet.event_id], identity_id)
            logger.info(
                f"[CrossCamera] 新建身份 {identity_id[:8]} "
                f"event={tracklet.event_id[:8]} cam={tracklet.camera_id[:8]} "
                f"class={tracklet.class_name}"
            )
            self._emit(identity_id, tracklet.event_id, created=True)

    def _emit(self, identity_id: str, event_id: str, created: bool) -> None:
        """推送前端刷新（失败不影响融合）"""
        if self._socketio is None:
            return
        try:
            self._socketio.emit('cross_camera_update', {
                'identity_id': identity_id,
                'event_id': event_id,
                'created': created,
            })
        except Exception as e:
            logger.debug(f"[CrossCamera] 推送失败: {e}")


# ========================================
# 进程内单例
# ========================================

_service: Optional[CrossCameraFusionService] = None
_service_lock = threading.Lock()


def get_cross_camera_service() -> CrossCameraFusionService:
    global _service
    with _service_lock:
        if _service is None:
            _service = CrossCameraFusionService()
        return _service


def start_cross_camera_service(socketio=None) -> bool:
    """启动融合服务（供 run.py 调用）"""
    return get_cross_camera_service().start(socketio)


def stop_cross_camera_service() -> None:
    global _service
    with _service_lock:
        service = _service
    if service is not None:
        service.stop()
