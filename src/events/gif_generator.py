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


class GIFGenerator:
    """GIF 动画生成器"""
    
    def __init__(self, config: Dict = None):
        """
        初始化 GIF 生成器
        
        Args:
            config: GIF 配置字典
        """
        self.config = config or {}
        
        # GIF 参数
        self.max_frames = self.config.get('max_frames', 30)  # 最大帧数
        self.fps = self.config.get('fps', 5)  # GIF 播放帧率
        self.duration = int(1000 / self.fps)  # 每帧持续时间（毫秒）
        
        # 图片尺寸
        self.max_width = self.config.get('max_width', 320)
        self.max_height = self.config.get('max_height', 240)
        
        # 输出方式
        self.output_method = self.config.get('output_method', 'local')  # local | minio | ftp | both
        
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
        self.trajectory_color = self.config.get('trajectory_color', (0, 255, 0))  # 绿色
        
        # 初始化 MinIO 输出器
        self.minio_outputter = None
        if self.output_method in ['minio', 'both']:
            self._init_minio_outputter()
        
        # 初始化 FTP 输出器
        self.ftp_outputter = None
        if self.output_method in ['ftp', 'both']:
            self._init_ftp_outputter()
        
        # 启动时清理一次
        if self.auto_cleanup:
            self._cleanup_old_files()
        
        print(f"[GIFGenerator] 初始化完成")
        print(f"[GIFGenerator]   最大帧数: {self.max_frames}")
        print(f"[GIFGenerator]   帧率: {self.fps} FPS")
        print(f"[GIFGenerator]   输出方式: {self.output_method}")
        if self.output_method == 'local' or self.auto_cleanup:
            print(f"[GIFGenerator]   输出目录: {self.output_dir}")
            print(f"[GIFGenerator]   本地保存天数: {self.local_retention_days}")
            print(f"[GIFGenerator]   自动清理: {self.auto_cleanup}")
    
    def _init_minio_outputter(self):
        """初始化 MinIO 输出器"""
        try:
            from .minio_outputter import MinIOOutputter
            
            # 获取全局存储配置（如果存在）
            storage_config = self.config.get('storage', {})
            global_minio_config = storage_config.get('minio', {})
            
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
            
            print(f"[GIFGenerator] MinIO 输出器已初始化")
            print(f"[GIFGenerator]   Bucket: {minio_config.get('bucket')}")
            
        except Exception as e:
            print(f"[GIFGenerator] MinIO 输出器初始化失败: {e}")
    
    def _init_ftp_outputter(self):
        """初始化 FTP 输出器"""
        try:
            from .ftp_outputter import FTPOutputter
            
            # 获取全局存储配置（如果存在）
            storage_config = self.config.get('storage', {})
            global_ftp_config = storage_config.get('ftp', {})
            
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
            
            print(f"[GIFGenerator] FTP 输出器已初始化")
            print(f"[GIFGenerator]   Remote Dir: {ftp_config.get('remote_dir')}")
            
        except Exception as e:
            print(f"[GIFGenerator] FTP 输出器初始化失败: {e}")
    
    def generate(self, track_id: int, frames: List[Dict], 
                output_filename: Optional[str] = None) -> str:
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
            GIF 文件路径
        """
        if not frames:
            print(f"[GIFGenerator] 警告：帧序列为空，无法生成 GIF")
            return ''
        
        # 限制帧数
        if len(frames) > self.max_frames:
            # 采样：保留首帧、尾帧和中间均匀采样
            frames = self._sample_frames(frames, self.max_frames)
        
        # 生成输出文件名
        if not output_filename:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_filename = f"track_{track_id}_{timestamp}.gif"
        
        output_path = os.path.join(self.output_dir, output_filename)
        
        # 处理帧序列
        processed_frames = []
        trajectory_points = []  # 轨迹点
        
        for i, frame_data in enumerate(frames):
            image = frame_data.get('image')
            bbox = frame_data.get('bbox', [0, 0, 0, 0])
            is_full_frame = frame_data.get('is_full_frame', False)
            
            if image is None:
                continue
            
            # 计算中心点（用于轨迹）
            center_x = int((bbox[0] + bbox[2]) / 2)
            center_y = int((bbox[1] + bbox[3]) / 2)
            
            # 如果是完整帧，裁剪目标区域（保留一定上下文）
            if is_full_frame:
                # 扩展边界框（保留上下文）
                h, w = image.shape[:2]
                margin_x = int((bbox[2] - bbox[0]) * 0.5)  # 左右扩展 50%
                margin_y = int((bbox[3] - bbox[1]) * 0.5)  #上下扩展 50%
                
                x1 = max(0, int(bbox[0] - margin_x))
                y1 = max(0, int(bbox[1] - margin_y))
                x2 = min(w, int(bbox[2] + margin_x))
                y2 = min(h, int(bbox[3] + margin_y))
                
                cropped = image[y1:y2, x1:x2]
                
                # 调整轨迹点坐标（相对于裁剪区域）
                trajectory_points.append((center_x - x1, center_y - y1))
            else:
                cropped = image
                # 裁剪帧的中心点（相对于裁剪区域）
                h, w = cropped.shape[:2]
                trajectory_points.append((w // 2, h // 2))
            
            # 缩放图片
            cropped = self._resize_frame(cropped)
            
            # 绘制轨迹（如果启用）
            if self.draw_trajectory and len(trajectory_points) > 1:
                cropped = self._draw_trajectory(cropped, trajectory_points)
            
            # 绘制边界框和标签
            if is_full_frame:
                # 在裁剪区域中绘制边界框（调整坐标）
                h, w = cropped.shape[:2]
                # 计算边界框在裁剪区域中的位置
                # 这里简化处理，直接在中心绘制一个小框
                box_w = int(w * 0.6)
                box_h = int(h * 0.6)
                box_x1 = (w - box_w) // 2
                box_y1 = (h - box_h) // 2
                cv2.rectangle(cropped, (box_x1, box_y1), 
                             (box_x1 + box_w, box_y1 + box_h), 
                             (0, 255, 0), 2)
            
            processed_frames.append(cropped)
        
        if not processed_frames:
            print(f"[GIFGenerator] 警告：处理后帧序列为空")
            return ''
        
        # 生成 GIF 字节数据
        try:
            gif_bytes = self._generate_gif_bytes(processed_frames)
            
            if gif_bytes is None:
                print(f"[GIFGenerator] GIF 生成失败")
                return ''
            
            print(f"[GIFGenerator] GIF 已生成")
            print(f"[GIFGenerator]   帧数: {len(processed_frames)}")
            print(f"[GIFGenerator]   尺寸: {processed_frames[0].shape[1]}x{processed_frames[0].shape[0]}")
            print(f"[GIFGenerator]   大小: {len(gif_bytes) / 1024:.1f} KB")
            
            # 上传 GIF
            gif_urls = self._upload_gif(output_filename, gif_bytes)
            
            # 返回 URL（优先 MinIO，其次 FTP，最后本地路径）
            if gif_urls:
                return gif_urls.get('minio') or gif_urls.get('ftp') or gif_urls.get('local')
            
            # 如果上传失败，保存到本地
            local_path = os.path.join(self.output_dir, output_filename)
            with open(local_path, 'wb') as f:
                f.write(gif_bytes)
            
            return local_path
            
        except Exception as e:
            print(f"[GIFGenerator] GIF 生成失败: {e}")
            return ''
    
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
                print(f"[GIFGenerator] GIF 已上传到 MinIO: {gif_filename}")
            except Exception as e:
                print(f"[GIFGenerator] MinIO 上传失败: {e}")
        
        # 上传到 FTP
        if self.ftp_outputter:
            try:
                gif_filename = filename.replace('.jpg', '.gif') if filename.endswith('.jpg') else filename
                url = self.ftp_outputter.upload(gif_filename, gif_bytes)
                urls['ftp'] = url
                print(f"[GIFGenerator] GIF 已上传到 FTP: {gif_filename}")
            except Exception as e:
                print(f"[GIFGenerator] FTP 上传失败: {e}")
        
        # 保存到本地（如果配置为 local 或作为备份）
        if self.output_method == 'local' or not urls:
            local_path = os.path.join(self.output_dir, filename)
            with open(local_path, 'wb') as f:
                f.write(gif_bytes)
            urls['local'] = local_path
            print(f"[GIFGenerator] GIF 已保存到本地: {local_path}")
        
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
        
        # 绘制轨迹线
        for i in range(1, len(points)):
            pt1 = points[i - 1]
            pt2 = points[i]
            
            # 渐变颜色（从浅到深）
            alpha = i / len(points)
            color = tuple(int(c * alpha) for c in self.trajectory_color)
            
            cv2.line(frame, pt1, pt2, color, 2)
        
        # 绘制当前位置点
        cv2.circle(frame, points[-1], 5, self.trajectory_color, -1)
        
        return frame
    
    def _generate_gif_bytes(self, frames: List[np.ndarray]) -> Optional[bytes]:
        """
        生成 GIF 字节数据
        
        Args:
            frames: 帧序列
        
        Returns:
            GIF 字节数据
        """
        try:
            import imageio
            
            # 转换为 RGB（imageio 需要 RGB）
            rgb_frames = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in frames]
            
            # 生成 GIF 字节数据
            import io
            gif_bytes = io.BytesIO()
            imageio.mimsave(gif_bytes, rgb_frames, duration=self.duration, format='GIF')
            
            return gif_bytes.getvalue()
            
        except ImportError:
            print("[GIFGenerator] imageio 未安装，无法生成 GIF")
            return None
        except Exception as e:
            print(f"[GIFGenerator] GIF 生成错误: {e}")
            return None
    
    def _cleanup_old_files(self):
        """
        清理过期的本地文件
        
        根据 local_retention_days 配置，删除超过保留天数的文件。
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
        
        # 遍历目录
        deleted_count = 0
        total_size = 0
        
        try:
            for filename in os.listdir(self.output_dir):
                filepath = os.path.join(self.output_dir, filename)
                
                # 检查文件修改时间
                if os.path.isfile(filepath):
                    file_mtime = os.path.getmtime(filepath)
                    age_seconds = current_time - file_mtime
                    
                    # 删除过期文件
                    if age_seconds > retention_seconds:
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        deleted_count += 1
                        total_size += file_size
            
            # 更新清理时间
            self.last_cleanup_time = current_time
            
            if deleted_count > 0:
                print(f"[GIFGenerator] 已清理 {deleted_count} 个过期文件")
                print(f"[GIFGenerator]   释放空间: {total_size / 1024 / 1024:.2f} MB")
                print(f"[GIFGenerator]   保留天数: {self.local_retention_days}")
        
        except Exception as e:
            print(f"[GIFGenerator] 清理文件失败: {e}")
    
    def close(self):
        """关闭 GIF 生成器"""
        # 最后一次清理
        if self.auto_cleanup:
            self._cleanup_old_files()
        
        print("[GIFGenerator] 已关闭")