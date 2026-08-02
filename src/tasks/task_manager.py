"""
任务管理器

管理检测任务和摄像机的数据库存储、查询和状态管理。
支持：
1. 一个检测任务关联多个摄像机（共享同一算法模型）
2. 同时运行多个检测任务
3. 每个任务独立的检测配置
"""

import json
import logging
import os
import sqlite3
import yaml
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from ..cameras.camera_manager import get_camera_manager

logger = logging.getLogger(__name__)


# ========================================
# 配置文件路径
# ========================================

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'config')

# 配置文件路径
DECODER_CONFIG_PATH = os.path.join(CONFIG_DIR, 'decoder_config.yaml')
DETECTOR_CONFIG_PATH = os.path.join(CONFIG_DIR, 'detector_config.yaml')
TRACKER_CONFIG_PATH = os.path.join(CONFIG_DIR, 'tracker_config.yaml')
MOTION_CONFIG_PATH = os.path.join(CONFIG_DIR, 'motion_config.yaml')
EVENT_CONFIG_PATH = os.path.join(CONFIG_DIR, 'event_output.yaml')
ZONES_CONFIG_PATH = os.path.join(CONFIG_DIR, 'zones_config.yaml')  # 新增：Zone区域配置

# ========================================
# 辅助函数
# ========================================

def load_yaml_config(config_path: str) -> Dict:
    """从 YAML 配置文件读取配置"""
    if not os.path.exists(config_path):
        logger.warning(f"配置文件不存在: {config_path}")
        return {}

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            return config if config else {}
    except Exception as e:
        logger.error(f"读取配置文件失败: {config_path}, 错误: {e}")
        return {}


def get_default_decoder_config() -> Dict:
    """
    获取默认解码器配置（从配置文件读取）

    优先级：
    1. YAML 配置文件中的值
    2. 硬编码的默认值
    """
    config = load_yaml_config(DECODER_CONFIG_PATH)
    decoder_config = config.get('decoder', {})

    # 如果配置文件中没有完整配置，补充默认值
    default_config = {
        # ========== 视频参数 ==========
        'width': decoder_config.get('width', 640),
        'height': decoder_config.get('height', 640),
        'fps': decoder_config.get('fps', 30),  # 新增：检测帧率

        # ========== 硬件加速配置 ==========
        'hwaccel_preset': decoder_config.get('hwaccel_preset', 'preset-dxva2'),  # 硬件加速预设（必须）

        # ========== 输入预设配置 ==========
        'input_preset': decoder_config.get('input_preset', 'preset-rtsp-generic'),  # 新增：输入预设

        # ========== 输出配置 ==========
        # output_format已删除，固定使用I420格式以优化性能

        # ========== RTSP 配置 ==========
        'rtsp_transport': decoder_config.get('rtsp_transport', 'tcp'),
        'reconnect': decoder_config.get('reconnect', True),
        'reconnect_delay': decoder_config.get('reconnect_delay', 5),
        'buffer_size': decoder_config.get('buffer_size', 2 * 1024 * 1024),  # 2MB

        # ========== 环形缓冲区配置（新架构）==========
        'ring_buffer_block_count': decoder_config.get('ring_buffer_block_count', 10),
        'index_queue_size': decoder_config.get('index_queue_size', 50),

        # ========== FFmpeg 滤镜参数（视频预处理）==========
        'ffmpeg_filters': decoder_config.get('ffmpeg_filters', []),  # FFmpeg 滤镜参数
    }

    return default_config


def get_default_detector_config() -> Dict:
    """获取默认检测器配置（从配置文件读取）

    预处理（NV12→RGB→Resize）由 region_utils.create_tensor_input 在检测器进程内完成，
    不再需要独立的 preprocessor 配置段。模型输入尺寸（input_width/input_height）由算法库提供，
    此处仅作为算法库未提供时的兜底默认值。
    """
    config = load_yaml_config(DETECTOR_CONFIG_PATH)

    detector_config = config.get('detector', {})

    default_config = {
        # 检测器配置
        'backend': detector_config.get('backend', 'onnx_tensorrt'),
        'use_fp16': detector_config.get('use_fp16', True),
        'conf_threshold': detector_config.get('conf_threshold', 0.25),
        'iou_threshold': detector_config.get('iou_threshold', 0.35),
        'min_box_area': detector_config.get('min_box_area', 50),
        'input_width': detector_config.get('input_width', 640),
        'input_height': detector_config.get('input_height', 640),
        'tensorrt_engine_cache_path': detector_config.get('tensorrt_engine_cache_path', './trt_cache'),
        'tensorrt_lib_path': detector_config.get('tensorrt_lib_path', ''),
        'device_id': detector_config.get('device_id', 0),
        'gpu_mem_limit': detector_config.get('gpu_mem_limit', 6442450944)
    }

    # 注意：target_classes 是任务表的单独字段，不从配置文件读取

    return default_config


def get_default_tracker_config() -> Dict:
    """获取默认跟踪器配置（从配置文件读取，Norfair 架构）"""
    config = load_yaml_config(TRACKER_CONFIG_PATH)

    # 注意：enabled 是任务表的单独字段（tracking_enabled），不从配置文件读取

    # 获取 tracker_type 和 debug_print_interval
    tracker_type = config.get('tracker_type', 'norfair')
    debug_print_interval = config.get('debug_print_interval', 0)

    # 获取 norfair 配置
    norfair_config = config.get('norfair', {})

    default_config = {
        # 注意：不包含 enabled 字段（enabled 是任务表的单独字段 tracking_enabled）
        'tracker_type': tracker_type,

        # 调试打印间隔配置
        'debug_print_interval': debug_print_interval,

        # Norfair 核心配置
        # n_init: 新轨迹确认帧数（映射到 NorfairTracker.min_initialized）
        # max_age: 最大丢失帧数（映射到 NorfairTracker.max_disappeared）
        'n_init': norfair_config.get('n_init', 3),
        'max_age': norfair_config.get('max_age', 30),
    }

    return default_config


def get_default_motion_config() -> Dict:
    """获取默认运动检测配置（从配置文件读取）"""
    config = load_yaml_config(MOTION_CONFIG_PATH)

    # 注意：enabled是任务表的单独字段（motion_enabled），不从配置文件读取

    # motion_config.yaml 的结构是顶层直接有字段，没有 'motion' 顶层键
    # 参数与 ImprovedMotionDetector（帧差法）的构造参数一一对应
    default_config = {
        # 注意：不包含 enabled 字段（enabled 是任务表的单独字段 motion_enabled）

        # 核心参数（与 ImprovedMotionDetector 构造参数对齐）
        'motion_height': config.get('motion_height', 180),         # 运动检测分辨率（降采样高度）
        'threshold': config.get('threshold', 25),                   # 运动阈值（0-255）
        'contour_area': config.get('contour_area', 200),            # 最小轮廓面积（像素）
        'improve_contrast': config.get('improve_contrast', False),  # 是否增强对比度（适应光照变化）
        'blur_radius': config.get('blur_radius', 1),                # 高斯滤波半径（降噪）
        'frame_alpha': config.get('frame_alpha', 0.01),             # 背景更新速率（0-1）
        'contrast_frame_history': config.get('contrast_frame_history', 50),  # 对比度历史帧数
        'lightning_threshold': config.get('lightning_threshold', 0.8),       # 闪电检测阈值（重新校准）
    }

    return default_config


