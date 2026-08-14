"""
GIF 动画生成器

负责生成跟踪过程的 GIF 动画，记录目标从出现到消失的完整轨迹。
支持上传到 MinIO/FTP 存储服务器，支持本地文件自动清理。
"""

import os
import time
import cv2
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class GIFGenerator:
    """GIF 动画生成器"""
    
    def __init__(self, config: Dict = None):
        """
        初始化 GIF 生成器
        
        Args:
            config: GIF 配置字典
        """
        self.config = config or {}
        
        # GIF 功能开关（总开关）
        self.enabled = self.config.get('enabled', True)
        if not self.enabled:
            logger.debug(f"[GIFGenerator] GIF 功能已禁用")
            return
        
        # GIF 参数
        self.max_frames = self.config.get('max_frames', 30)  # 最大帧数
        self.fps = self.config.get('fps', 5)  # GIF 播放帧率
        self.duration = int(1000 / self.fps)  # 每帧持续时间（毫秒）
        
        # 图片尺寸
        self.max_width = self.config.get('max_width', 320)
        self.max_height = self.config.get('max_height', 240)
        # 裁剪边距比例：与采集端保持一致，用于完整帧在GIF中裁剪目标区域
        self.crop_margin_ratio = self.config.get('crop_margin_ratio', 1.0)
        
        # 存储方式独立开关（向后兼容旧版 output_method 字段）
        # 优先读取独立开关；若未提供则从旧版 output_method 转换
        if self.config.get('local_enabled') is not None:
            self.local_enabled = bool(self.config.get('local_enabled'))
            self.minio_enabled = bool(self.config.get('minio_enabled', False))
            self.ftp_enabled = bool(self.config.get('ftp_enabled', False))
        else:
            legacy_method = self.config.get('output_method', 'local')
            method_mapping = {
                'both':  (True,  True,  True),
                'minio': (False, True,  False),
                'ftp':   (False, False, True),
                'local': (True,  False, False),
                'none':  (False, False, False),
            }
            self.local_enabled, self.minio_enabled, self.ftp_enabled = method_mapping.get(
                legacy_method, (True, False, False)
            )
            logger.debug(f"[GIFGenerator] 旧版 output_method='{legacy_method}' 已自动迁移为独立开关")

        # 本地输出目录
        self.output_dir = self.config.get('output_dir', 'data/gifs')
        os.makedirs(self.output_dir, exist_ok=True)

        # 本地保存策略配置
        self.local_retention_days = self.config.get('local_retention_days', 7)  # 本地保存天数
        self.auto_cleanup = self.config.get('auto_cleanup', True)  # 是否自动清理
        self.cleanup_interval_hours = self.config.get('cleanup_interval_hours', 24)  # 清理间隔（小时）

        # 上次清理时间
        self.last_cleanup_time = 0

        # 是否绘制轨迹
        self.draw_trajectory = self.config.get('draw_trajectory', True)
        # 裁剪模式：'full' = 全场景帧（不裁剪，bbox直接画在完整画面上）；'target' = 裁剪目标区域
        self.crop_mode = self.config.get('crop_mode', 'full')
        # 是否绘制检测框（受前端开关控制，False时只保留轨迹/场景不画bbox）
        self.draw_bbox = self.config.get('draw_bbox', True)

        # 显示配置（与 AI 视频流/事件图片共用同一颜色来源）
        # display_config 中颜色为 RGB 格式，GIF 帧也是 RGB（FrameCache 输出），直接使用
        dc = self.config.get('display_config', {}) or {}
        det_box_cfg = dc.get('detection_box', {}) or {}
        traj_cfg = dc.get('trajectory', {}) or {}
        self.bbox_color = tuple(det_box_cfg.get('color', [0, 255, 0]))        # 检测框颜色
        self.bbox_thickness = det_box_cfg.get('thickness', 2)                  # 检测框线宽
        self.trajectory_color = tuple(traj_cfg.get('color', [255, 255, 0]))    # 轨迹线颜色
        self.trajectory_point_color = tuple(traj_cfg.get('point_color', [255, 0, 0]))  # 轨迹点颜色
        self.trajectory_thickness = traj_cfg.get('thickness', 1)               # 轨迹线宽
        self.trajectory_point_size = traj_cfg.get('point_size', 2)             # 轨迹点大小

        # 初始化 MinIO 输出器（任务开关 AND 系统全局开关）
        self.minio_outputter = None
        if self.minio_enabled:
            self._init_minio_outputter()

        # 初始化 FTP 输出器（任务开关 AND 系统全局开关）
        self.ftp_outputter = None
        if self.ftp_enabled:
            self._init_ftp_outputter()

        # 启动时清理一次
        if self.auto_cleanup:
            self._cleanup_old_files()

        logger.debug(f"[GIFGenerator] 初始化完成")
        logger.debug(f"[GIFGenerator]   功能状态: 已启用")
        logger.debug(f"[GIFGenerator]   最大帧数: {self.max_frames}")
        logger.debug(f"[GIFGenerator]   帧率: {self.fps} FPS")
        logger.debug(f"[GIFGenerator]   存储开关: local={self.local_enabled}, minio={self.minio_enabled}, ftp={self.ftp_enabled}")
        if self.local_enabled or self.auto_cleanup:
            logger.debug(f"[GIFGenerator]   输出目录: {self.output_dir}")
            logger.debug(f"[GIFGenerator]   本地保存天数: {self.local_retention_days}")
            logger.debug(f"[GIFGenerator]   自动清理: {self.auto_cleanup}")
    
    def _init_minio_outputter(self):
        """初始化 MinIO 输出器"""
        try:
            # 获取全局存储配置
            storage_config = self.config.get('storage', {})
            global_minio_config = storage_config.get('minio', {})

            # 检查是否启用 MinIO
            if not global_minio_config.get('enabled', False):
                logger.debug(f"[GIFGenerator] MinIO 已禁用，跳过初始化")
                return

            from .minio_outputter import MinIOOutputter

            # 获取 GIF 特定配置
            minio_bucket = self.config.get('minio_bucket', 'sagesight-gifs')
            
            # 合并配置：全局配置 + GIF 特定配置
            minio_config = {}
            
            # 如果有全局配置，使用全局配置
            if global_minio_config:
                minio_config = {
                    'enabled': global_minio_config.get('enabled', True),
                    'endpoint': global_minio_config.get('endpoint', 'localhost:9000'),
                    'access_key': global_minio_config.get('access_key', 'minioadmin'),
                    'secret_key': global_minio_config.get('secret_key', 'minioadmin'),
                    'secure': global_minio_config.get('secure', False),
                    'presigned_expires': global_minio_config.get('presigned_expires', 3600),
                    'bucket': minio_bucket  # 使用 GIF 特定的 bucket
                }
            else:
                # 向后兼容：使用单独的 minio 配置
                old_minio_config = self.config.get('minio', {})
                minio_config = {
                    'enabled': old_minio_config.get('enabled', True),
                    'endpoint': old_minio_config.get('endpoint', 'localhost:9000'),
                    'access_key': old_minio_config.get('access_key', 'minioadmin'),
                    'secret_key': old_minio_config.get('secret_key', 'minioadmin'),
                    'secure': old_minio_config.get('secure', False),
                    'bucket': old_minio_config.get('bucket', minio_bucket)
                }
            
            self.minio_outputter = MinIOOutputter(minio_config)
            
            logger.debug(f"[GIFGenerator] MinIO 输出器已初始化")
            logger.debug(f"[GIFGenerator]   Bucket: {minio_config.get('bucket')}")
            
        except Exception as e:
            logger.debug(f"[GIFGenerator] MinIO 输出器初始化失败: {e}")
    
    def _init_ftp_outputter(self):
        """初始化 FTP 输出器"""
        try:
            # 获取全局存储配置
            storage_config = self.config.get('storage', {})
            global_ftp_config = storage_config.get('ftp', {})

            # 检查是否启用 FTP
            if not global_ftp_config.get('enabled', False):
                logger.debug(f"[GIFGenerator] FTP 已禁用，跳过初始化")
                return

            from .ftp_outputter import FTPOutputter
            
            # 获取 GIF 特定配置
            ftp_remote_dir = self.config.get('ftp_remote_dir', '/gifs')
            
            # 合并配置：全局配置 + GIF 特定配置
            ftp_config = {}
            
            # 如果有全局配置，使用全局配置
            if global_ftp_config:
                ftp_config = {
                    'enabled': global_ftp_config.get('enabled', True),
                    'host': global_ftp_config.get('host', 'localhost'),
                    'port': global_ftp_config.get('port', 21),
                    'username': global_ftp_config.get('username', 'ftpuser'),
                    'password': global_ftp_config.get('password', 'ftppassword'),
                    'passive_mode': global_ftp_config.get('passive_mode', True),
                    'remote_dir': ftp_remote_dir  # 使用 GIF 特定的 remote_dir
                }
            else:
                # 向后兼容：使用单独的 ftp 配置
                old_ftp_config = self.config.get('ftp', {})
                ftp_config = {
                    'enabled': old_ftp_config.get('enabled', True),
                    'host': old_ftp_config.get('host', 'localhost'),
                    'port': old_ftp_config.get('port', 21),
                    'username': old_ftp_config.get('username', 'ftpuser'),
                    'password': old_ftp_config.get('password', 'ftppassword'),
                    'passive_mode': old_ftp_config.get('passive_mode', True),
                    'remote_dir': old_ftp_config.get('remote_dir', ftp_remote_dir)
                }
            
            self.ftp_outputter = FTPOutputter(ftp_config)
            
            logger.debug(f"[GIFGenerator] FTP 输出器已初始化")
            logger.debug(f"[GIFGenerator]   Remote Dir: {ftp_config.get('remote_dir')}")
            
        except Exception as e:
            logger.debug(f"[GIFGenerator] FTP 输出器初始化失败: {e}")
    
    def generate(self, track_id: int, frames: List[Dict], 
                output_filename: Optional[str] = None) -> Dict[str, str]:
        """
        生成 GIF 动画
        
        Args:
            track_id: 跟踪 ID
            frames: 帧序列列表，每个元素包含：
                    - 'image': 图片数据（完整帧或裁剪帧）
                    - 'bbox': 边界框 [x1, y1, x2, y2]
                    - 'frame_id': 帧 ID
                    - 'timestamp': 时间戳
                    - 'is_full_frame': 是否为完整帧
            output_filename: 输出文件名（可选）
        
        Returns:
            GIF 路径字典 {'local': path, 'minio': url, 'ftp': url}
        """
        if not frames:
            logger.debug(f"[GIFGenerator] 警告：帧序列为空，无法生成 GIF")
            return {}
        
        # 限制帧数
        if len(frames) > self.max_frames:
            # 采样：保留首帧、尾帧和中间均匀采样
            frames = self._sample_frames(frames, self.max_frames)
        
        # 生成输出文件名
        if not output_filename:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_filename = f"track_{track_id}_{timestamp}.gif"
        
        # 处理帧序列
        processed_frames = []
        trajectory_points = []  # 轨迹点
        
        for i, frame_data in enumerate(frames):
            image = frame_data.get('image')
            bbox = frame_data.get('bbox', [0, 0, 0, 0])
            is_full_frame = frame_data.get('is_full_frame', False)
            
            if image is None:
                continue
            
            # bbox 是否有效（非零面积）
            has_bbox = (bbox[2] - bbox[0]) > 0 and (bbox[3] - bbox[1]) > 0
            center_x = int((bbox[0] + bbox[2]) / 2)
            center_y = int((bbox[1] + bbox[3]) / 2)
            
            if self.crop_mode == 'full':
                # 全场景模式：不裁剪，直接使用完整帧
                cropped = image
                h, w = cropped.shape[:2]
                # 仅在有bbox时记录轨迹点（事件前/后的帧无轨迹）
                if has_bbox:
                    trajectory_points.append((center_x, center_y))
                crop_offset = (0, 0, w, h)
            elif is_full_frame:
                # 完整帧裁剪目标区域（保留上下文）：按配置比例扩展
                h, w = image.shape[:2]
                margin_x = int((bbox[2] - bbox[0]) * self.crop_margin_ratio)
                margin_y = int((bbox[3] - bbox[1]) * self.crop_margin_ratio)
                
                crop_x1 = max(0, int(bbox[0] - margin_x))
                crop_y1 = max(0, int(bbox[1] - margin_y))
                crop_x2 = min(w, int(bbox[2] + margin_x))
                crop_y2 = min(h, int(bbox[3] + margin_y))
                
                cropped = image[crop_y1:crop_y2, crop_x1:crop_x2]
                crop_w = crop_x2 - crop_x1
                crop_h = crop_y2 - crop_y1
                
                # 调整轨迹点坐标（相对于裁剪区域）
                trajectory_points.append((center_x - crop_x1, center_y - crop_y1))
                
                # 记录裁剪偏移，供后续绘制真实边界框使用
                crop_offset = (crop_x1, crop_y1, crop_w, crop_h)
            else:
                cropped = image
                # 裁剪帧的中心点（相对于裁剪区域）
                h, w = cropped.shape[:2]
                trajectory_points.append((w // 2, h // 2))
                crop_offset = None
            
            # 缩放图片
            original_crop_h, original_crop_w = cropped.shape[:2]
            cropped = self._resize_frame(cropped)
            scale_x = cropped.shape[1] / original_crop_w if original_crop_w > 0 else 1
            scale_y = cropped.shape[0] / original_crop_h if original_crop_h > 0 else 1
            
            # 调整已有轨迹点坐标（缩放到统一尺寸）
            if len(trajectory_points) > 0:
                trajectory_points[-1] = (
                    int(trajectory_points[-1][0] * scale_x),
                    int(trajectory_points[-1][1] * scale_y)
                )
            
            # 绘制轨迹（如果启用）
            if self.draw_trajectory and len(trajectory_points) > 1:
                cropped = self._draw_trajectory(cropped, trajectory_points)
            
            # 绘制真实边界框（受 draw_bbox 开关控制）
            if self.draw_bbox and self.crop_mode == 'full':
                # 全场景模式：bbox已在缓存帧坐标系（与缩放后一致，因缓存帧即为目标尺寸）
                if has_bbox:
                    adj_x1 = bbox[0] * scale_x
                    adj_y1 = bbox[1] * scale_y
                    adj_x2 = bbox[2] * scale_x
                    adj_y2 = bbox[3] * scale_y
                    cv2.rectangle(cropped,
                                 (int(adj_x1), int(adj_y1)),
                                 (int(adj_x2), int(adj_y2)),
                                 self.bbox_color, self.bbox_thickness)
                    # 中心十字标记
                    cx = int((adj_x1 + adj_x2) / 2)
                    cy = int((adj_y1 + adj_y2) / 2)
                    cv2.line(cropped, (cx - 5, cy), (cx + 5, cy), (0, 0, 255), 1)
                    cv2.line(cropped, (cx, cy - 5), (cx, cy + 5), (0, 0, 255), 1)
            elif self.draw_bbox and is_full_frame and crop_offset:
                # 目标裁剪模式：经过裁剪偏移和缩放
                adj_x1 = (bbox[0] - crop_offset[0]) * scale_x
                adj_y1 = (bbox[1] - crop_offset[1]) * scale_y
                adj_x2 = (bbox[2] - crop_offset[0]) * scale_x
                adj_y2 = (bbox[3] - crop_offset[1]) * scale_y
                cv2.rectangle(cropped,
                             (int(adj_x1), int(adj_y1)),
                             (int(adj_x2), int(adj_y2)),
                             self.bbox_color, self.bbox_thickness)
                # 绘制中心十字标记
                cx = int((adj_x1 + adj_x2) / 2)
                cy = int((adj_y1 + adj_y2) / 2)
                cv2.line(cropped, (cx - 5, cy), (cx + 5, cy), (0, 0, 255), 1)
                cv2.line(cropped, (cx, cy - 5), (cx, cy + 5), (0, 0, 255), 1)
            
            processed_frames.append(cropped)
        
        if not processed_frames:
            logger.debug(f"[GIFGenerator] 警告：处理后帧序列为空")
            return {}
        
        # 根据帧时间戳计算实际时长（毫秒/帧）
        actual_duration = self._calculate_duration_from_timestamps(frames)
        
        # 生成 GIF 字节数据
        try:
            gif_bytes = self._generate_gif_bytes(processed_frames, actual_duration)
            
            if gif_bytes is None:
                logger.debug(f"[GIFGenerator] GIF 生成失败")
                return {}
            
            logger.debug(f"[GIFGenerator] GIF 已生成")
            logger.debug(f"[GIFGenerator]   帧数: {len(processed_frames)}")
            logger.debug(f"[GIFGenerator]   尺寸: {processed_frames[0].shape[1]}x{processed_frames[0].shape[0]}")
            logger.debug(f"[GIFGenerator]   大小: {len(gif_bytes) / 1024:.1f} KB")
            logger.debug(f"[GIFGenerator]   每帧时长: {actual_duration}ms")
            
            # 上传 GIF
            gif_urls = self._upload_gif(output_filename, gif_bytes)
            
            # 返回路径字典（包含 local/minio/ftp 三种路径）
            return gif_urls
            
        except Exception as e:
            logger.debug(f"[GIFGenerator] GIF 生成失败: {e}")
            return {}
    
    def _upload_gif(self, filename: str, gif_bytes: bytes) -> Dict[str, str]:
        """
        上传 GIF 到 MinIO/FTP
        
        Args:
            filename: 文件名
            gif_bytes: GIF 字节数据
        
        Returns:
            URL 字典 {'minio': url, 'ftp': url, 'local': path}
        """
        urls = {}
        
        # 上传到 MinIO
        if self.minio_outputter:
            try:
                # 修改文件名扩展名为 .gif
                gif_filename = filename.replace('.jpg', '.gif') if filename.endswith('.jpg') else filename
                url = self.minio_outputter.upload(gif_filename, gif_bytes)
                urls['minio'] = url
                logger.debug(f"[GIFGenerator] GIF 已上传到 MinIO: {gif_filename}")
            except Exception as e:
                logger.debug(f"[GIFGenerator] MinIO 上传失败: {e}")
        
        # 上传到 FTP
        if self.ftp_outputter:
            try:
                gif_filename = filename.replace('.jpg', '.gif') if filename.endswith('.jpg') else filename
                url = self.ftp_outputter.upload(gif_filename, gif_bytes)
                urls['ftp'] = url
                logger.debug(f"[GIFGenerator] GIF 已上传到 FTP: {gif_filename}")
            except Exception as e:
                logger.debug(f"[GIFGenerator] FTP 上传失败: {e}")
        
        # 保存到本地（开关启用，或远程上传失败时作为备份）
        if self.local_enabled or not urls:
            # 磁盘空间保护：写入前确保有足够空间，不足时滚动删除旧 GIF
            from src.utils.disk_guard import ensure_disk_space
            ensure_disk_space(self.output_dir, len(gif_bytes))

            local_path = os.path.join(self.output_dir, filename)
            with open(local_path, 'wb') as f:
                f.write(gif_bytes)
            urls['local'] = local_path
            logger.debug(f"[GIFGenerator] GIF 已保存到本地: {local_path}")
        
        return urls
    
    def _sample_frames(self, frames: List[Dict], max_frames: int) -> List[Dict]:
        """
        采样帧序列
        
        Args:
            frames: 原始帧序列
            max_frames: 最大帧数
        
        Returns:
            采样后的帧序列
        """
        if len(frames) <= max_frames:
            return frames
        
        # 保留首帧和尾帧
        sampled = [frames[0], frames[-1]]
        
        # 中间均匀采样
        remaining = max_frames - 2
        step = (len(frames) - 2) / remaining
        
        for i in range(1, remaining + 1):
            index = int(1 + i * step)
            sampled.insert(i, frames[index])
        
        return sampled
    
    def _resize_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        缩放帧到固定尺寸
        
        Args:
            frame: 输入帧
        
        Returns:
            缩放后的帧（固定尺寸）
        """
        # 统一所有帧到固定尺寸，避免 imageio 报错
        frame = cv2.resize(frame, (self.max_width, self.max_height))
        
        return frame
    
    def _draw_trajectory(self, frame: np.ndarray, points: List[tuple]) -> np.ndarray:
        """
        绘制轨迹
        
        Args:
            frame: 输入帧
            points: 轨迹点列表
        
        Returns:
            绘制轨迹后的帧
        """
        if len(points) < 2:
            return frame

        # 绘制轨迹线（渐变效果：从浅到深，增强方向感）
        for i in range(1, len(points)):
            pt1 = points[i - 1]
            pt2 = points[i]
            alpha = i / len(points)
            color = tuple(int(c * alpha) for c in self.trajectory_color)
            cv2.line(frame, pt1, pt2, color, self.trajectory_thickness)

        # 绘制当前位置点
        cv2.circle(frame, points[-1], self.trajectory_point_size + 3,
                   self.trajectory_point_color, -1)

        return frame
    
    def _calculate_duration_from_timestamps(self, frames: List[Dict]) -> int:
        """
        根据帧时间戳计算每帧时长（毫秒）
        
        Args:
            frames: 帧序列（包含 timestamp 字段）
        
        Returns:
            每帧时长（毫秒）
        """
        try:
            if len(frames) < 2:
                return self.duration  # 默认时长
            
            # 解析首尾帧时间戳
            first_ts_str = frames[0].get('timestamp')
            last_ts_str = frames[-1].get('timestamp')
            
            if not first_ts_str or not last_ts_str:
                return self.duration
            
            first_ts = datetime.fromisoformat(first_ts_str)
            last_ts = datetime.fromisoformat(last_ts_str)
            
            time_span = (last_ts - first_ts).total_seconds()
            if time_span <= 0:
                return self.duration
            
            # 计算每帧时长：总时长 / 帧数
            duration_per_frame = int(time_span * 1000 / len(frames))
            
            # 限制在合理范围：50ms - 500ms（2-20fps）
            duration_per_frame = max(50, min(500, duration_per_frame))
            
            return duration_per_frame
            
        except Exception as e:
            logger.debug(f"[GIFGenerator] 时间跨度计算失败，使用默认时长: {e}")
            return self.duration
    
    def _generate_gif_bytes(self, frames: List[np.ndarray], duration: int = None) -> Optional[bytes]:
        """
        生成 GIF 字节数据（PIL 调色板优化编码）

        优化点：
        - 用 PIL.Image.quantize 生成共用 256 色调色板
        - 所有帧共用同一调色板，文件大幅缩小（50-70%）
        - optimize=True 进一步压缩

        Args:
            frames: 帧序列（RGB numpy 数组，来自 FrameCache）
            duration: 每帧时长（毫秒），None 则使用默认值

        Returns:
            GIF 字节数据
        """
        try:
            from PIL import Image
            import io as _io

            if not frames:
                return None

            # 帧已是 RGB 格式，直接转 PIL Image
            pil_images = [Image.fromarray(f) for f in frames]

            # ✅ 共用调色板量化（256色）：所有帧共用同一调色板 → 文件大幅缩小
            first = pil_images[0].convert('P', palette=Image.ADAPTIVE, colors=256)
            optimized = [first]
            for img in pil_images[1:]:
                # 用第一帧的调色板量化后续帧，保持调色板一致
                optimized.append(img.convert('P', palette=first.palette))

            actual_duration = duration if duration is not None else self.duration

            # 生成 GIF 字节数据
            buf = _io.BytesIO()
            optimized[0].save(
                buf, format='GIF', save_all=True, append_images=optimized[1:],
                duration=actual_duration, loop=0, disposal=2, optimize=True
            )
            return buf.getvalue()

        except ImportError:
            logger.debug("[GIFGenerator] PIL 未安装，无法生成 GIF")
            return None
        except Exception as e:
            logger.debug(f"[GIFGenerator] GIF 生成错误: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _cleanup_old_files(self):
        """
        清理过期的本地文件
        
        根据 local_retention_days 配置，删除超过保留天数的文件。
        采用分批删除 + 批间休眠，避免一次性删除大量文件时阻塞事件处理进程。
        """
        if not os.path.exists(self.output_dir):
            return
        
        # 检查是否需要清理（根据清理间隔）
        current_time = time.time()
        cleanup_interval_seconds = self.cleanup_interval_hours * 3600
        
        if current_time - self.last_cleanup_time < cleanup_interval_seconds:
            return
        
        # 计算过期时间
        retention_seconds = self.local_retention_days * 24 * 3600
        
        # 分批清理参数
        batch_size = 200
        batch_sleep_seconds = 0.05
        
        # 1. 先扫描出过期文件列表（listdir + stat）
        expired_files = []
        try:
            for filename in os.listdir(self.output_dir):
                filepath = os.path.join(self.output_dir, filename)
                if not os.path.isfile(filepath):
                    continue
                file_mtime = os.path.getmtime(filepath)
                if current_time - file_mtime > retention_seconds:
                    expired_files.append(filepath)
        except Exception as e:
            logger.debug(f"[GIFGenerator] 扫描过期文件失败: {e}")
            return
        
        if not expired_files:
            self.last_cleanup_time = current_time
            return
        
        # 2. 分批删除，批间休眠让出文件系统/CPU
        deleted_count = 0
        total_size = 0
        try:
            for i in range(0, len(expired_files), batch_size):
                batch = expired_files[i:i + batch_size]
                for filepath in batch:
                    try:
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        deleted_count += 1
                        total_size += file_size
                    except OSError:
                        # 文件可能已被其他清理逻辑删除，跳过
                        pass
                time.sleep(batch_sleep_seconds)
        except Exception as e:
            logger.debug(f"[GIFGenerator] 清理文件失败: {e}")
        
        # 更新清理时间
        self.last_cleanup_time = current_time
        
        if deleted_count > 0:
            logger.debug(f"[GIFGenerator] 已清理 {deleted_count} 个过期文件")
            logger.debug(f"[GIFGenerator]   释放空间: {total_size / 1024 / 1024:.2f} MB")
            logger.debug(f"[GIFGenerator]   保留天数: {self.local_retention_days}")
    
    def close(self):
        """关闭 GIF 生成器"""
        # 最后一次清理
        if self.auto_cleanup:
            self._cleanup_old_files()
        
        logger.debug("[GIFGenerator] 已关闭")