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
from typing import Dict, Optional, List, Tuple
import cv2
import numpy as np
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


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
        # 摄像机名称缓存（MQTT 推送时附带真实名称，供第三方映射设备；避免每次推送查库）
        self._camera_name_cache: Optional[str] = None
        # 摄像机物联网坐标缓存（iot_product_key / iot_device_name）
        self._camera_iot_cache: Optional[Tuple[str, str]] = None

        # 任务关联（可选）
        self.task_id = config.get('task_id')
        self.algorithm = config.get('algorithm', 'yolov11')

        # 是否保存到数据库
        self.save_to_db = config.get('save_to_db', False)

        # ========================================
        # 新增：缩略图管理）
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
        # local_enabled 由 _init_image_outputters 设置（支持独立开关和旧版 local_backup 兼容）
        image_cfg = config.get('image_output', {})
        self.local_output_dir = image_cfg.get('output_dir', 'output/images')  # 本地保存目录（默认 output/images）
        self.local_retention_days = image_cfg.get('local_retention_days', 7)  # 本地保存天数
        self.auto_cleanup = image_cfg.get('auto_cleanup', True)  # 是否自动清理
        self.cleanup_interval_hours = image_cfg.get('cleanup_interval_hours', 24)  # 清理间隔

        # 读取服务器配置（统一通过 system_config_loader 获取，单一真相源）
        # host 用于生成事件图片/GIF 的可访问 URL（MQTT 云端拉图）：
        # 未配置时自动探测本机局域网 IP，换设备部署无需手动改地址
        from src.system.system_config_loader import get_event_image_host, get_web_server_port
        self.server_host = get_event_image_host()
        self.server_port = get_web_server_port()

        # 上次清理时间
        self.last_cleanup_time = 0

        # 异步 I/O 线程池（远程上传、MQTT发送、数据库保存、WebSocket推送）
        # 避免 I/O 阻塞事件处理主循环导致队列积压
        # emit() 的上传和 update_event() 的上传+DB 都提交到此池，需要足够 worker 让两者并行
        self._io_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='event_io')

        # 创建本地保存目录（强制创建，无论配置如何）
        os.makedirs(self.local_output_dir, exist_ok=True)

        # 启动时清理一次（异步执行，避免阻塞 TrackedObjectProcessor 初始化）
        if self.auto_cleanup:
            self._io_executor.submit(self._cleanup_old_images)

        # 图片压缩配置
        self.compression_cfg = config.get('compression', {})

        # 过滤配置
        self.filter_cfg = config.get('filter', {})

        # 边缘库比对配置（人脸/车牌目标作为任务主检测模型输出；命中后走精简事件：
        # 只保留一张按检测框外扩裁剪的抓拍小图，随事件上报并送边缘识别+比对）
        self.library_match_cfg = config.get('library_match', {}) or {}
        self.library_crop_margin = float(self.library_match_cfg.get('crop_margin', 0.2) or 0.0)
        self.library_client = None
        if self.library_match_cfg.get('enabled'):
            from src.library.edge_library_client import EdgeLibraryClient
            self.library_client = EdgeLibraryClient(self.library_match_cfg)

        # GIF 配置（供 TrackedObjectProcessor 读取）
        self.gif_cfg = config.get('gif', {})
        self.gif_enabled = self.gif_cfg.get('enabled', False)

        # 关键帧采集配置（供 TrackedObjectProcessor 读取）
        capture_cfg = config.get('capture', {})
        self.capture_save_full_on_start = capture_cfg.get('save_full_on_start', True)
        self.capture_save_full_on_end = capture_cfg.get('save_full_on_end', True)
        # required_zones：目标必须进入过其中任一区域，才在 end 阶段保存图片/GIF
        # 空列表表示不限制（所有目标都保存）
        self.required_zones: List[str] = list(capture_cfg.get('required_zones', []) or [])

        # GIF 生成器（延迟初始化，仅在 GIF 启用时创建）
        self._gif_generator = None

        logger.debug(f"[EventOutput] 初始化完成")
        logger.debug(f"[EventOutput]   MQTT 输出器: {self.mqtt_outputter.__class__.__name__ if self.mqtt_outputter else 'None'}")
        logger.debug(f"[EventOutput]   图片输出器数量: {len(self.image_outputters)}")
        if getattr(self, 'local_enabled', False) or self.auto_cleanup:
            logger.debug(f"[EventOutput]   本地保存: {'启用' if getattr(self, 'local_enabled', False) else '禁用'}")
            logger.debug(f"[EventOutput]   本地保存目录: {self.local_output_dir}")
            logger.debug(f"[EventOutput]   本地保存天数: {self.local_retention_days}")
            logger.debug(f"[EventOutput]   自动清理: {self.auto_cleanup}")
        logger.debug(f"[EventOutput]   缩略图管理: 已启用")
    
    def _init_mqtt_outputter(self):
        """初始化 MQTT 输出器（给其他平台订阅）"""
        mqtt_cfg = self.config.get('mqtt', {})

        if not mqtt_cfg.get('enabled', False):
            logger.debug(f"[EventOutput] MQTT 未启用，跳过初始化")
            return

        try:
            from .mqtt_outputter import MQTTOutputter

            mqtt_config = mqtt_cfg.get('config', {})
            self.mqtt_outputter = MQTTOutputter(mqtt_config)

            logger.info(f"[EventOutput] MQTT 输出器已初始化（给其他平台订阅）")
            logger.info(f"[EventOutput]   Broker: {mqtt_config.get('broker', 'localhost:1883')}")
            logger.info(f"[EventOutput]   Topic: {mqtt_config.get('topic', 'sagesight/events')}")

        except Exception as e:
            logger.warning(f"[EventOutput] MQTT 输出器初始化失败: {e}")

    def _init_image_outputters(self):
        """初始化图片输出器（根据独立开关：local_enabled / minio_enabled / ftp_enabled）"""
        image_cfg = self.config.get('image_output', {})

        # 向后兼容：旧版 method 字段自动转换为独立开关
        method = image_cfg.get('method', None)
        if method is not None and 'local_enabled' not in image_cfg:
            if method == 'both':
                image_cfg['local_enabled'] = True
                image_cfg['minio_enabled'] = True
                image_cfg['ftp_enabled'] = True
            elif method == 'minio':
                image_cfg['local_enabled'] = False
                image_cfg['minio_enabled'] = True
                image_cfg['ftp_enabled'] = False
            elif method == 'ftp':
                image_cfg['local_enabled'] = False
                image_cfg['minio_enabled'] = False
                image_cfg['ftp_enabled'] = True
            elif method == 'local':
                image_cfg['local_enabled'] = True
                image_cfg['minio_enabled'] = False
                image_cfg['ftp_enabled'] = False
            elif method == 'none':
                image_cfg['local_enabled'] = False
                image_cfg['minio_enabled'] = False
                image_cfg['ftp_enabled'] = False
            logger.debug(f"[EventOutput] 旧版 method='{method}' 已自动迁移为独立开关")

        # 读取三个独立开关
        self.local_enabled = image_cfg.get('local_enabled', True)
        minio_enabled = image_cfg.get('minio_enabled', False)
        ftp_enabled = image_cfg.get('ftp_enabled', False)

        # 获取全局存储配置（连接参数和全局开关）
        storage_config = self.config.get('storage', {})
        global_minio_config = storage_config.get('minio', {})
        global_ftp_config = storage_config.get('ftp', {})

        logger.debug(f"[EventOutput] 存储开关: local={self.local_enabled}, minio={minio_enabled}, ftp={ftp_enabled}")

        try:
            # ========================================
            # MinIO 输出器（任务开关 AND 系统全局开关）
            # ========================================
            if minio_enabled:
                if not global_minio_config.get('enabled', False):
                    logger.debug(f"[EventOutput] MinIO 任务开关已启用，但系统全局 storage.minio.enabled=false，跳过MinIO输出器")
                else:
                    minio_bucket = image_cfg.get('minio_bucket', 'sagesight-images')
                    minio_config = {
                        'enabled': True,
                        'endpoint': global_minio_config.get('endpoint', 'localhost:9000'),
                        'access_key': global_minio_config.get('access_key', 'minioadmin'),
                        'secret_key': global_minio_config.get('secret_key', 'minioadmin'),
                        'secure': global_minio_config.get('secure', False),
                        'presigned_expires': global_minio_config.get('presigned_expires', 3600),
                        'bucket': minio_bucket
                    }
                    from .minio_outputter import MinIOOutputter
                    self.image_outputters.append(MinIOOutputter(minio_config))
                    logger.debug(f"[EventOutput] MinIO 输出器已初始化 (Bucket: {minio_config.get('bucket')})")

            # ========================================
            # FTP 输出器（任务开关 AND 系统全局开关）
            # ========================================
            if ftp_enabled:
                if not global_ftp_config.get('enabled', False):
                    logger.debug(f"[EventOutput] FTP 任务开关已启用，但系统全局 storage.ftp.enabled=false，跳过FTP输出器")
                else:
                    ftp_remote_dir = image_cfg.get('ftp_remote_dir', '/sagesight/images')
                    ftp_config = {
                        'enabled': True,
                        'host': global_ftp_config.get('host', 'localhost'),
                        'port': global_ftp_config.get('port', 21),
                        'username': global_ftp_config.get('username', 'ftpuser'),
                        'password': global_ftp_config.get('password', 'ftppassword'),
                        'passive_mode': global_ftp_config.get('passive_mode', True),
                        'remote_dir': ftp_remote_dir
                    }
                    from .ftp_outputter import FTPOutputter
                    self.image_outputters.append(FTPOutputter(ftp_config))
                    logger.debug(f"[EventOutput] FTP 输出器已初始化 (Remote Dir: {ftp_config.get('remote_dir')})")

        except Exception as e:
            logger.debug(f"[EventOutput] 图片输出器初始化失败: {e}")
    
    # ========================================
    # 新增：缩略图管理
    # ========================================
    def update_thumbnail(self, event_id: str, image: np.ndarray,
                        score: float, bbox: tuple) -> bool:
        """
        更新事件缩略图（自动选择最佳帧）

        is_better_thumbnail 逻辑：
        - 选择最高分数的帧作为缩略图
        - 提高事件质量

        Args:
            event_id: 事件 ID
            image: 图片数据（RGB 格式，与项目统一语义一致）
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

                logger.debug(f"[EventOutput] 清理了 {len(old_keys)} 个旧缩略图")
            except Exception as e:
                logger.debug(f"[EventOutput] 清理缩略图失败: {e}")

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

            logger.debug(f"[EventOutput] 更新最佳缩略图: event_id={event_id}, score={score:.3f}")
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
    # 新增：属性事件支持
    # ========================================
    def emit_attribute_event(self, track_id: int, attribute_type: str,
                            attribute_value: str, confidence: float,
                            bbox: tuple, image: Optional[np.ndarray] = None) -> Optional[str]:
        """
        发送属性事件
        
        属性系统：
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
        压缩图片（RGB 格式，PIL 编码无需 BGR 转换）

        Args:
            image: RGB 格式的图片 (numpy 数组)

        Returns:
            压缩后的 JPEG 字节数据
        """
        from src.utils.image import encode_jpeg

        if not self.compression_cfg.get('enabled', True):
            return encode_jpeg(image, quality=80)

        quality = self.compression_cfg.get('quality', 80)
        max_width = self.compression_cfg.get('max_width', 640)
        max_height = self.compression_cfg.get('max_height', 480)

        # 缩放图片（cv2.resize 不关心通道顺序，无需转换）
        h, w = image.shape[:2]
        if w > max_width or h > max_height:
            scale = min(max_width / w, max_height / h)
            image = cv2.resize(image, None, fx=scale, fy=scale)

        return encode_jpeg(image, quality=quality)
    
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
        保存图片到本地（磁盘空间不足时滚动删除旧文件）

        Args:
            event_id: 事件 ID
            image_bytes: 图片字节数据

        Returns:
            本地文件路径，失败返回 None
        """
        if not os.path.exists(self.local_output_dir):
            os.makedirs(self.local_output_dir, exist_ok=True)

        # 磁盘空间保护：写入前确保有足够空间，不足时滚动删除旧图片
        from src.utils.disk_guard import ensure_disk_space
        ensure_disk_space(self.local_output_dir, len(image_bytes))

        filename = f"{event_id}.jpg"
        filepath = os.path.join(self.local_output_dir, filename)

        try:
            with open(filepath, 'wb') as f:
                f.write(image_bytes)

            logger.debug(f"[EventOutput] 图片已保存到本地: {filepath}")
            return filepath

        except Exception as e:
            logger.debug(f"[EventOutput] 本地保存失败: {e}")
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
                logger.debug(f"[EventOutput] 已清理 {deleted_count} 个过期图片")
                logger.debug(f"[EventOutput]   释放空间: {total_size / 1024 / 1024:.2f} MB")
                logger.debug(f"[EventOutput]   保留天数: {self.local_retention_days}")
        
        except Exception as e:
            logger.debug(f"[EventOutput] 清理图片失败: {e}")
    
    def _get_camera_name(self) -> str:
        """获取摄像机真实名称（MQTT 推送附带，供第三方按名称映射设备）

        结果缓存（名称极少变化，避免每次推送查库）；查询失败或相机已删除时
        回退为 camera_id，保证第三方仍能定位设备。
        """
        if self._camera_name_cache is None:
            self._load_camera_cache()
        return self._camera_name_cache or self.camera_id

    def _get_camera_iot(self) -> Tuple[str, str]:
        """获取摄像机物联网坐标（iot_product_key / iot_device_name）。

        供物联网平台按 productKey+deviceName 解析 AI 事件（每路摄像机对应
        云平台上的产品/设备，在摄像机管理中按台配置）；结果随名称一并缓存。
        """
        if self._camera_name_cache is None:
            self._load_camera_cache()
        return self._camera_iot_cache

    def _load_camera_cache(self):
        """一次性加载摄像机名称与物联网坐标并缓存（查询失败或相机已删除时空值）"""
        try:
            from src.cameras.camera_manager import get_camera_manager
            camera = get_camera_manager().get_camera(self.camera_id)
            if camera:
                self._camera_name_cache = camera.name
                self._camera_iot_cache = (
                    camera.iot_product_key or '',
                    camera.iot_device_name or '',
                )
            else:
                self._camera_name_cache = ''
                self._camera_iot_cache = ('', '')
        except Exception as e:
            logger.debug(f"[EventOutput] 获取摄像机信息失败: {e}")
            self._camera_name_cache = ''
            self._camera_iot_cache = ('', '')

    def emit(self, event_type: str, data: Dict, image: Optional[np.ndarray] = None,
            frame_id: int = 0, task_id: int = None, algorithm: str = None,
            metadata: Optional[Dict] = None, lifecycle: bool = False,
            defer_db: bool = False, bypass_filter: bool = False) -> Optional[str]:
        """
        发送事件

        Args:
            event_type: 事件类型 (detection, track_start, track_end, track_update)
            data: 事件数据字典，包含 track_id, class_name, confidence, bbox 等
            image: 可选的图片数据 (RGB 格式 numpy 数组)
            frame_id: 当前帧 ID
            task_id: 关联的任务 ID（可选）
            algorithm: 使用的算法（可选）
            metadata: 额外的元数据
            lifecycle: 是否参与跟踪事件状态机（规则/语义去重 → final）。
                True 时创建阶段不推 MQTT，待 GIF 与 LLM 全部完成后由
                push_terminal_to_mqtt 单推一条终态消息（final/duplicate）；
                False（属性事件等）创建时即推 MQTT。
            defer_db: 是否延迟写库。True（跟踪事件）时创建阶段不写数据库，
                事件仅在跟踪完成并通过区域过滤后由 create_tracking_event
                首次落库为 initial/duplicate，保证事件列表只有三种状态。

        Returns:
            事件 ID，如果未发送则返回 None
        """
        # ✅ 调试日志：跟踪事件输出流程
        logger.debug(f"[EventOutput] emit() 被调用: type={event_type}, class_name={data.get('class_name', '')}, confidence={data.get('confidence', 0.0)}")
        logger.debug(f"[EventOutput]   self.enabled={self.enabled}")
        
        if not self.enabled:
            logger.debug(f"[EventOutput]   事件未启用，跳过")
            return None
        
        # 检查过滤条件（bypass_filter=True 时跳过：置信度/类别过滤面向模型
        # 检测输出，非模型类事件如占道告警的面积占比语义不同，不适用）
        class_name = data.get('class_name', '')
        confidence = data.get('confidence', 0.0)
        should_send = True if bypass_filter else self._should_send(class_name, confidence)
        logger.debug(f"[EventOutput]   过滤检查结果: should_send={should_send}, class_name={class_name}, confidence={confidence}, target_classes={self.filter_cfg.get('target_classes', [])}, min_confidence={self.filter_cfg.get('min_confidence', 0.0)}")
        if not should_send:
            logger.debug(f"[EventOutput]   未通过过滤，跳过")
            return None
        
        # 生成事件 ID
        event_id = str(uuid.uuid4())
        
        # 使用传入的 task_id 或默认值
        actual_task_id = task_id or self.task_id
        actual_algorithm = algorithm or self.algorithm
        
        # 处理图片：先保存到本地，再根据配置决定是否上传远程
        image_urls = {}
        local_image_path = None
        
        compressed_image = None
        logger.debug(f"[EventOutput]   图片处理: image is not None={image is not None}")
        logger.debug(f"[EventOutput]   local_enabled={getattr(self, 'local_enabled', False)}, image_outputters count={len(self.image_outputters)}")

        if image is not None:
            try:
                compressed_image = self._compress_image(image)
                logger.debug(f"[EventOutput]   图片压缩成功: {len(compressed_image)} bytes")

                # 本地保存（同步，需要 URL 填入 event）
                if getattr(self, 'local_enabled', False):
                    local_image_path = self._save_image_local(event_id, compressed_image)
                    if local_image_path:
                        local_image_url = f"http://{self.server_host}:{self.server_port}/{local_image_path}"
                        image_urls['local'] = local_image_url
                        logger.debug(f"[EventOutput]   图片本地保存成功: {local_image_path}")
                else:
                    logger.debug(f"[EventOutput]   本地存储已禁用，跳过本地保存")
                # 远程上传（MinIO/FTP）已改为异步执行，见 _async_io_task
            except Exception as e:
                logger.debug(f"[EventOutput] 图片处理失败: {e}")
                compressed_image = None
        else:
            logger.debug(f"[EventOutput]   未传入图片，跳过图片处理")
        
        # 构建事件
        event = {
            'event_id': event_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'type': event_type,
            'camera_id': self.camera_id,
            'camera_name': self._get_camera_name(),  # 摄像机真实名称（供第三方映射设备）
            'iot_product_key': self._get_camera_iot()[0],  # 物联网产品标识（按摄像机配置，供物联网解析）
            'iot_device_name': self._get_camera_iot()[1],  # 物联网设备名称（按摄像机配置，供物联网解析）
            'task_id': actual_task_id,
            'algorithm': actual_algorithm,
            'frame_id': frame_id,
            'data': data,
            'image_urls': image_urls,
            'metadata': metadata or {},
            'lifecycle': lifecycle,  # 跟踪事件状态机标记：True 时不做创建阶段 MQTT 首推
            'defer_db': defer_db,    # 延迟写库标记：True 时跟踪中不写库，仅在完成后落库
        }
        
        # 异步执行 I/O 密集型操作（远程上传、MQTT发送、数据库保存、WebSocket推送）
        # 避免网络 I/O 阻塞事件处理主循环导致事件队列积压
        self._io_executor.submit(self._async_io_task, event, compressed_image)

        return event_id

    def _async_io_task(self, event: Dict, compressed_image):
        """异步执行 I/O 密集型操作（在线程池中运行，不阻塞事件处理主循环）"""
        try:
            # 1. 远程上传（MinIO/FTP）
            if compressed_image is not None and self.image_outputters:
                for outputter in self.image_outputters:
                    try:
                        url = outputter.upload(event['event_id'], compressed_image, content_type='image/jpeg')
                        event['image_urls'][outputter.__class__.__name__] = url
                    except Exception as e:
                        logger.debug(f"[EventOutput] 图片上传失败 ({outputter.__class__.__name__}): {e}")

            # 2. MQTT 发送
            # 跟踪事件（lifecycle=True）只在状态切换为最终事件后推送（被去重不推送），
            # 属性事件等无状态机的事件仍在创建时立即推送
            if self.mqtt_outputter and not event.get('lifecycle'):
                try:
                    self.mqtt_outputter.send(event)
                except Exception as e:
                    logger.debug(f"[EventOutput] MQTT 发送失败: {e}")

            # 3. 数据库保存 + WebSocket 推送
            # 跟踪事件（defer_db=True）不在创建阶段写库，仅在跟踪完成并通过
            # 区域过滤后由 create_tracking_event 落库为 initial/duplicate，
            # 保证事件列表只有 initial/final/duplicate 三种状态。
            # 人员计数（type=people_counting）不落 events 表，仅保留 MQTT 直发
            # 与统计，避免产生事件记录。
            _is_counting = event.get('type') == 'people_counting'
            if not _is_counting and (self.save_to_db or event.get('task_id')) and not event.get('defer_db'):
                self._save_to_database(event)
        except Exception as e:
            logger.error(f"[EventOutput] 异步 I/O 任务失败: {e}")

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
            
            # zone 字段：entered_zones 写入独立列，current_zones 转逗号串作为 zone_name
            event_data = event.get('data', {}) or {}
            entered_zones = event_data.get('entered_zones') or []
            current_zones = event_data.get('current_zones') or []
            zone_name = ",".join(current_zones) if current_zones else ""
            # loitered_zones：生命周期内累计曾徘徊的区域（只增不减），写入独立列
            loitered_zones = event_data.get('loitered_zones') or []

            event_record = EventRecord(
                event_id=event['event_id'],
                task_id=event.get('task_id'),
                camera_id=event['camera_id'],
                algorithm=event.get('algorithm', ''),
                type=event['type'],
                class_name=event_data.get('class_name', ''),
                confidence=event_data.get('confidence', 0.0),
                bbox=event_data.get('bbox', []),
                local_image_url=image_urls.get('local', ''),
                minio_image_url=image_urls.get('MinIOOutputter', ''),
                ftp_image_url=image_urls.get('FTPOutputter', ''),
                local_gif_url=gif_urls.get('local', ''),
                minio_gif_url=gif_urls.get('minio', ''),
                ftp_gif_url=gif_urls.get('ftp', ''),
                metadata=event.get('metadata', {}),
                start_time=self._parse_datetime(event_data.get('start_time')),
                end_time=self._parse_datetime(event_data.get('end_time')),
                status=event_data.get('status', 'tracking_start'),
                entered_zones=entered_zones,
                zone_name=zone_name,
                loitered_zones=loitered_zones
            )
            task_manager.add_event(event_record)
            
            # 设置created_at（add_event未回写对象）
            if not event_record.created_at:
                event_record.created_at = datetime.now()
            
            # 通过WebSocket实时推送新事件到前端
            self._push_event_to_websocket(event_record)
            
        except Exception as e:
            logger.debug(f"[EventOutput] 保存事件到数据库失败: {e}")
    
    @staticmethod
    def _parse_datetime(value):
        """安全解析时间字符串或时间戳"""
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        # 支持float/int类型的时间戳（Unix timestamp）
        if isinstance(value, (float, int)):
            try:
                return datetime.fromtimestamp(value)
            except (ValueError, TypeError, OSError):
                return None
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
                'status': event_record.status,
                'entered_zones': event_record.entered_zones,
                'zone_name': event_record.zone_name,
                'loitered_zones': event_record.loitered_zones,
                'created_at': event_record.created_at.isoformat() + 'Z' if event_record.created_at else None
            }
            
            # 通过HTTP POST发送到Flask服务器，由服务器通过WebSocket广播
            resp = requests.post(
                f'http://{self.server_host}:{self.server_port}/api/events/push',
                json=event_data,
                timeout=2
            )
            logger.debug(f"[EventOutput] 事件已推送到驾驶舱: {event_record.event_id[:8]}... type={event_record.type}")
            
        except Exception as e:
            logger.debug(f"[EventOutput] 推送事件到驾驶舱失败: {e}")
    
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

    def has_required_zones(self) -> bool:
        """是否配置了必需区域（目标必须进入必需区域才产生事件）

        required_zones 由 TrackedObjectProcessor 从 zones_config 的 required 标志派生注入
        （单一真相源）。为空表示未配置必需区域，所有目标正常产生事件。
        """
        return bool(self.required_zones)

    def passes_required_zones(self, entered_zones: List[str]) -> bool:
        """判断目标是否满足 required_zones 过滤条件

        - required_zones 为空：不限制，所有目标都通过
        - required_zones 非空：目标进入过的区域与 required_zones 有交集才通过

        Args:
            entered_zones: 目标进入过的区域列表（TrackedObject.entered_zones，只增不减）

        Returns:
            bool: 是否通过 required_zones 过滤
        """
        if not self.required_zones:
            return True
        if not entered_zones:
            return False
        return bool(set(entered_zones) & set(self.required_zones))

    def create_tracking_event(self, event_id: str, status: str, data: Dict = None,
                              image=None, event_type: str = None,
                              dedup_status: str = None, dedup_reason: str = None) -> bool:
        """跟踪完成时创建事件记录（事件状态初始落库）

        跟踪过程中不写库（emit 以 defer_db=True 跳过），事件仅在跟踪完成并通过
        区域过滤后首次写入数据库，状态为 initial（待语义去重）或 duplicate（规则
        去重判重）。后续由去重流程切换为最终事件 final / 被去重 duplicate，
        保证事件列表只有 initial / final / duplicate 三种状态。

        - 图片压缩 + 本地保存 + 数据库 INSERT 同步执行：确保记录行先于
          final/duplicate 的状态切换存在，避免异步 UPDATE 命中空行。
        - 远程上传（MinIO/FTP）+ WebSocket 推送异步执行。

        Args:
            event_id: 事件 UUID
            status: 初始状态（initial / duplicate）
            data: 更新数据字典（含 start_time, end_time, bbox, entered_zones 等）
            image: 最终快照（RGB 格式 numpy 数组）
            event_type: 事件类型（tracking / zone / loitering）
            dedup_status: 去重状态（normal / duplicate）
            dedup_reason: 去重判定原因

        Returns:
            bool: True 表示记录已写入数据库
        """
        try:
            image_urls = {}
            compressed_image = None
            if image is not None:
                try:
                    compressed_image = self._compress_image(image)
                    if getattr(self, 'local_enabled', False):
                        local_path = self._save_image_local(event_id, compressed_image)
                        if local_path:
                            image_urls['local'] = f"http://{self.server_host}:{self.server_port}/{local_path}"
                except Exception as e:
                    logger.debug(f"[EventOutput] 创建事件图片处理失败: {e}")
                    compressed_image = None

            # 同步写库：确保行存在，供后续 final/duplicate 状态切换的 UPDATE 命中
            record = self._insert_tracking_record(
                event_id, status, data, image_urls, event_type, dedup_status, dedup_reason
            )

            # 异步：远程上传 + WebSocket 推送
            self._io_executor.submit(
                self._async_create_tracking_io,
                event_id, compressed_image, image_urls, record,
            )
            return True

        except Exception as e:
            logger.debug(f"[EventOutput] 创建跟踪事件失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _insert_tracking_record(self, event_id: str, status: str, data: Dict,
                                image_urls: Dict, event_type: str,
                                dedup_status: str, dedup_reason: str):
        """同步插入事件记录到数据库，返回 EventRecord"""
        from src.tasks.task_manager import TaskManager, EventRecord

        event_data = data or {}
        entered_zones = event_data.get('entered_zones') or []
        current_zones = event_data.get('current_zones') or []
        zone_name = ",".join(current_zones) if current_zones else ""
        loitered_zones = event_data.get('loitered_zones') or []

        record = EventRecord(
            event_id=event_id,
            task_id=self.task_id,
            camera_id=self.camera_id,
            algorithm=self.algorithm,
            type=event_type or 'tracking',
            class_name=event_data.get('class_name', ''),
            confidence=event_data.get('confidence', 0.0),
            bbox=event_data.get('bbox', []),
            local_image_url=image_urls.get('local', ''),
            minio_image_url=image_urls.get('MinIOOutputter', ''),
            ftp_image_url=image_urls.get('FTPOutputter', ''),
            metadata=event_data.get('metadata', {}),
            start_time=self._parse_datetime(event_data.get('start_time')),
            end_time=self._parse_datetime(event_data.get('end_time')),
            status=status,
            entered_zones=entered_zones,
            zone_name=zone_name,
            loitered_zones=loitered_zones,
            dedup_status=dedup_status or '',
            dedup_reason=dedup_reason or '',
        )
        TaskManager().add_event(record)
        if not record.created_at:
            record.created_at = datetime.now()
        return record

    def _async_create_tracking_io(self, event_id: str, compressed_image,
                                  image_urls: Dict, record):
        """异步执行创建事件记录的 I/O：远程上传 + WebSocket 推送"""
        try:
            if compressed_image is not None and self.image_outputters:
                for outputter in self.image_outputters:
                    try:
                        url = outputter.upload(event_id, compressed_image, content_type='image/jpeg')
                        image_urls[outputter.__class__.__name__] = url
                    except Exception as e:
                        logger.debug(f"[EventOutput] 事件图片上传失败 ({outputter.__class__.__name__}): {e}")
                # 更新远程图片 URL 到数据库与记录对象
                if any(k in image_urls for k in ('MinIOOutputter', 'FTPOutputter')):
                    from src.tasks.task_manager import TaskManager
                    TaskManager().update_event(event_id=event_id, image_urls=image_urls)
                    record.minio_image_url = image_urls.get('MinIOOutputter', record.minio_image_url)
                    record.ftp_image_url = image_urls.get('FTPOutputter', record.ftp_image_url)

            # WebSocket 推送前校验实际状态：异步去重可能已完成，避免 initial 覆盖 final
            try:
                from src.tasks.task_manager import TaskManager
                current = TaskManager().get_event_by_id(event_id)
                record.status = current.status if current is not None else record.status
            except Exception as e:
                logger.debug(f"[EventOutput] 查询推送状态失败，使用初始状态推送: {e}")
            self._push_event_to_websocket(record)

        except Exception as e:
            logger.error(f"[EventOutput] 创建事件异步 I/O 失败: {e}")

    def _get_gif_generator(self):
        """延迟初始化 GIF 生成器"""
        if self._gif_generator is None:
            try:
                from .gif_generator import GIFGenerator
                # 合并全局存储配置到 GIF 配置中
                gif_config = dict(self.gif_cfg)
                gif_config['storage'] = self.config.get('storage', {})
                self._gif_generator = GIFGenerator(gif_config)
            except Exception as e:
                logger.debug(f"[EventOutput] GIF 生成器初始化失败: {e}")
                self._gif_generator = None
        return self._gif_generator

    def generate_gif(self, event_id: str, frames: list,
                     bbox: tuple = None, trajectory_points: list = None) -> Optional[str]:
        """生成 GIF 并更新事件的 GIF URL

        Args:
            event_id: 事件 UUID
            frames: 帧图像列表（RGB 格式 numpy 数组）
            bbox: 目标边界框 (x1, y1, x2, y2)
            trajectory_points: 轨迹点列表（当前由 GIFGenerator 内部根据 bbox 生成）

        Returns:
            GIF 本地路径（成功）或 None（失败/未启用）
        """
        if not self.gif_enabled:
            return None

        gif_generator = self._get_gif_generator()
        if gif_generator is None:
            return None

        try:
            # 将纯图像列表转换为 GIFGenerator 期望的帧字典列表
            bbox_list = list(bbox) if bbox else [0, 0, 0, 0]
            gif_frames = []
            for img in frames:
                if img is None:
                    continue
                gif_frames.append({
                    'image': img,
                    'bbox': bbox_list,
                    'is_full_frame': True,
                })

            if not gif_frames:
                logger.debug(f"[EventOutput] GIF 帧序列为空，跳过生成: event_id={event_id[:8]}")
                return None

            # 生成输出文件名（基于 event_id）
            output_filename = f"{event_id[:8]}.gif"

            # 调用 GIFGenerator 生成 GIF
            gif_urls = gif_generator.generate(
                track_id=0,  # 不使用 track_id，由 output_filename 决定文件名
                frames=gif_frames,
                output_filename=output_filename
            )

            if not gif_urls:
                return None

            # 更新数据库中的 GIF URL（不覆盖事件状态：新状态机中状态由去重流程决定）
            from src.tasks.task_manager import TaskManager
            task_manager = TaskManager()
            task_manager.update_event(
                event_id=event_id,
                gif_urls=gif_urls,
            )

            local_path = gif_urls.get('local', '')
            logger.debug(f"[EventOutput] GIF 生成成功: event_id={event_id[:8]}, urls={list(gif_urls.keys())}")

            # ✅ 推送 gif_ready 到前端（驾驶舱 + 事件管理页面）
            # MQTT 不再在此补推：终态消息由事件处理器在 GIF 与 LLM 全部完成后统一推送一次
            self._push_gif_ready_to_websocket(event_id, gif_urls)

            return local_path if local_path else None

        except Exception as e:
            logger.debug(f"[EventOutput] GIF 生成失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _push_gif_ready_to_websocket(self, event_id: str, gif_urls: dict):
        """推送 GIF 完成通知到前端（gif_ready 事件）

        让前端实时收到 GIF 并更新已显示事件卡片的 GIF 缩略图。

        Args:
            event_id: 事件 UUID
            gif_urls: GIF URL 字典 {'local': ..., 'minio': ..., 'ftp': ...}
        """
        try:
            import requests
            payload = {
                'event_id': event_id,
                'local_gif_url': gif_urls.get('local', ''),
                'minio_gif_url': gif_urls.get('minio', ''),
                'ftp_gif_url': gif_urls.get('ftp', ''),
            }
            requests.post(
                f'http://{self.server_host}:{self.server_port}/api/events/gif_ready',
                json=payload,
                timeout=2
            )
        except Exception as e:
            logger.debug(f"[EventOutput] 推送 gif_ready 失败: {e}")

    def push_terminal_to_mqtt(self, event_id: str):
        """终态事件推送到 MQTT（每个事件只推一条）

        跟踪事件（lifecycle=True）创建时不推 MQTT，待 GIF 与 LLM（去重/描述）全部
        完成后，由事件处理器调用本方法推送唯一一条终态消息：
          - 正常事件（final）→ 完整数据（含 GIF URL、AI 描述）
          - 被大模型去重（duplicate）→ 标记 duplicate，供第三方撤销/忽略

        内部经 io 线程池异步执行，不阻塞调用线程。
        """
        if not self.mqtt_outputter:
            logger.warning(
                f"[EventOutput] MQTT 跳过终态推送：mqtt_outputter 未初始化, event_id={event_id[:8]}"
            )
            return
        self._io_executor.submit(self._push_terminal_task, event_id)

    def _push_terminal_task(self, event_id: str):
        """异步执行终态事件 MQTT 推送（在 io 线程池中运行）"""
        try:
            from src.tasks.task_manager import TaskManager
            task_manager = TaskManager()
            record = task_manager.get_event_by_id(event_id)
            if record is None:
                logger.warning(f"[EventOutput] MQTT 推送终态事件失败：未找到事件 {event_id[:8]}")
                return

            d = record.to_dict()
            # 仅终态事件推送：final（正常收尾）/ duplicate（被去重）
            # 非终态（initial / 异常时序）不推，避免第三方收到可能被去重的事件
            if d['status'] not in ('final', 'duplicate'):
                logger.warning(
                    f"[EventOutput] MQTT 跳过终态推送（状态非终态）: "
                    f"event_id={event_id[:8]}, status={d['status']}"
                )
                return

            payload = {
                'event_id': d['event_id'],
                'update_type': 'final' if d['status'] == 'final' else 'duplicate',
                'type': d['type'],                  # 事件类型（tracking/zone/loitering）
                'status': d['status'],              # 终态：final / duplicate
                'dedup_status': d['dedup_status'] or '',
                'dedup_reason': d['dedup_reason'] or '',
                'camera_id': d['camera_id'],
                'camera_name': self._get_camera_name(),  # 摄像机真实名称（供第三方映射设备）
                'iot_product_key': self._get_camera_iot()[0],  # 物联网产品标识（按摄像机配置，供物联网解析）
                'iot_device_name': self._get_camera_iot()[1],  # 物联网设备名称（按摄像机配置，供物联网解析）
                'task_id': d['task_id'],
                'algorithm': d['algorithm'],
                'class_name': d['class_name'],
                'confidence': d['confidence'],
                'bbox': d['bbox'],
                'image_urls': {                     # 图片存储地址
                    'local': d['local_image_url'],
                    'minio': d['minio_image_url'],
                    'ftp': d['ftp_image_url'],
                },
                'gif_urls': {                       # GIF 存储地址（终态时已生成）
                    'local': d['local_gif_url'],
                    'minio': d['minio_gif_url'],
                    'ftp': d['ftp_gif_url'],
                },
                'entered_zones': d['entered_zones'],
                'zone_name': d['zone_name'],
                'loitered_zones': d['loitered_zones'],
                'start_time': d['start_time'],
                'end_time': d['end_time'],
                'metadata': d['metadata'] or {},
                # AI 描述（终态时已生成；被去重事件为空）
                'ai_description': d['ai_description'],
                'ai_description_model': d['ai_description_model'],
                'ai_description_time': d['ai_description_time'],
            }
            # 边缘库比对结果：终态前已由 run_library_match 比对并写入事件 metadata，
            # 此处从库读取附加到 payload（人脸带人名/相似度，车牌带车牌号/命中记录）
            if d['status'] == 'final':
                metadata = d.get('metadata') or {}
                if 'face_match' in metadata:
                    payload['face_match'] = metadata['face_match']
                if 'plate_match' in metadata:
                    payload['plate_match'] = metadata['plate_match']
            self.mqtt_outputter.send(payload)
            logger.info(
                f"[EventOutput] MQTT 推送终态事件: event_id={event_id[:8]}, "
                f"status={d['status']}, update_type={payload['update_type']}, "
                f"gif_urls={[k for k, v in payload['gif_urls'].items() if v]}, "
                f"desc={str(d['ai_description'] or '')[:40]}"
            )
        except Exception as e:
            logger.warning(f"[EventOutput] MQTT 推送终态事件失败: {e}")

    def crop_library_image(self, image: np.ndarray, bbox: tuple) -> Optional[np.ndarray]:
        """按检测框外扩裁剪人脸/车牌抓拍小图（输入非法返回 None）

        外扩比例取 library_crop_margin（默认 0.2，四周各外扩框宽高的 20%），
        裁剪范围限制在图像内。裁剪出的干净小图（无绘制框）既作事件图，
        也作边缘识别+比对的输入。
        """
        if image is None or bbox is None or len(bbox) < 4:
            return None
        try:
            h, w = image.shape[:2]
            x1, y1, x2, y2 = (float(v) for v in bbox[:4])
        except Exception:
            return None
        bw, bh = x2 - x1, y2 - y1
        if bw <= 0 or bh <= 0:
            return None
        mx, my = bw * self.library_crop_margin, bh * self.library_crop_margin
        cx1 = max(0, int(x1 - mx))
        cy1 = max(0, int(y1 - my))
        cx2 = min(w, int(x2 + mx))
        cy2 = min(h, int(y2 + my))
        if cx2 <= cx1 or cy2 <= cy1:
            return None
        return image[cy1:cy2, cx1:cx2].copy()

    def library_match_kind(self, class_name: str) -> Optional[str]:
        """按类别名判断是否需要库比对：'face' / 'plate' / None

        任务未启用库比对、未配置边缘地址或该类未配库 ID 时返回 None。
        该类事件采用精简生成路径（不做区域过滤/去重/GIF，仅保留一张最优抓拍图）。
        """
        if self.library_client is None or not self.library_client.usable:
            return None
        return self.library_client.match_class(class_name)

    def run_library_match(self, event_id: str) -> Optional[Dict]:
        """对指定事件执行库比对，结果写入事件 metadata 并返回

        人脸：上传人脸图，边缘抽特征 + 余弦相似度比对
        车牌：上传车牌图，边缘 OCR 出车牌号，再按车牌号在库内匹配
        比对结果（人名/相似度 或 车牌号/命中记录）写入 metadata 的
        face_match / plate_match 键，事件页/驾驶舱/第三方推送统一从库读取。
        任何失败都不影响事件上报（仅记录日志）。

        Returns:
            比对结果字典 {'face_match': {...}} 或 {'plate_match': {...}}，未比对返回 None
        """
        if self.library_client is None or not self.library_client.usable:
            return None

        from src.tasks.task_manager import TaskManager
        d = {}
        try:
            record = TaskManager().get_event_by_id(event_id)
            d = record.to_dict() if record is not None else {}
        except Exception as e:
            logger.debug(f"[EventOutput] 库比对读取事件失败: event_id={event_id[:8]}, {e}")
            return None

        kind = self.library_client.match_class(d.get('class_name'))
        if kind is None:
            return None

        image_path = os.path.join(self.local_output_dir, f"{event_id}.jpg")
        try:
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
        except Exception as e:
            logger.debug(f"[EventOutput] 库比对读取抓拍图失败: event_id={event_id[:8]}, {e}")
            return None

        result_meta: Dict = {}
        try:
            if kind == 'face':
                result = self.library_client.match_face(image_bytes)
                if result is None:
                    return None
                best = result.get('best_match') or {}
                result_meta['face_match'] = {
                    'library_id': self.library_client.resolve_face_library(),
                    'library_name': self.library_match_cfg.get('face_library_name'),
                    'matched': bool(result.get('matched')),
                    'person_id': best.get('person_id'),
                    'person_name': best.get('person_name'),
                    'similarity': best.get('similarity'),
                    'top_score': result.get('top_score'),
                    'threshold': result.get('threshold'),
                }
            else:
                plate_no = self.library_client.recognize_plate(image_bytes)
                match = self.library_client.match_plate(plate_no) if plate_no else None
                result_meta['plate_match'] = {
                    'library_id': self.library_client.resolve_plate_library(),
                    'library_name': self.library_match_cfg.get('plate_library_name'),
                    'plate_no': plate_no,
                    'matched': bool((match or {}).get('matched')),
                    'match_type': (match or {}).get('match_type'),
                    'entry': (match or {}).get('entry'),
                }
            # 比对结果落库（合并进事件 metadata），事件页/驾驶舱/第三方推送统一从库读取
            try:
                TaskManager().update_event(event_id, metadata=result_meta)
            except Exception as e:
                logger.debug(f"[EventOutput] 库比对结果落库失败: event_id={event_id[:8]}, {e}")
            logger.info(f"[EventOutput] 库比对完成: event_id={event_id[:8]}, kind={kind}")
            return result_meta
        except Exception as e:
            logger.warning(f"[EventOutput] 库比对失败（不影响上报）: event_id={event_id[:8]}, {e}")
            return None

    def close(self):
        """关闭所有输出器"""
        # 关闭 GIF 生成器
        if self._gif_generator is not None:
            try:
                self._gif_generator.close()
            except Exception as e:
                logger.debug(f"[EventOutput] 关闭 GIF 生成器失败: {e}")

        # 关闭 MQTT 输出器
        if self.mqtt_outputter:
            try:
                self.mqtt_outputter.close()
            except Exception as e:
                logger.debug(f"[EventOutput] 关闭 MQTT 输出器失败: {e}")

        # 关闭 WebSocket 输出器
        # ✅ 修复：使用 getattr 避免未初始化属性导致 AttributeError，使后续清理被跳过
        if getattr(self, 'websocket_outputter', None):
            try:
                self.websocket_outputter.close()
            except Exception as e:
                logger.debug(f"[EventOutput] 关闭 WebSocket 输出器失败: {e}")

        # 关闭图片输出器
        for outputter in self.image_outputters:
            try:
                outputter.close()
            except Exception as e:
                logger.debug(f"[EventOutput] 关闭图片输出器失败 ({outputter.__class__.__name__}): {e}")

        # ✅ 修复：关闭异步 I/O 线程池，避免线程泄漏
        try:
            self._io_executor.shutdown(wait=False)
        except Exception as e:
            logger.debug(f"[EventOutput] 关闭 IO 线程池失败: {e}")

        # 最后一次清理本地图片
        if self.auto_cleanup:
            self._cleanup_old_images()

        logger.debug("[EventOutput] 已关闭所有输出器")