def get_default_event_config() -> Dict:
    """获取默认事件输出配置（从配置文件读取）
    
    注意：
        - camera_id是任务表的camera字段，不从配置文件读取（每个摄像头ID不同）
        - storage、mqtt、websocket属于系统配置，从system_config.yaml读取，不在event_config中保存
    """
    config = load_yaml_config(EVENT_CONFIG_PATH)

    # event_output.yaml 的结构是所有字段都在 'event_output' 顶层键下
    event_output = config.get('event_output', {})

    default_config = {
        # 注意：不包含camera_id字段（camera_id在任务表的camera字段）
        
        # 注意：不包含storage、mqtt、websocket字段
        # 这些字段属于系统配置，从system_config.yaml读取

        'enabled': event_output.get('enabled', True),

        'metadata': event_output.get('metadata', {}),

        # 图片压缩配置
        'compression': event_output.get('compression', {
            'enabled': True,
            'quality': 80,
            'max_width': 640,
            'max_height': 480
        }),

        'image_output': event_output.get('image_output', {}),
        'gif': event_output.get('gif', {}),
        'trajectory': event_output.get('trajectory', {}),
        'capture': event_output.get('capture', {}),

        'filter': event_output.get('filter', {})
    }

    return default_config


def _normalize_storage_switches(cfg: Dict, defaults: Dict[str, bool]) -> Dict[str, bool]:
    """将旧版存储模式字段归一化为独立开关

    优先级：独立开关字段 > 旧版 method/output_method > 旧版 local_backup > 默认值

    Args:
        cfg: image_output 或 gif 配置字典
        defaults: 默认开关 {'local': bool, 'minio': bool, 'ftp': bool}

    Returns:
        归一化后的开关 {'local': bool, 'minio': bool, 'ftp': bool}
    """
    result = dict(defaults)

    if not cfg:
        return result

    # 1. 优先读取独立开关字段
    if cfg.get('local_enabled') is not None:
        result['local'] = bool(cfg.get('local_enabled'))
    if cfg.get('minio_enabled') is not None:
        result['minio'] = bool(cfg.get('minio_enabled'))
    if cfg.get('ftp_enabled') is not None:
        result['ftp'] = bool(cfg.get('ftp_enabled'))

    # 2. 向后兼容：旧版 method / output_method 字段
    legacy_method = cfg.get('method') or cfg.get('output_method')
    if legacy_method is not None and cfg.get('local_enabled') is None:
        mapping = {
            'both':  (True,  True,  True),
            'minio': (False, True,  False),
            'ftp':   (False, False, True),
            'local': (True,  False, False),
            'none':  (False, False, False),
        }
        result['local'], result['minio'], result['ftp'] = mapping.get(legacy_method, (True, True, True))

    # 3. 向后兼容：旧版 local_backup 字段（仅图片输出使用）
    if cfg.get('local_backup') is not None and cfg.get('local_enabled') is None and legacy_method is None:
        result['local'] = bool(cfg.get('local_backup'))

    return result


def normalize_event_config_storage(event_config: Dict) -> Dict:
    """归一化事件配置中的存储开关字段

    将旧版 method / output_method / local_backup 字段转换为独立开关
    （local_enabled / minio_enabled / ftp_enabled），并清理旧版字段，
    确保配置格式统一。返回深拷贝，不修改原配置。

    Args:
        event_config: 原始事件配置字典

    Returns:
        归一化后的事件配置字典
    """
    if not event_config:
        return event_config

    # 深拷贝，避免修改原对象
    result = json.loads(json.dumps(event_config))

    # 处理 image_output 段
    image_output = result.get('image_output', {})
    if image_output:
        switches = _normalize_storage_switches(image_output, {'local': True, 'minio': False, 'ftp': False})
        image_output['local_enabled'] = switches['local']
        image_output['minio_enabled'] = switches['minio']
        image_output['ftp_enabled'] = switches['ftp']
        # 清理旧版字段
        image_output.pop('method', None)
        image_output.pop('local_backup', None)
        result['image_output'] = image_output

    # 处理 gif 段
    gif = result.get('gif', {})
    if gif:
        switches = _normalize_storage_switches(gif, {'local': True, 'minio': False, 'ftp': False})
        gif['local_enabled'] = switches['local']
        gif['minio_enabled'] = switches['minio']
        gif['ftp_enabled'] = switches['ftp']
        # 清理旧版字段
        gif.pop('output_method', None)
        result['gif'] = gif

    return result


def get_default_display_config() -> Dict:
    """获取默认显示配置（从配置文件读取）"""
    DISPLAY_CONFIG_PATH = os.path.join(CONFIG_DIR, 'display_config.yaml')
    config = load_yaml_config(DISPLAY_CONFIG_PATH)

    # 读取所有显示相关配置
    display_config = {
        'display': config.get('display', {}),
        'detection_box': config.get('detection_box', {}),
        'track_box': config.get('track_box', {}),
        'trajectory': config.get('trajectory', {}),
        'motion_region': config.get('motion_region', {}),
        'alarm': config.get('alarm', {})
    }

    return display_config


def get_default_zones_config() -> List[Dict]:
    """
    获取默认Zone区域配置（从配置文件读取）

    Returns:
        List[Dict]: Zone配置列表，每个Zone包含：
            - name: 区域名称
            - polygon: 多边形顶点列表 [[x1,y1], [x2,y2], ...]
            - required: 是否为必需区域
            - color: BGR颜色 [B, G, R]
            - min_area: 最小区域面积
    """
    config = load_yaml_config(ZONES_CONFIG_PATH)

    # zones_config.yaml 的结构：
    # zones: [list of zone configs]
    # zone_detection: {enabled, send_enter_events, ...}

    zones = config.get('zones', [])

    if not zones:
        # 如果配置文件中没有定义zones，返回空列表
        return []

    # 验证每个zone的必需字段
    validated_zones = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue

        # 必需字段：name, polygon
        if 'name' not in zone or 'polygon' not in zone:
            logger.warning(f"Zone配置缺少必需字段，跳过: {zone}")
            continue

        # polygon必须是至少3个点的列表
        polygon = zone.get('polygon', [])
        if not isinstance(polygon, list) or len(polygon) < 3:
            logger.warning(f"Zone {zone['name']} 的polygon无效，跳过")
            continue

        # 构造标准化的zone配置
        validated_zone = {
            'name': zone['name'],
            'polygon': polygon,
            'required': zone.get('required', False),
            'color': zone.get('color', [0, 255, 0]),  # 默认绿色
            'min_area': zone.get('min_area', 0)
        }

        validated_zones.append(validated_zone)

    return validated_zones


