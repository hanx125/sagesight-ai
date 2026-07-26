"""
事件输出管理器

负责管理事件的生成、过滤和输出，支持双通道架构：
- MQTT：完整事件数据给其他平台订阅
- 数据库：事件持久化存储
"""

import json
import uuid
import os
import time
from datetime import datetime
from typing import Dict, Optional, List
import cv2
import numpy as np


class EventOutput:
    """事件输出管理器"""

    def __init__(self, config: Dict):
        """
        初始化事件输出器

        Args:
            config: 事件输出配置字典
        """
        self.config = config
        self.enabled = config.get('enabled', False)

        # 摄像头标识
        self.camera_id = config.get('camera_id', 'default')

        # 任务关联（可选）
        self.task_id = config.get('task_id')
        self.algorithm = config.get('algorithm', 'yolov11')

        # 是否保存到数据库
        self.save_to_db = config.get('save_to_db', False)

        # ========================================
        # 新增：缩略图管理（借鉴 Frigate）
        # ========================================
        # 缩略图存储：event_id -> {image, score, bbox, timestamp}
        self.thumbnails: Dict[str, Dict] = {}

        # 最佳缩略图：event_id -> best_thumbnail_data
        self.best_thumbnails: Dict[str, Dict] = {}

        # 初始化 MQTT 输出器（给其他平台订阅）
        self.mqtt_outputter = None
        self._init_mqtt_outputter()

        # 初始化图片输出器
        self.image_outputters = []
        self._init_image_outputters()

        # 图片本地保存配置
        image_cfg = config.get('image_output', {})
        self.local_backup_enabled = image_cfg.get('local_backup', False)  # 是否本地备份
        self.local_output_dir = image_cfg.get('output_dir', 'output/images')  # 本地保存目录（默认 output/images）
        self.local_retention_days = image_cfg.get('local_retention_days', 7)  # 本地保存天数
        self.auto_cleanup = image_cfg.get('auto_cleanup', True)  # 是否自动清理
        self.cleanup_interval_hours = image_cfg.get('cleanup_interval_hours', 24)  # 清理间隔

        # ✅ 新增：读取服务器配置（用于生成完整的 HTTP URL）
        system_cfg = config.get('system_config', {})
        web_server_cfg = system_cfg.get('web_server', {})
        self.server_host = web_server_cfg.get('host', 'localhost')
        self.server_port = web_server_cfg.get('port', 5555)

        # 上次清理时间
        self.last_cleanup_time = 0

        # 创建本地保存目录（强制创建，无论配置如何）
        os.makedirs(self.local_output_dir, exist_ok=True)

        # 启动时清理一次（如果启用自动清理）
        if self.auto_cleanup:
            self._cleanup_old_images()

        # 图片压缩配置
        self.compression_cfg = config.get('compression', {})

        # 过滤配置
        self.filter_cfg = config.get('filter', {})

        print(f"[EventOutput] 初始化完成")
        print(f"[EventOutput]   MQTT 输出器: {self.mqtt_outputter.__class__.__name__ if self.mqtt_outputter else 'None'}")
        print(f"[EventOutput]   图片输出器数量: {len(self.image_outputters)}")
        if self.local_backup_enabled or self.auto_cleanup:
            print(f"[EventOutput]   本地保存目录: {self.local_output_dir}")
            print(f"[EventOutput]   本地保存天数: {self.local_retention_days}")
            print(f"[EventOutput]   自动清理: {self.auto_cleanup}")
        print(f"[EventOutput]   缩略图管理: 已启用")
    
    def _init_mqtt_outputter(self):
        """初始化 MQTT 输出器（给其他平台订阅）"""
        mqtt_cfg = self.config.get('mqtt', {})
        
        if not mqtt_cfg.get('enabled', False):
            return
        
        try:
            from .mqtt_outputter import MQTTOutputter
            
            mqtt_config = mqtt_cfg.get('config', {})
            self.mqtt_outputter = MQTTOutputter(mqtt_config)
            
            print(f"[EventOutput] MQTT 输出器已初始化（给其他平台订阅）")
            print(f"[EventOutput]   Broker: {mqtt_config.get('broker', 'localhost:1883')}")
            print(f"[EventOutput]   Topic: {mqtt_config.get('topic', 'sagesight/events')}")
            
        except Exception as e:
            print(f"[EventOutput] MQTT 输出器初始化失败: {e}")

    def _init_image_outputters(self):
        """初始化图片输出器"""
        image_cfg = self.config.get('image_output', {})
        method = image_cfg.get('method', 'none')
        
        # 获取全局存储配置
        storage_config = self.config.get('storage', {})
        global_minio_config = storage_config.get('minio', {})
        global_ftp_config = storage_config.get('ftp', {})

        try:
            # MinIO 输出器
            if method in ['minio', 'both']:
                # 检查是否启用 MinIO
                if not global_minio_config.get('enabled', False):
                    print(f"[EventOutput] MinIO 已禁用，跳过图片MinIO输出器初始化")
                else:
                    minio_bucket = image_cfg.get('minio_bucket', 'sagesight-images')
                
                # 合并配置：全局配置 + 图片特定配置
                minio_config = {}
                
                if global_minio_config:
                    minio_config = {
                        'enabled': global_minio_config.get('enabled', True),
                        'endpoint': global_minio_config.get('endpoint', 'localhost:9000'),
                        'access_key': global_minio_config.get('access_key', 'minioadmin'),
                        'secret_key': global_minio_config.get('secret_key', 'minioadmin'),
                        'secure': global_minio_config.get('secure', False),
                        'presigned_expires': global_minio_config.get('presigned_expires', 3600),
                        'bucket': minio_bucket
                    }
                else:
                    # 向后兼容：使用单独的 minio 配置
                    old_minio_config = image_cfg.get('minio', {})
                    minio_config = {
                        'enabled': old_minio_config.get('enabled', True),
                        'endpoint': old_minio_config.get('endpoint', 'localhost:9000'),
                        'access_key': old_minio_config.get('access_key', 'minioadmin'),
                        'secret_key': old_minio_config.get('secret_key', 'minioadmin'),
                        'secure': old_minio_config.get('secure', False),
                        'bucket': old_minio_config.get('bucket', minio_bucket)
                    }
                
                if minio_config.get('enabled', True):
                    from .minio_outputter import MinIOOutputter
                    self.image_outputters.append(MinIOOutputter(minio_config))
                    print(f"[EventOutput] MinIO 输出器已初始化")
                    print(f"[EventOutput]   Bucket: {minio_config.get('bucket')}")
            
            # FTP 输出器
            if method in ['ftp', 'both']:
                # 检查是否启用 FTP
                if not global_ftp_config.get('enabled', False):
                    print(f"[EventOutput] FTP 已禁用，跳过图片FTP输出器初始化")
                else:
                    ftp_remote_dir = image_cfg.get('ftp_remote_dir', '/sagesight/images')
                
                # 合并配置：全局配置 + 图片特定配置
                ftp_config = {}
                
                if global_ftp_config:
                    ftp_config = {
                        'enabled': global_ftp_config.get('enabled', True),
                        'host': global_ftp_config.get('host', 'localhost'),
                        'port': global_ftp_config.get('port', 21),
                        'username': global_ftp_config.get('username', 'ftpuser'),
                        'password': global_ftp_config.get('password', 'ftppassword'),
                        'passive_mode': global_ftp_config.get('passive_mode', True),
                        'remote_dir': ftp_remote_dir
                    }
                else:
                    # 向后兼容：使用单独的 ftp 配置
                    old_ftp_config = image_cfg.get('ftp', {})
                    ftp_config = {
                        'enabled': old_ftp_config.get('enabled', True),
                        'host': old_ftp_config.get('host', 'localhost'),
                        'port': old_ftp_config.get('port', 21),
                        'username': old_ftp_config.get('username', 'ftpuser'),
                        'password': old_ftp_config.get('password', 'ftppassword'),
                        'passive_mode': old_ftp_config.get('passive_mode', True),
                        'remote_dir': old_ftp_config.get('remote_dir', ftp_remote_dir)
                    }
                
                if ftp_config.get('enabled', True):
                    from .ftp_outputter import FTPOutputter
                    self.image_outputters.append(FTPOutputter(ftp_config))
                    print(f"[EventOutput] FTP 输出器已初始化")
                    print(f"[EventOutput]   Remote Dir: {ftp_config.get('remote_dir')}")
                    
        except Exception as e:
            print(f"[EventOutput] 图片输出器初始化失败: {e}")
    
    # ========================================
    # 新增：缩略图管理（借鉴 Frigate）
    # ========================================
    def update_thumbnail(self, event_id: str, image: np.ndarray,
                        score: float, bbox: tuple) -> bool:
        """
        更新事件缩略图（自动选择最佳帧）

        参考 Frigate 的 is_better_thumbnail 逻辑：
        - 选择最高分数的帧作为缩略图
        - 提高事件质量

        Args:
            event_id: 事件 ID
            image: 图片数据（OpenCV BGR 格式）
            score: 检测分数（computed_score）
            bbox: 边界框 (x1, y1, x2, y2)

        Returns:
            bool: 是否更新了最佳缩略图
        """
        # ✅ 新增：定期清理旧的缩略图（避免内存泄漏）
        if len(self.thumbnails) > 100:
            try:
                # 删除最旧的50个缩略图
                old_keys = sorted(self.thumbnails.keys(),
                                 key=lambda k: self.thumbnails[k]['timestamp'])[:50]
                for key in old_keys:
                    self.clear_thumbnail(key)

                print(f"[EventOutput] 清理了 {len(old_keys)} 个旧缩略图")
            except Exception as e:
                print(f"[EventOutput] 清理缩略图失败: {e}")

        # 初始化事件缩略图存储
        if event_id not in self.thumbnails:
            self.thumbnails[event_id] = {
                'image': None,
                'score': 0.0,
                'bbox': None,
                'timestamp': time.time()
            }

        # 检查是否为更好的缩略图（分数更高）
        current_best_score = self.thumbnails[event_id]['score']

        if score > current_best_score:
            # 更新最佳缩略图
            self.thumbnails[event_id] = {
                'image': image.copy(),  # 避免引用问题
                'score': score,
                'bbox': bbox,
                'timestamp': time.time()
            }

            # 同时更新 best_thumbnails（用于快速访问）
            self.best_thumbnails[event_id] = self.thumbnails[event_id]

            print(f"[EventOutput] 更新最佳缩略图: event_id={event_id}, score={score:.3f}")
            return True

        return False
    
    def get_best_thumbnail(self, event_id: str) -> Optional[Dict]:
        """
        获取事件的最佳缩略图
        
        Args:
            event_id: 事件 ID
            
        Returns:
            Dict: 最佳缩略图数据 {image, score, bbox, timestamp}
        """
        return self.best_thumbnails.get(event_id)
    
    def clear_thumbnail(self, event_id: str):
        """
        清除事件缩略图（事件结束时）
        
        Args:
            event_id: 事件 ID
        """
        if event_id in self.thumbnails:
            del self.thumbnails[event_id]
        
        if event_id in self.best_thumbnails:
            del self.best_thumbnails[event_id]
    
    # ========================================
    # 新增：属性事件支持（借鉴 Frigate）
    # ========================================
    def emit_attribute_event(self, track_id: int, attribute_type: str,
                            attribute_value: str, confidence: float,
                            bbox: tuple, image: Optional[np.ndarray] = None) -> Optional[str]:
        """
        发送属性事件
        
        参考 Frigate 的属性系统：
        - 支持多种属性类型（颜色、类型、方向等）
        - 扩展事件类型
        
        Args:
            track_id: 跟踪 ID
            attribute_type: 属性类型（如 'color', 'type', 'direction'）
            attribute_value: 属性值（如 'red', 'car', 'left'）
            confidence: 属性置信度
            bbox: 边界框 (x1, y1, x2, y2)
            image: 可选的图片数据
            
        Returns:
            事件 ID，如果未发送则返回 None
        """
        # 构造属性事件数据
        data = {
            'track_id': track_id,
            'attribute_type': attribute_type,
            'attribute_value': attribute_value,
            'confidence': confidence,
            'bbox': bbox,
            'class_name': f'{attribute_type}_{attribute_value}',  # 用于过滤
            'event_subtype': 'attribute'  # 标记为属性事件
        }
        
        # 发送事件
        event_type = f'attribute_{attribute_type}'
        return self.emit(event_type, data, image)
    
    def _compress_image(self, image: np.ndarray) -> bytes:
        """
        压缩图片
        
        Args:
            image: OpenCV 格式的图片 (BGR)
        
        Returns:
            压缩后的 JPEG 字节数据
        """
        if not self.compression_cfg.get('enabled', True):
            _, buffer = cv2.imencode('.jpg', image)
            return buffer.tobytes()
        
        quality = self.compression_cfg.get('quality', 80)
        max_width = self.compression_cfg.get('max_width', 640)
        max_height = self.compression_cfg.get('max_height', 480)
        
        # 缩放图片
        h, w = image.shape[:2]
        if w > max_width or h > max_height:
            scale = min(max_width / w, max_height / h)
            image = cv2.resize(image, None, fx=scale, fy=scale)
        
        # 压缩为 JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        _, buffer = cv2.imencode('.jpg', image, encode_param)
        return buffer.tobytes()
    
    def should_send(self, class_name: str, confidence: float) -> bool:
        """
        公共方法：检查事件是否通过过滤（供外部调用，避免无效GIF/图片生成）
        
        Args:
            class_name: 目标类别名称
            confidence: 检测置信度
        
        Returns:
            是否应该发送
        """
        return self._should_send(class_name, confidence)
    
    def _should_send(self, class_name: str, confidence: float) -> bool:
        """
        检查是否应该发送此事件
        
        Args:
            class_name: 目标类别名称
            confidence: 检测置信度
        
        Returns:
            是否应该发送
        """
        # 检查类别过滤
        target_classes = self.filter_cfg.get('target_classes', [])
        if target_classes and class_name not in target_classes:
            return False
        
        # 检查置信度过滤
        min_confidence = self.filter_cfg.get('min_confidence', 0.0)
        if confidence < min_confidence:
            return False
        
        return True
    
    def _save_image_local(self, event_id: str, image_bytes: bytes) -> Optional[str]:
        """
        保存图片到本地
        
        Args:
            event_id: 事件 ID
            image_bytes: 图片字节数据
        
        Returns:
            本地文件路径，失败返回 None
        """
        if not os.path.exists(self.local_output_dir):
            os.makedirs(self.local_output_dir, exist_ok=True)
        
        filename = f"{event_id}.jpg"
        filepath = os.path.join(self.local_output_dir, filename)
        
        try:
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            
            print(f"[EventOutput] 图片已保存到本地: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"[EventOutput] 本地保存失败: {e}")
            return None
    
    def _cleanup_old_images(self):
        """
        清理过期的本地图片文件
        
        根据 local_retention_days 配置，删除超过保留天数的图片。
        """
        if not os.path.exists(self.local_output_dir):
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
            for filename in os.listdir(self.local_output_dir):
                filepath = os.path.join(self.local_output_dir, filename)
                
                # 检查文件扩展名
                if not filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                    continue
                
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
                print(f"[EventOutput] 已清理 {deleted_count} 个过期图片")
                print(f"[EventOutput]   释放空间: {total_size / 1024 / 1024:.2f} MB")
                print(f"[EventOutput]   保留天数: {self.local_retention_days}")
        
        except Exception as e:
            print(f"[EventOutput] 清理图片失败: {e}")
    
    def emit(self, event_type: str, data: Dict, image: Optional[np.ndarray] = None, 
            frame_id: int = 0, task_id: int = None, algorithm: str = None,
            metadata: Optional[Dict] = None) -> Optional[str]:
        """
        发送事件
        
        Args:
            event_type: 事件类型 (detection, track_start, track_end, track_update)
            data: 事件数据字典，包含 track_id, class_name, confidence, bbox 等
            image: 可选的图片数据 (OpenCV BGR 格式)
            frame_id: 当前帧 ID
            task_id: 关联的任务 ID（可选）
            algorithm: 使用的算法（可选）
            metadata: 额外的元数据
        
        Returns:
            事件 ID，如果未发送则返回 None
        """
        # ✅ 调试日志：跟踪事件输出流程
        print(f"[EventOutput] emit() 被调用: type={event_type}, class_name={data.get('class_name', '')}, confidence={data.get('confidence', 0.0)}")
        print(f"[EventOutput]   self.enabled={self.enabled}")
        
        if not self.enabled:
            print(f"[EventOutput]   事件未启用，跳过")
            return None
        
        # 检查过滤条件
        class_name = data.get('class_name', '')
        confidence = data.get('confidence', 0.0)
        should_send = self._should_send(class_name, confidence)
        print(f"[EventOutput]   过滤检查结果: should_send={should_send}, class_name={class_name}, confidence={confidence}, target_classes={self.filter_cfg.get('target_classes', [])}, min_confidence={self.filter_cfg.get('min_confidence', 0.0)}")
        if not should_send:
            print(f"[EventOutput]   未通过过滤，跳过")
            return None
        
        # 生成事件 ID
        event_id = str(uuid.uuid4())
        
        # 使用传入的 task_id 或默认值
        actual_task_id = task_id or self.task_id
        actual_algorithm = algorithm or self.algorithm
        
        # 处理图片：先保存到本地，再根据配置决定是否上传远程
        image_urls = {}
        local_image_path = None
        
        print(f"[EventOutput]   图片处理: image is not None={image is not None}")
        print(f"[EventOutput]   local_backup_enabled={self.local_backup_enabled}, image_outputters count={len(self.image_outputters)}")
        
        if image is not None:
            try:
                compressed_image = self._compress_image(image)
                print(f"[EventOutput]   图片压缩成功: {len(compressed_image)} bytes")
                
                # ✅ 强制先保存到本地（无论配置如何，符合项目约束）
                local_image_path = self._save_image_local(event_id, compressed_image)
                if local_image_path:
                    # ✅ 修复：生成完整的 HTTP URL（避免浏览器 HTTPS 强制升级）
                    # 将本地路径转换为 URL（如 output/images/xxx.jpg -> http://localhost:5555/output/images/xxx.jpg）
                    local_image_url = f"http://{self.server_host}:{self.server_port}/{local_image_path}"
                    image_urls['local'] = local_image_url
                    print(f"[EventOutput]   图片本地保存成功: {local_image_path}")
                    print(f"[EventOutput]   图片 URL: {local_image_url}")
                
                # 2. 再根据配置上传到远程服务器（MinIO/FTP）
                if self.image_outputters:
                    for outputter in self.image_outputters:
                        try:
                            url = outputter.upload(event_id, compressed_image)
                            image_urls[outputter.__class__.__name__] = url
                            print(f"[EventOutput]   图片上传成功 ({outputter.__class__.__name__}): {url}")
                        except Exception as e:
                            print(f"[EventOutput] 图片上传失败 ({outputter.__class__.__name__}): {e}")
                        
            except Exception as e:
                print(f"[EventOutput] 图片处理失败: {e}")
                import traceback
                traceback.print_exc()
                
                # 处理失败时也强制本地保存
                try:
                    compressed_image = self._compress_image(image)
                    local_image_path = self._save_image_local(event_id, compressed_image)
                    if local_image_path:
                        # ✅ 修复：生成完整的 HTTP URL
                        local_image_url = f"http://{self.server_host}:{self.server_port}/{local_image_path}"
                        image_urls['local'] = local_image_url
                except Exception as e2:
                    print(f"[EventOutput] 本地保存失败: {e2}")
        else:
            print(f"[EventOutput]   未传入图片，跳过图片处理")
        
        # 构建事件
        event = {
            'event_id': event_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'type': event_type,
            'camera_id': self.camera_id,
            'task_id': actual_task_id,
            'algorithm': actual_algorithm,
            'frame_id': frame_id,
            'data': data,
            'image_urls': image_urls,
            'metadata': metadata or {}
        }
        
        # 发送元数据到 MQTT（给其他平台订阅）
        if self.mqtt_outputter:
            try:
                self.mqtt_outputter.send(event)
                print(f"[EventOutput] 事件已发送到 MQTT: {event_type} - {class_name} (ID: {event_id[:8]}...)")
            except Exception as e:
                print(f"[EventOutput] MQTT 发送失败: {e}")
                return None
        
        # 保存到数据库（如果启用）
        if self.save_to_db or actual_task_id:
            self._save_to_database(event)

        return event_id

    def _save_to_database(self, event: Dict):
        """
        保存事件到数据库
        
        Args:
            event: 事件字典
        """
        try:
            from src.tasks.task_manager import TaskManager, EventRecord
            
            task_manager = TaskManager()
            # 分别保存三个独立的图片URL字段
            image_urls = event.get('image_urls', {})
            # 提取GIF URL字典
            gif_urls = event.get('data', {}).get('gif_urls', {})
            
            event_record = EventRecord(
                event_id=event['event_id'],
                task_id=event.get('task_id'),
                camera_id=event['camera_id'],
                algorithm=event.get('algorithm', ''),
                type=event['type'],
                class_name=event['data'].get('class_name', ''),
                confidence=event['data'].get('confidence', 0.0),
                bbox=event['data'].get('bbox', []),
                local_image_url=image_urls.get('local', ''),
                minio_image_url=image_urls.get('MinIOOutputter', ''),
                ftp_image_url=image_urls.get('FTPOutputter', ''),
                local_gif_url=gif_urls.get('local', ''),
                minio_gif_url=gif_urls.get('minio', ''),
                ftp_gif_url=gif_urls.get('ftp', ''),
                metadata=event.get('metadata', {}),
                start_time=self._parse_datetime(event['data'].get('start_time')),
                end_time=self._parse_datetime(event['data'].get('end_time'))
            )
            task_manager.add_event(event_record)
            
            # 设置created_at（add_event未回写对象）
            if not event_record.created_at:
                event_record.created_at = datetime.now()
            
            # 通过WebSocket实时推送新事件到前端
            self._push_event_to_websocket(event_record)
            
        except Exception as e:
            print(f"[EventOutput] 保存事件到数据库失败: {e}")
    
    @staticmethod
    def _parse_datetime(value):
        """安全解析时间字符串"""
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            try:
                return datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                return None
    
    def _push_event_to_websocket(self, event_record):
        """通过HTTP API推送事件到Flask服务器，再由服务器通过WebSocket广播到驾驶舱"""
        try:
            import requests
            
            # 构建前端需要的事件数据
            event_data = {
                'event_id': event_record.event_id,
                'task_id': event_record.task_id,
                'camera_id': event_record.camera_id,
                'algorithm': event_record.algorithm,
                'type': event_record.type,
                'class_name': event_record.class_name,
                'confidence': event_record.confidence,
                'bbox': event_record.bbox,
                'local_image_url': event_record.local_image_url,
                'minio_image_url': event_record.minio_image_url,
                'ftp_image_url': event_record.ftp_image_url,
                'start_time': event_record.start_time.isoformat() + 'Z' if event_record.start_time else None,
                'end_time': event_record.end_time.isoformat() + 'Z' if event_record.end_time else None,
                'created_at': event_record.created_at.isoformat() + 'Z' if event_record.created_at else None
            }
            
            # 通过HTTP POST发送到Flask服务器，由服务器通过WebSocket广播
            resp = requests.post(
                'http://127.0.0.1:5555/api/events/push',
                json=event_data,
                timeout=2
            )
            print(f"[EventOutput] 事件已推送到驾驶舱: {event_record.event_id[:8]}... type={event_record.type}")
            
        except Exception as e:
            print(f"[EventOutput] 推送事件到驾驶舱失败: {e}")
    
    def emit_detection(self, track_id: int, class_name: str, confidence: float, 
                    bbox: List[float], image: Optional[np.ndarray] = None, 
                    frame_id: int = 0, task_id: int = None, algorithm: str = None,
                    extra_metadata: Optional[Dict] = None) -> Optional[str]:
        """
        发送检测事件（便捷方法）
        
        Args:
            track_id: 跟踪 ID
            class_name: 类别名称
            confidence: 置信度
            bbox: 边界框 [x1, y1, x2, y2]
            image: 可选图片
            frame_id: 帧 ID
            task_id: 关联的任务 ID（可选）
            algorithm: 使用的算法（可选）
            extra_metadata: 额外元数据
        
        Returns:
            事件 ID
        """
        data = {
            'track_id': track_id,
            'class_name': class_name,
            'confidence': confidence,
            'bbox': bbox,
            'center': [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
        }
        
        return self.emit(
            event_type='detection',
            data=data,
            image=image,
            frame_id=frame_id,
            task_id=task_id,
            algorithm=algorithm,
            metadata=extra_metadata
        )

    def close(self):
        """关闭所有输出器"""
        # 关闭 MQTT 输出器
        if self.mqtt_outputter:
            try:
                self.mqtt_outputter.close()
            except Exception as e:
                print(f"[EventOutput] 关闭 MQTT 输出器失败: {e}")
        
        # 关闭 WebSocket 输出器
        if self.websocket_outputter:
            try:
                self.websocket_outputter.close()
            except Exception as e:
                print(f"[EventOutput] 关闭 WebSocket 输出器失败: {e}")
        
        # 关闭图片输出器
        for outputter in self.image_outputters:
            try:
                outputter.close()
            except Exception as e:
                print(f"[EventOutput] 关闭图片输出器失败 ({outputter.__class__.__name__}): {e}")
        
        # 最后一次清理本地图片
        if self.auto_cleanup:
            self._cleanup_old_images()
        
        print("[EventOutput] 已关闭所有输出器")
