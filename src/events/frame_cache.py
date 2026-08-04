"""
帧缓存模块

为 GIF 生成提供基于时间窗口的帧回放能力。
每个相机维护一个独立的帧缓存，持续按 cache_fps 采样，
保留 cache_retention 秒的历史帧。

核心流程：
1. 持续写入：每帧处理时按 cache_fps 采样写入缓存（JPEG 压缩字节）
2. 时间窗口回取：事件结束时，按 [start-pre, end+post] 窗口取出帧（解码为 numpy）
3. 自动淘汰：超过 cache_retention 的旧帧自动丢弃

✅ 优化：缓存 JPEG 压缩字节而非原始 numpy 数组
- 原始：320x240x3 ≈ 230KB/帧
- JPEG：~8KB/帧（quality=70）
- 内存节省 ~28 倍

✅ 线程安全：push / get_frames / clear_before 内部加锁，
   支持 TrackedObjectProcessor 的 frame_cache_executor 异步写入

配置来源：event_output.yaml 的 gif 段
- cache_fps: 缓存采样率（帧/秒）
- cache_retention: 缓存保留秒数
- pre_seconds: 事件起始前回取秒数
- post_seconds: 事件结束后回取秒数
"""

import threading
import time
import logging
from collections import deque
from typing import Optional, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# JPEG 压缩质量（降低内存占用，70 足够生成 GIF）
_JPEG_QUALITY = 70


class FrameCache:
    """
    滚动帧缓存

    按固定采样率持续缓存帧（JPEG 压缩字节），支持按时间窗口回取。
    使用 deque 自动淘汰过期帧，内存占用可控。
    线程安全：内部加锁，支持异步写入场景。
    """

    def __init__(self, camera_id: str, cache_fps: int = 10,
                 cache_retention: float = 20.0):
        """
        Args:
            camera_id: 摄像机 ID
            cache_fps: 缓存采样率（帧/秒），0 表示不缓存
            cache_retention: 缓存保留秒数，超过此时间的帧自动淘汰
        """
        self.camera_id = camera_id
        self.cache_fps = max(1, cache_fps)
        self.cache_retention = cache_retention

        # 帧缓存：[(frame_time, jpeg_bytes), ...]  ← ✅ 存 JPEG 字节而非 numpy
        max_len = int(self.cache_fps * self.cache_retention) + 10
        self._frames: deque = deque(maxlen=max_len)

        # 采样控制：距离上次采样的最小时间间隔
        self._sample_interval = 1.0 / self.cache_fps
        self._last_sample_time = 0.0

        # 统计
        self._total_pushed = 0
        self._total_sampled = 0

        # 线程锁：保护 _last_sample_time、_frames、统计计数
        # push（frame_cache_executor 线程池异步调用）和 get_frames（GIF 生成）
        # 会并发访问，必须加锁
        self._lock = threading.Lock()

    def push(self, frame_time: float, image: np.ndarray) -> bool:
        """
        尝试将帧写入缓存（按 cache_fps 采样，JPEG 压缩存储）

        线程安全：内部加锁，可从 frame_cache_executor 线程池异步调用。

        Args:
            frame_time: 帧时间戳
            image: 帧图像（RGB 格式，来自共享内存 YUV→RGB 转换）

        Returns:
            True 表示帧实际写入缓存（采样通过+编码成功），False 表示被采样过滤或编码失败

        说明:
            输入为 RGB 帧，使用 PIL 编码/解码，全程 RGB 无需 BGR 转换。
        """
        with self._lock:
            self._total_pushed += 1

            # 采样控制：仅当距上次采样超过采样间隔时才写入
            if frame_time - self._last_sample_time < self._sample_interval:
                return False

            self._last_sample_time = frame_time
            self._total_sampled += 1

        # JPEG 编码在锁外执行（耗时 ~2-5ms，不阻塞其他 push/get_frames）
        try:
            from src.utils.image import encode_jpeg
            jpeg_bytes = encode_jpeg(image, quality=_JPEG_QUALITY)
            if not jpeg_bytes:
                logger.warning(f"[FrameCache] JPEG 编码失败: camera_id={self.camera_id}")
                return False
        except Exception as e:
            logger.warning(f"[FrameCache] 压缩帧失败: {e}")
            return False

        # append 回锁内（保护 _frames）
        with self._lock:
            self._frames.append((frame_time, jpeg_bytes))
        return True

    def get_frames(self, start_time: float, end_time: float,
                   max_frames: int = 60) -> List[Tuple[float, np.ndarray]]:
        """
        按时间窗口回取帧（JPEG 解码为 numpy）

        线程安全：与 push 并发访问时加锁保护 _frames 读取快照，
        JPEG 解码在锁外执行（耗时操作不持锁）。

        Args:
            start_time: 起始时间（含 pre_seconds 偏移）
            end_time: 结束时间（含 post_seconds 偏移）
            max_frames: 最大返回帧数

        Returns:
            [(frame_time, image), ...] 按时间升序排列，image 为 RGB numpy 数组
        """
        # 锁内：快照 _frames 的引用并做时间窗口筛选（deque 不可切片，需遍历）
        with self._lock:
            if not self._frames:
                return []

            # 筛选时间窗口内的帧（只取 jpeg_bytes）
            selected = []
            last_item = None
            for ft, jpg in self._frames:
                if ft < start_time:
                    continue
                if ft > end_time:
                    break
                selected.append((ft, jpg))
                last_item = (ft, jpg)

            # 限制最大帧数（均匀采样）
            if len(selected) > max_frames:
                step = max(1, len(selected) // max_frames)
                selected = selected[::step]
                # 确保最后一帧在内
                if selected[-1] != last_item:
                    selected.append(last_item)
                selected = selected[:max_frames]

        # 锁外：解码 JPEG 为 numpy 数组（耗时操作，不阻塞 push）
        from src.utils.image import decode_jpeg
        result = []
        for ft, jpg in selected:
            rgb_img = decode_jpeg(jpg)
            if rgb_img is not None:
                result.append((ft, rgb_img))

        return result

    def clear_before(self, time_threshold: float):
        """
        清除指定时间之前的缓存帧

        Args:
            time_threshold: 时间阈值，早于此时间的帧将被清除
        """
        with self._lock:
            while self._frames and self._frames[0][0] < time_threshold:
                self._frames.popleft()

    @property
    def size(self) -> int:
        """当前缓存帧数"""
        with self._lock:
            return len(self._frames)

    @property
    def time_range(self) -> Tuple[float, float]:
        """当前缓存的时间范围 (earliest, latest)"""
        with self._lock:
            if not self._frames:
                return (0.0, 0.0)
            return (self._frames[0][0], self._frames[-1][0])

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        return {
            'camera_id': self.camera_id,
            'cache_fps': self.cache_fps,
            'cache_retention': self.cache_retention,
            'current_size': self.size,
            'max_size': self._frames.maxlen,
            'time_range': self.time_range,
            'total_pushed': self._total_pushed,
            'total_sampled': self._total_sampled,
            'sample_rate': self._total_sampled / max(1, self._total_pushed),
        }