# ========================================
# 辅助函数（解析）
# ========================================

def parse_datetime(value):
    if value is None or value == '':
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def safe_json_loads(value, default=None):
    """安全解析JSON字符串"""
    if value is None or value == '':
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


# ========================================
# 数据类定义
# ========================================

@dataclass
class DetectionTask:
    """检测任务数据类 - 包含检测配置（不含摄像机信息）"""
    id: Optional[int] = None
    task_name: str = ""
    enabled: bool = True
    status: str = "stopped"
    detection_type: str = "realtime"  # realtime（实时检测）, polling（轮询检测）
    status: str = "stopped"  # running, stopped, error

    # 检测算法配置
    algorithm: str = "yolov11"
    model_path: str = "models/yolov11n.onnx"
    model_type: str = "yolov11n"  # 模型类型字段（如 yolov11n, yolov8n, yolo11n）
    labels_file: str = ""  # 标签文件路径（创建任务时从算法库 labels_file 拷贝，独立字段避免嵌套 JSON）
    target_classes: List[str] = field(default_factory=list)  # 空列表表示不限制检测类别

    # 功能开关（独立字段，便于任务管理）
    motion_detection_enabled: bool = True  # 是否启用运动检测（默认启用）
    tracking_enabled: bool = True  # 是否启用跟踪功能（默认启用）

    # 解码器配置（JSON）- 从配置文件读取默认值
    decoder_config: Dict = field(default_factory=get_default_decoder_config)

    # 检测器配置（JSON）- 从配置文件读取默认值
    detector_config: Dict = field(default_factory=get_default_detector_config)

    # 跟踪器配置（JSON）- 从配置文件读取默认值
    tracker_config: Dict = field(default_factory=get_default_tracker_config)

    # 运动检测配置（JSON）- 从配置文件读取默认值
    motion_config: Dict = field(default_factory=get_default_motion_config)

    # 事件输出配置（JSON）- 从配置文件读取默认值
    event_config: Dict = field(default_factory=get_default_event_config)

    # 显示配置（JSON）- 测试工具专用，任务配置中使用空字典
    display_config: Dict = field(default_factory=dict)

    # 时间戳
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        def format_datetime(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value)
        
        return {
            'id': self.id,
            'task_name': self.task_name,
            'enabled': self.enabled,
            'status': self.status,
            'detection_type': self.detection_type,  # 检测类型（新增）
            'algorithm': self.algorithm,
            'model_path': self.model_path,
            'model_type': self.model_type,  # 模型类型字段
            'labels_file': self.labels_file,  # 标签文件路径
            'target_classes': self.target_classes,
            'motion_detection_enabled': self.motion_detection_enabled,
            'tracking_enabled': self.tracking_enabled,
            'decoder_config': self.decoder_config,
            'detector_config': self.detector_config,
            'tracker_config': self.tracker_config,
            'motion_config': self.motion_config,
            'event_config': normalize_event_config_storage(self.event_config),
            'display_config': self.display_config,
            'created_at': format_datetime(self.created_at),
            'updated_at': format_datetime(self.updated_at)
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'DetectionTask':
        """从字典创建"""
        return cls(
            id=data.get('id'),
            task_name=data.get('task_name', ''),
            enabled=data.get('enabled', True),
            status=data.get('status', 'stopped'),
            detection_type=data.get('detection_type', 'realtime'),  # 检测类型（新增）
            algorithm=data.get('algorithm', 'yolov11'),
            model_path=data.get('model_path', 'models/yolov11n.onnx'),
            model_type=data.get('model_type', 'yolov11n'),  # 模型类型字段
            labels_file=data.get('labels_file', ''),  # 标签文件路径
            target_classes=data.get('target_classes', []),  # 空列表表示不限制检测类别
            motion_detection_enabled=data.get('motion_detection_enabled', True),  # 默认启用
            tracking_enabled=data.get('tracking_enabled', True),
            decoder_config=data.get('decoder_config', get_default_decoder_config()),
            detector_config=data.get('detector_config', get_default_detector_config()),
            tracker_config=data.get('tracker_config', get_default_tracker_config()),
            motion_config=data.get('motion_config', get_default_motion_config()),
            event_config=data.get('event_config', get_default_event_config()),
            display_config=data.get('display_config', get_default_display_config()),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else None,
            updated_at=datetime.fromisoformat(data['updated_at']) if data.get('updated_at') else None
        )
    
    def get_full_config(self) -> Dict:
        """获取完整配置（用于任务执行器）"""
        return {
            'id': self.id,
            'task_name': self.task_name,
            'algorithm': self.algorithm,
            'model_path': self.model_path,
            'model_type': self.model_type,  # 模型类型字段
            'labels_file': self.labels_file,  # 标签文件路径（task_executor 经 task_config 取此值）
            'target_classes': self.target_classes,
            'motion_detection_enabled': self.motion_detection_enabled,
            'tracking_enabled': self.tracking_enabled,
            'decoder_config': self.decoder_config,
            'detector_config': self.detector_config,
            'tracker_config': self.tracker_config,
            'motion_config': self.motion_config,
            'event_config': normalize_event_config_storage(self.event_config),
            'display_config': self.display_config
        }


@dataclass
class TaskCameraBinding:
    """任务-摄像机关联数据类"""
    id: Optional[int] = None
    task_id: int = 0
    camera_id: str = ""  # 改为字符串类型（UUID）
    enabled: bool = True
    status: str = "stopped"  # running, stopped, error
    zones_config: List[Dict] = field(default_factory=list)  # ✅ 新增：Zone配置（通道级别）
    created_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'id': self.id,
            'task_id': self.task_id,
            'camera_id': self.camera_id,
            'enabled': self.enabled,
            'status': self.status,
            'zones_config': self.zones_config,  # ✅ 新增
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


@dataclass
class EventRecord:
    """事件记录数据类"""
    id: Optional[int] = None
    event_id: str = ""
    task_id: Optional[int] = None
    camera_id: str = ""
    algorithm: str = ""
    type: str = ""  # detection, track_start, track_end
    class_name: str = ""
    confidence: float = 0.0
    bbox: List[float] = field(default_factory=list)
    local_image_url: str = ""  # 本地图片路径
    minio_image_url: str = ""  # MinIO图片路径
    ftp_image_url: str = ""    # FTP图片路径
    local_gif_url: str = ""    # 本地GIF路径
    minio_gif_url: str = ""    # MinIO GIF路径
    ftp_gif_url: str = ""      # FTP GIF路径
    metadata: Dict = field(default_factory=dict)
    start_time: Optional[datetime] = None  # 跟踪起始时间
    end_time: Optional[datetime] = None    # 跟踪终止时间
    status: str = "tracking_start"  # 事件状态：tracking_start/tracking_update/tracking_end/gif_ready
    entered_zones: List[str] = field(default_factory=list)  # 目标进入过的区域（只增不减）
    zone_name: str = ""  # 当前所在区域（多个用逗号分隔）
    loitered_zones: List[str] = field(default_factory=list)  # 生命周期内累计曾徘徊的区域（只增不减）
    created_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        def format_datetime(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value)
        
        return {
            'id': self.id,
            'event_id': self.event_id,
            'task_id': self.task_id,
            'camera_id': self.camera_id,
            'algorithm': self.algorithm,
            'type': self.type,
            'class_name': self.class_name,
            'confidence': self.confidence,
            'bbox': self.bbox,
            'local_image_url': self.local_image_url,
            'minio_image_url': self.minio_image_url,
            'ftp_image_url': self.ftp_image_url,
            'local_gif_url': self.local_gif_url,
            'minio_gif_url': self.minio_gif_url,
            'ftp_gif_url': self.ftp_gif_url,
            'metadata': self.metadata,
            'start_time': format_datetime(self.start_time),
            'end_time': format_datetime(self.end_time),
            'status': self.status,
            'entered_zones': self.entered_zones,
            'zone_name': self.zone_name,
            'loitered_zones': self.loitered_zones,
            'created_at': format_datetime(self.created_at)
        }


# ========================================
# 任务管理器
# ========================================

class TaskManager:
    """任务管理器 - 支持多摄像机多任务"""
    
    def __init__(self, db_path: str = "data/tasks.db"):
        """
        初始化任务管理器
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        # 确保目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # ========================================
            # 检测任务表（不含摄像机信息）
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS detection_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name VARCHAR(100) NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    status VARCHAR(20) DEFAULT 'stopped',
                    detection_type VARCHAR(20) DEFAULT 'realtime',

                    -- 检测算法配置
                    algorithm VARCHAR(50) DEFAULT 'yolov11',
                    model_path VARCHAR(200) DEFAULT 'models/yolov11n.onnx',
                    model_type VARCHAR(50) DEFAULT 'yolov11n',  -- 模型类型字段
                    labels_file VARCHAR(500) DEFAULT '',  -- 标签文件路径（创建任务时从算法库拷贝）
                    target_classes TEXT DEFAULT '[]',  -- 空数组表示不限制检测类别

                    -- 功能开关
                    motion_detection_enabled BOOLEAN DEFAULT 1,  -- 默认启用运动检测
                    tracking_enabled BOOLEAN DEFAULT 1,  -- 默认启用跟踪功能,

                    -- 解码器配置（JSON）
                    decoder_config TEXT DEFAULT '{}',

                    -- 检测器配置（JSON）
                    detector_config TEXT DEFAULT '{}',

                    -- 跟踪器配置（JSON）
                    tracker_config TEXT DEFAULT '{}',

                    -- 运动检测配置（JSON）
                    motion_config TEXT DEFAULT '{}',

                    -- 事件输出配置（JSON）
                    event_config TEXT DEFAULT '{}',

                    -- 显示配置（JSON）
                    display_config TEXT DEFAULT '{}',

                    -- 时间戳
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # ========================================
            # 设备通道检测池表
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS device_channel_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_name VARCHAR(100) NOT NULL,
                    rtsp_url VARCHAR(200) NOT NULL,
                    channel_source VARCHAR(50) DEFAULT 'local_platform',
                    enabled INTEGER DEFAULT 1,
                    zones_config TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # ========================================
            # 任务-设备通道检测池关联表
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS task_device_channel_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    status VARCHAR(20) DEFAULT 'stopped',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES detection_tasks(id),
                    FOREIGN KEY (channel_id) REFERENCES device_channel_pool(id),
                    UNIQUE(task_id, channel_id)
                )
            ''')

            # ========================================
            # Zone 配置存储位置统一：通道级（device_channel_pool.zones_config）
            # ========================================
            # 确保 device_channel_pool 表有 zones_config 列（历史数据库可能缺失）
            cursor.execute("PRAGMA table_info(device_channel_pool)")
            dcp_cols = [row[1] for row in cursor.fetchall()]
            if 'zones_config' not in dcp_cols:
                cursor.execute("ALTER TABLE device_channel_pool ADD COLUMN zones_config TEXT")
                logger.info("[TaskManager] device_channel_pool 表添加 zones_config 字段")

            # 数据迁移：把任务-通道绑定表中的 zones_config 迁移到通道表（按 channel_id 取非空值）
            # 迁移后清空绑定表的 zones_config，使通道表成为唯一数据源
            try:
                # 取绑定表中每个 channel 的非空 zones_config（优先最后一个非空值）
                cursor.execute('''
                    SELECT channel_id, zones_config
                    FROM task_device_channel_bindings
                    WHERE zones_config IS NOT NULL AND zones_config != ''
                ''')
                migrated_rows = cursor.fetchall()
                if migrated_rows:
                    # 按 channel_id 聚合，保留最后一个非空值
                    channel_zones_map = {}
                    for ch_id, zc in migrated_rows:
                        if zc:
                            channel_zones_map[ch_id] = zc
                    for ch_id, zc in channel_zones_map.items():
                        cursor.execute(
                            "UPDATE device_channel_pool SET zones_config = ? WHERE id = ?",
                            (zc, ch_id)
                        )
                    # 清空绑定表的 zones_config（通道表已成为唯一数据源）
                    cursor.execute(
                        "UPDATE task_device_channel_bindings SET zones_config = NULL "
                        "WHERE zones_config IS NOT NULL"
                    )
                    logger.info(
                        f"[TaskManager] 已将 {len(channel_zones_map)} 个通道的 Zone 配置"
                        f"从 task_device_channel_bindings 迁移到 device_channel_pool"
                    )
            except Exception as e:
                logger.warning(f"[TaskManager] Zone 配置迁移失败，跳过: {e}")
            
            # ========================================
            # 摄像机-设备通道池关联表
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS camera_device_channel_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (camera_id) REFERENCES cameras(id),
                    FOREIGN KEY (channel_id) REFERENCES device_channel_pool(id),
                    UNIQUE(camera_id, channel_id)
                )
            ''')
            
            # ========================================
            # 事件表
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id VARCHAR(36) NOT NULL,
                    task_id INTEGER,
                    camera_id TEXT NOT NULL,
                    algorithm VARCHAR(50),
                    type VARCHAR(20) NOT NULL,
                    class_name VARCHAR(50),
                    confidence FLOAT,
                    bbox TEXT,
                    local_image_url VARCHAR(500),
                    minio_image_url VARCHAR(500),
                    ftp_image_url VARCHAR(500),
                    local_gif_url VARCHAR(500),
                    minio_gif_url VARCHAR(500),
                    ftp_gif_url VARCHAR(500),
                    metadata TEXT,
                    start_time DATETIME,
                    end_time DATETIME,
                    status VARCHAR(20) DEFAULT 'tracking_start',
                    entered_zones TEXT,
                    zone_name VARCHAR(200),
                    loitered_zones TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES detection_tasks(id)
                )
            ''')
            
            # 数据库迁移：如果旧表存在但缺少新字段，则添加
            cursor.execute("PRAGMA table_info(events)")
            columns = [col[1] for col in cursor.fetchall()]

            # 迁移：添加GIF存储路径字段
            if 'local_gif_url' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN local_gif_url VARCHAR(500)")
                logger.debug("[TaskManager] events 表添加 local_gif_url 字段")
            if 'minio_gif_url' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN minio_gif_url VARCHAR(500)")
                logger.debug("[TaskManager] events 表添加 minio_gif_url 字段")
            if 'ftp_gif_url' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN ftp_gif_url VARCHAR(500)")
                logger.debug("[TaskManager] events 表添加 ftp_gif_url 字段")
            
            # 迁移：添加 start_time 和 end_time 字段
            if 'start_time' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN start_time DATETIME")
                logger.debug("[TaskManager] events 表添加 start_time 字段")
            if 'end_time' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN end_time DATETIME")
                logger.debug("[TaskManager] events 表添加 end_time 字段")

            # ========================================
            # 迁移：统一 status 为独立列（废弃 metadata.status）
            # 三段式幂等迁移：建列 → 回填 → 清理
            # ========================================
            if 'status' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN status VARCHAR(20) DEFAULT 'tracking_start'")
                logger.debug("[TaskManager] events 表添加 status 字段")

            # 回填：从 metadata.status 复制到独立 status 列
            try:
                cursor.execute('''
                    UPDATE events
                    SET status = json_extract(metadata, '$.status')
                    WHERE json_extract(metadata, '$.status') IS NOT NULL
                      AND json_extract(metadata, '$.status') != ''
                ''')
                if cursor.rowcount > 0:
                    logger.debug(f"[TaskManager] 已从 metadata.status 回填 {cursor.rowcount} 条事件的 status 列")
            except Exception as e:
                logger.warning(f"json_extract 回填 status 失败，跳过: {e}")

            # 旧事件兜底：metadata 无 status 的行视为已结束
            try:
                cursor.execute('''
                    UPDATE events
                    SET status = 'tracking_end'
                    WHERE (json_extract(metadata, '$.status') IS NULL
                           OR json_extract(metadata, '$.status') = '')
                      AND (status IS NULL OR status = '' OR status = 'tracking_start')
                ''')
            except Exception as e:
                logger.warning(f"旧事件兜底回填 status 失败: {e}")

            # 清理：从 metadata JSON 中移除 status 键（统一到独立列）
            try:
                cursor.execute("SELECT id, metadata FROM events WHERE metadata LIKE '%\"status\"%'")
                rows_to_clean = cursor.fetchall()
                for row_id, meta_str in rows_to_clean:
                    try:
                        meta = json.loads(meta_str) if meta_str else {}
                        if 'status' in meta:
                            meta.pop('status', None)
                            cursor.execute('UPDATE events SET metadata = ? WHERE id = ?',
                                           (json.dumps(meta), row_id))
                    except (json.JSONDecodeError, TypeError):
                        continue
                if rows_to_clean:
                    logger.debug(f"[TaskManager] 已从 {len(rows_to_clean)} 条事件的 metadata 中清理 status 键")
            except Exception as e:
                logger.warning(f"清理 metadata.status 失败: {e}")

            # 迁移：添加 entered_zones 和 zone_name 字段（zone 检测独立列）
            if 'entered_zones' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN entered_zones TEXT")
                logger.debug("[TaskManager] events 表添加 entered_zones 字段")
            if 'zone_name' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN zone_name VARCHAR(200)")
                logger.debug("[TaskManager] events 表添加 zone_name 字段")

            # 迁移：添加 loitered_zones 字段（徘徊区域历史记录独立列）
            # 只增不减的累计集合，区别于瞬时 pending_loitering（后者塞在 metadata JSON 中）
            if 'loitered_zones' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN loitered_zones TEXT")
                logger.debug("[TaskManager] events 表添加 loitered_zones 字段")

            # ========================================
            # 算法注册表（保留表结构，避免旧触发器/外键引用失败）
            # 实际使用 algorithm_configs 表，此表仅作兼容性保留
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS algorithms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    algorithm_name VARCHAR(50) NOT NULL UNIQUE,
                    display_name VARCHAR(100),
                    description TEXT,
                    default_model VARCHAR(200),
                    supported_classes TEXT,
                    enabled BOOLEAN DEFAULT 1
                )
            ''')
            
            # ========================================
            # 创建索引
            # ========================================
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON detection_tasks(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_enabled ON detection_tasks(enabled)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_camera_id ON events(camera_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)')
            
            # ========================================
            # 数据库迁移：添加model_type字段
            # ========================================
            cursor.execute("PRAGMA table_info(detection_tasks)")
            columns = [column[1] for column in cursor.fetchall()]

            # 添加detection_type字段到detection_tasks表
            if 'detection_type' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN detection_type VARCHAR(20) DEFAULT 'realtime'")
                logger.info("数据库迁移: 添加detection_type字段到detection_tasks表")

            # ✓ 新增：添加model_type字段到detection_tasks表
            if 'model_type' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN model_type VARCHAR(50) DEFAULT 'yolov11n'")
                logger.info("数据库迁移: 添加model_type字段到detection_tasks表")

            # 添加 labels_file 字段到 detection_tasks 表（标签文件路径，创建任务时从算法库拷贝）
            if 'labels_file' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN labels_file VARCHAR(500) DEFAULT ''")
                logger.info("数据库迁移: 添加 labels_file 字段到 detection_tasks 表")
            
            # algorithms 表保留（不删除），避免旧触发器/外键引用失败
            # 实际算法配置已迁移到 algorithm_configs 表
            
            conn.commit()
            logger.debug(f"[TaskManager] 数据库初始化完成: {self.db_path}")
    
    # ========================================
    # 任务管理
    # ========================================
    
    def add_task(self, task: DetectionTask) -> int:
        """添加检测任务
        
        Args:
            task: 检测任务对象
        
        Returns:
            任务 ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            now = datetime.now()

            cursor.execute('''
                INSERT INTO detection_tasks
                (task_name, enabled, status, detection_type, algorithm, model_path, model_type, labels_file, target_classes,
                 motion_detection_enabled, tracking_enabled,
                 decoder_config, detector_config,
                 tracker_config, motion_config, event_config, display_config,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.task_name,
                task.enabled,
                task.status,
                task.detection_type,
                task.algorithm,
                task.model_path,
                task.model_type,  # 模型类型字段
                task.labels_file,  # 标签文件路径
                json.dumps(task.target_classes),
                task.motion_detection_enabled,
                task.tracking_enabled,
                json.dumps(task.decoder_config),
                json.dumps(task.detector_config),
                json.dumps(task.tracker_config),
                json.dumps(task.motion_config),
                json.dumps(task.event_config),
                json.dumps(task.display_config),
                now,
                now
            ))

            conn.commit()
            task_id = cursor.lastrowid
            logger.debug(f"[TaskManager] 添加任务成功: ID={task_id}, Name={task.task_name}")
            return task_id
    
    def get_task(self, task_id: int) -> Optional[DetectionTask]:
        """获取单个任务"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 明确指定列名查询，避免ALTER TABLE导致的列顺序问题
            cursor.execute('''SELECT id, task_name, enabled, status, detection_type,
                              algorithm, model_path, model_type, labels_file, target_classes,
                              motion_detection_enabled, tracking_enabled,
                              decoder_config, detector_config, tracker_config,
                              motion_config, event_config, display_config,
                              created_at, updated_at
                              FROM detection_tasks WHERE id = ?''', (task_id,))
            row = cursor.fetchone()

            if row:
                task = self._row_to_task(row)
                return task
            return None
    
    def get_all_tasks(self, enabled_only: bool = False, status: str = None) -> List[DetectionTask]:
        """获取所有任务

        Args:
            enabled_only: 只获取启用的任务
            status: 按状态筛选

        Returns:
            任务列表
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # 明确指定列名查询，避免ALTER TABLE导致的列顺序问题
            query = '''SELECT id, task_name, enabled, status, detection_type,
                       algorithm, model_path, model_type, labels_file, target_classes,
                       motion_detection_enabled, tracking_enabled,
                       decoder_config, detector_config, tracker_config,
                       motion_config, event_config, display_config,
                       created_at, updated_at
                       FROM detection_tasks WHERE 1=1'''
            params = []

            if enabled_only:
                query += ' AND enabled = 1'
            if status:
                query += ' AND status = ?'
                params.append(status)

            query += ' ORDER BY created_at DESC'

            cursor.execute(query, params)
            rows = cursor.fetchall()

            tasks = [self._row_to_task(row) for row in rows]

            return tasks
    
    def update_task(self, task_id: int, updates: Dict) -> bool:
        """更新任务"""
        # 调试日志：打印接收到的更新数据
        logger.debug(f"[TaskManager] 更新任务: ID={task_id}")
        logger.debug(f"[TaskManager] 接收到的更新字段: {list(updates.keys())}")
        if 'event_config' in updates:
            logger.debug(f"[TaskManager] event_config 内容: {json.dumps(updates['event_config'], ensure_ascii=False, indent=2)}")
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # JSON 字段列表
            json_fields = ['target_classes', 'decoder_config', 'detector_config',
                          'tracker_config', 'motion_config', 'event_config', 'display_config']

            updatable_fields = {
                'task_name': 'task_name',
                'enabled': 'enabled',
                'status': 'status',
                'detection_type': 'detection_type',
                'algorithm': 'algorithm',
                'model_path': 'model_path',
                'model_type': 'model_type',  # 模型类型字段支持更新
                'labels_file': 'labels_file',  # 标签文件路径支持更新
                'target_classes': 'target_classes',
                'motion_detection_enabled': 'motion_detection_enabled',
                'tracking_enabled': 'tracking_enabled',
                'decoder_config': 'decoder_config',
                'detector_config': 'detector_config',
                'tracker_config': 'tracker_config',
                'motion_config': 'motion_config',
                'event_config': 'event_config',
                'display_config': 'display_config'
            }
            
            set_clause = []
            params = []
            
            for field, column in updatable_fields.items():
                if field in updates:
                    set_clause.append(f'{column} = ?')
                    if field in json_fields:
                        params.append(json.dumps(updates[field]))
                    else:
                        params.append(updates[field])
            
            if not set_clause:
                return False
            
            set_clause.append('updated_at = ?')
            params.append(datetime.now())
            params.append(task_id)
            
            cursor.execute(
                f'UPDATE detection_tasks SET {", ".join(set_clause)} WHERE id = ?',
                params
            )
            
            conn.commit()
            success = cursor.rowcount > 0
            
            if success:
                logger.debug(f"[TaskManager] 更新任务成功: ID={task_id}")
            else:
                logger.debug(f"[TaskManager] 更新任务失败: ID={task_id}")
            
            return success
    
    def delete_task(self, task_id: int) -> bool:
        """删除任务（保留关联的事件记录）"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 删除任务-设备通道关联
            cursor.execute('DELETE FROM task_device_channel_bindings WHERE task_id = ?', (task_id,))
            
            cursor.execute('DELETE FROM detection_tasks WHERE id = ?', (task_id,))
            conn.commit()
            
            success = cursor.rowcount > 0
            if success:
                logger.debug(f"[TaskManager] 删除任务成功: ID={task_id}")
            
            return success
    
    def _row_to_task(self, row: tuple) -> DetectionTask:
        """将数据库行转换为 DetectionTask 对象"""
        # 安全解析 JSON 字段（不补充默认值，完全使用数据库配置）
        def safe_json_loads(value, default):
            """解析JSON并返回（不补充默认值）"""
            if not value:
                return default
            try:
                loaded = json.loads(value)
                if isinstance(loaded, (dict, list)):
                    return loaded
                return default
            except (json.JSONDecodeError, TypeError):
                return default

        # 安全解析JSON并补充默认值（用于需要向后兼容的配置）
        def safe_json_loads_with_defaults(value, default):
            """解析JSON并补充默认值（用于字典类型）"""
            if not value:
                return default
            try:
                loaded = json.loads(value)
                if not isinstance(loaded, dict):
                    return default
                # 补充默认值中不存在的新字段
                result = default.copy()
                result.update(loaded)
                return result
            except (json.JSONDecodeError, TypeError):
                return default
        
        # 安全解析列表类型的 JSON 字段
        def safe_json_loads_list(value, default):
            """解析JSON列表并返回（用于列表类型）"""
            if not value:
                return default
            try:
                loaded = json.loads(value)
                if isinstance(loaded, list):
                    return loaded
                return default
            except (json.JSONDecodeError, TypeError):
                return default
        
        return DetectionTask(
            id=row[0],
            task_name=row[1] or '',
            enabled=row[2],
            status=row[3] or 'stopped',
            detection_type=row[4] if len(row) > 4 else 'realtime',  # 检测类型字段
            algorithm=row[5] if len(row) > 5 else 'yolov11',
            model_path=row[6] if len(row) > 6 else 'models/yolov11n.onnx',
            model_type=row[7] if len(row) > 7 and row[7] else 'yolov11n',  # 模型类型字段
            labels_file=row[8] if len(row) > 8 and row[8] else '',  # 标签文件路径（索引 8）
            target_classes=safe_json_loads_list(row[9] if len(row) > 9 else None, []),  # 索引 9，空列表表示不限制
            motion_detection_enabled=row[10] if len(row) > 10 else True,  # 索引 10，默认启用
            tracking_enabled=row[11] if len(row) > 11 else True,  # 索引 11
            decoder_config=safe_json_loads_with_defaults(row[12] if len(row) > 12 else None, get_default_decoder_config()),  # 索引 12
            detector_config=safe_json_loads_with_defaults(row[13] if len(row) > 13 else None, get_default_detector_config()),  # 索引 13
            tracker_config=safe_json_loads_with_defaults(row[14] if len(row) > 14 else None, get_default_tracker_config()),  # 索引 14
            motion_config=safe_json_loads_with_defaults(row[15] if len(row) > 15 else None, get_default_motion_config()),  # 索引 15
            event_config=safe_json_loads_with_defaults(row[16] if len(row) > 16 else None, get_default_event_config()),  # 索引 16
            display_config=safe_json_loads(row[17] if len(row) > 17 else None, get_default_display_config()),  # 索引 17
            created_at=parse_datetime(row[18] if len(row) > 18 else None),  # 索引 18
            updated_at=parse_datetime(row[19] if len(row) > 19 else None)  # 索引 19
        )
    
    # ========================================
    # 事件管理
    # ========================================
    
    def add_event(self, event: EventRecord) -> int:
        """添加事件"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO events
                (event_id, task_id, camera_id, algorithm, type, class_name,
                 confidence, bbox, local_image_url, minio_image_url, ftp_image_url,
                 local_gif_url, minio_gif_url, ftp_gif_url,
                 metadata, start_time, end_time, status, entered_zones, zone_name, loitered_zones, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                event.event_id,
                event.task_id,
                event.camera_id,
                event.algorithm,
                event.type,
                event.class_name,
                event.confidence,
                json.dumps(event.bbox),
                event.local_image_url,
                event.minio_image_url,
                event.ftp_image_url,
                event.local_gif_url,
                event.minio_gif_url,
                event.ftp_gif_url,
                json.dumps(event.metadata),
                event.start_time,
                event.end_time,
                event.status,
                json.dumps(event.entered_zones),
                event.zone_name,
                json.dumps(event.loitered_zones),
                datetime.now()
            ))
            
            conn.commit()
            return cursor.lastrowid

    def update_event(self, event_id: str, event_type: str = None,
                     metadata: Dict = None, image_urls: Dict = None,
                     gif_urls: Dict = None, start_time=None, end_time=None,
                     bbox: List[float] = None, confidence: float = None,
                     class_name: str = None,
                     status: str = None,
                     entered_zones: List[str] = None,
                     zone_name: str = None,
                     loitered_zones: List[str] = None) -> bool:
        """更新事件记录

        支持部分更新：仅更新提供的字段，未提供的字段保持不变。

        Args:
            event_id: 事件 UUID
            event_type: 事件类型（detection/track_start/track_end/tracking/tracking_end）
            metadata: 元数据字典（合并到现有 metadata，不再承载 status）
            image_urls: 图片 URL 字典 {'local': ..., 'MinIOOutputter': ..., 'FTPOutputter': ...}
            gif_urls: GIF URL 字典 {'local': ..., 'minio': ..., 'ftp': ...}
            start_time: 跟踪起始时间
            end_time: 跟踪终止时间
            bbox: 检测框坐标
            confidence: 置信度
            class_name: 类别名称
            status: 事件状态（tracking_start/tracking_update/tracking_end/gif_ready），写入独立 status 列
            entered_zones: 目标进入过的区域列表（只增不减）
            zone_name: 当前所在区域（多个用逗号分隔）
            loitered_zones: 生命周期内累计曾徘徊的区域列表（只增不减，写入独立列）

        Returns:
            是否更新成功
        """
        updates = []
        params = []

        if event_type is not None:
            updates.append('type = ?')
            params.append(event_type)

        if status is not None:
            updates.append('status = ?')
            params.append(status)

        if entered_zones is not None:
            updates.append('entered_zones = ?')
            params.append(json.dumps(entered_zones))

        if zone_name is not None:
            updates.append('zone_name = ?')
            params.append(zone_name)

        if loitered_zones is not None:
            updates.append('loitered_zones = ?')
            params.append(json.dumps(loitered_zones))

        if start_time is not None:
            updates.append('start_time = ?')
            params.append(start_time)

        if end_time is not None:
            updates.append('end_time = ?')
            params.append(end_time)

        if bbox is not None:
            updates.append('bbox = ?')
            params.append(json.dumps(bbox))

        if confidence is not None:
            updates.append('confidence = ?')
            params.append(confidence)

        if class_name is not None:
            updates.append('class_name = ?')
            params.append(class_name)

        if image_urls:
            if image_urls.get('local'):
                updates.append('local_image_url = ?')
                params.append(image_urls['local'])
            if image_urls.get('MinIOOutputter'):
                updates.append('minio_image_url = ?')
                params.append(image_urls['MinIOOutputter'])
            if image_urls.get('FTPOutputter'):
                updates.append('ftp_image_url = ?')
                params.append(image_urls['FTPOutputter'])

        if gif_urls:
            if gif_urls.get('local'):
                updates.append('local_gif_url = ?')
                params.append(gif_urls['local'])
            if gif_urls.get('minio'):
                updates.append('minio_gif_url = ?')
                params.append(gif_urls['minio'])
            if gif_urls.get('ftp'):
                updates.append('ftp_gif_url = ?')
                params.append(gif_urls['ftp'])

        if metadata is not None:
            # ✅ 合并 metadata 而非替换（避免覆盖已有的 track_id, center 等字段）
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT metadata FROM events WHERE event_id = ?', (event_id,))
                row = cursor.fetchone()
            existing_metadata = {}
            if row and row[0]:
                try:
                    existing_metadata = json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    existing_metadata = {}
            if isinstance(metadata, dict):
                existing_metadata.update(metadata)
            updates.append('metadata = ?')
            params.append(json.dumps(existing_metadata))

        if not updates:
            return False

        params.append(event_id)
        query = f"UPDATE events SET {', '.join(updates)} WHERE event_id = ?"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0

    def get_events(
        self,
        task_id: int = None,
        camera_id: str = None,
        algorithm: str = None,
        event_type: str = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[EventRecord]:
        """获取事件列表"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # 明确指定列名查询，避免ALTER TABLE导致的列顺序问题
            query = '''SELECT id, event_id, task_id, camera_id, algorithm, type, class_name,
                       confidence, bbox, local_image_url, minio_image_url, ftp_image_url,
                       local_gif_url, minio_gif_url, ftp_gif_url,
                       metadata, start_time, end_time, status, entered_zones, zone_name, loitered_zones, created_at FROM events WHERE 1=1'''
            params = []

            if task_id:
                query += ' AND task_id = ?'
                params.append(task_id)
            if camera_id:
                query += ' AND camera_id = ?'
                params.append(camera_id)
            if algorithm:
                query += ' AND algorithm = ?'
                params.append(algorithm)
            if event_type:
                query += ' AND type = ?'
                params.append(event_type)

            query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])

            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [self._row_to_event(row) for row in rows]

    def get_event_count(
        self,
        task_id: int = None,
        camera_id: str = None,
        algorithm: str = None,
        event_type: str = None
    ) -> int:
        """获取事件数量"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            query = 'SELECT COUNT(*) FROM events WHERE 1=1'
            params = []

            if task_id:
                query += ' AND task_id = ?'
                params.append(task_id)
            if camera_id:
                query += ' AND camera_id = ?'
                params.append(camera_id)
            if algorithm:
                query += ' AND algorithm = ?'
                params.append(algorithm)
            if event_type:
                query += ' AND type = ?'
                params.append(event_type)

            cursor.execute(query, params)
            return cursor.fetchone()[0]
    
    def clear_old_events(self, days: int = 7) -> int:
        """清理旧事件"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                'DELETE FROM events WHERE created_at < datetime("now", ?)',
                (f'-{days} days',)
            )
            
            conn.commit()
            deleted = cursor.rowcount
            logger.debug(f"[TaskManager] 清理了 {deleted} 条超过 {days} 天的事件")
            return deleted
    
    def _row_to_event(self, row: tuple) -> EventRecord:
        """将数据库行转换为 EventRecord 对象"""
        # 安全解析 JSON 字段
        def safe_json_loads(value, default):
            if not value:
                return default
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return default
        
        # 解析时间字段
        def parse_datetime(value):
            if not value:
                return None
            try:
                if isinstance(value, datetime):
                    return value
                return datetime.fromisoformat(str(value))
            except (ValueError, TypeError):
                try:
                    return datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError):
                    return value  # 保持原字符串
        
        created_at = parse_datetime(row[22])  # created_at 在 index 22（loitered_zones=21, zone_name=20）
        start_time = parse_datetime(row[16])  # start_time 在 index 16
        end_time = parse_datetime(row[17])    # end_time 在 index 17

        return EventRecord(
            id=row[0],
            event_id=row[1],
            task_id=row[2],
            camera_id=row[3],
            algorithm=row[4],
            type=row[5],
            class_name=row[6],
            confidence=row[7],
            bbox=safe_json_loads(row[8], []),
            local_image_url=row[9] or '',
            minio_image_url=row[10] or '',
            ftp_image_url=row[11] or '',
            local_gif_url=row[12] or '',
            minio_gif_url=row[13] or '',
            ftp_gif_url=row[14] or '',
            metadata=safe_json_loads(row[15], {}),
            start_time=start_time,
            end_time=end_time,
            status=row[18] or 'tracking_start',
            entered_zones=safe_json_loads(row[19], []),
            zone_name=row[20] or '',
            loitered_zones=safe_json_loads(row[21], []),
            created_at=created_at
        )
    
    # ========================================
    # 算法管理
    # ========================================
    
    def get_algorithms(self) -> List[Dict]:
        """获取可用算法列表（从算法库获取）"""
        from ..algorithms.algorithm_manager import get_algorithm_manager
        
        manager = get_algorithm_manager()
        algorithms = manager.get_all_algorithms()
        
        return [{
            'algorithm_name': algo.name,
            'display_name': algo.name,
            'description': algo.description or '',
            'default_model': algo.model_path,
            'supported_classes': algo.target_classes,
            'model_format': algo.model_format,
            'input_size': algo.input_size,
            'labels_file': algo.labels_file
        } for algo in algorithms if algo.status == 'active']
    
    # ========================================
    # 统计信息
    # ========================================
    
    def get_statistics(self) -> Dict:
        """获取系统统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 任务统计
            cursor.execute('SELECT COUNT(*) FROM detection_tasks')
            total_tasks = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM detection_tasks WHERE status = "running"')
            running_tasks = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM detection_tasks WHERE enabled = 1')
            enabled_tasks = cursor.fetchone()[0]
            
            # 绑定统计
            cursor.execute('SELECT COUNT(*) FROM task_device_channel_bindings')
            total_bindings = cursor.fetchone()[0]
            
            # 事件统计
            cursor.execute('SELECT COUNT(*) FROM events')
            total_events = cursor.fetchone()[0]
        
        # 摄像机统计（从 camera_manager 获取）
        try:
            camera_manager = get_camera_manager()
            camera_stats = camera_manager.get_statistics()
            total_cameras = camera_stats.get('total', 0)
            enabled_cameras = camera_stats.get('enabled', 0)
        except Exception as e:
            logger.debug(f"[TaskManager] 获取摄像机统计失败: {e}")
            total_cameras = 0
            enabled_cameras = 0
        
        return {
            'total_tasks': total_tasks,
            'running_tasks': running_tasks,
            'enabled_tasks': enabled_tasks,
            'total_cameras': total_cameras,
            'enabled_cameras': enabled_cameras,
            'total_bindings': total_bindings,
            'total_events': total_events
        }
    
    # ========================================
    # Zone配置管理（通道级别，存储于 device_channel_pool.zones_config）
    # ========================================

    def get_channel_zones(self, channel_id: int) -> List[Dict]:
        """
        获取通道的Zone配置（通道级，与任务无关）

        Args:
            channel_id: 通道ID（device_channel_pool.id）

        Returns:
            Zone配置列表
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 强制 WAL checkpoint，确保读到其他进程（如 Flask API）的最新写入
            try:
                cursor.execute("PRAGMA wal_checkpoint(FULL)")
            except Exception:
                pass

            cursor.execute(
                "SELECT zones_config FROM device_channel_pool WHERE id = ?",
                (channel_id,)
            )

            row = cursor.fetchone()

            if row and row[0]:
                try:
                    zones = json.loads(row[0])
                    return zones if isinstance(zones, list) else []
                except (json.JSONDecodeError, TypeError):
                    return []

            return []

    def save_channel_zones(self, channel_id: int, zones_config: List[Dict]) -> bool:
        """
        保存通道的Zone配置（通道级，与任务无关）

        Args:
            channel_id: 通道ID（device_channel_pool.id）
            zones_config: Zone配置列表

        Returns:
            是否保存成功
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # 检查通道是否存在
                cursor.execute(
                    "SELECT id FROM device_channel_pool WHERE id = ?",
                    (channel_id,)
                )

                if not cursor.fetchone():
                    logger.error(f"通道不存在: channel_id={channel_id}")
                    return False

                # 更新通道级 Zone 配置
                cursor.execute(
                    "UPDATE device_channel_pool SET zones_config = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(zones_config), channel_id)
                )

                conn.commit()
                # 强制 WAL checkpoint，确保其他进程（任务子进程）能立即读到最新配置
                try:
                    cursor.execute("PRAGMA wal_checkpoint(FULL)")
                except Exception:
                    pass
                logger.info(f"Zone配置已保存: channel_id={channel_id}, zones_count={len(zones_config)}")
                return True

        except Exception as e:
            logger.error(f"保存Zone配置失败: {e}")
            return False

    def delete_channel_zones(self, channel_id: int) -> bool:
        """
        删除通道的Zone配置（重置为空）

        Args:
            channel_id: 通道ID（device_channel_pool.id）

        Returns:
            是否删除成功
        """
        return self.save_channel_zones(channel_id, [])


# 全局任务管理器实例
_task_manager = None


def get_task_manager() -> TaskManager:
    """获取任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager