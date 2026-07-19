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
        'output_format': decoder_config.get('output_format', 'auto'),

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
    """获取默认检测器配置（从配置文件读取）"""
    config = load_yaml_config(DETECTOR_CONFIG_PATH)

    # 合并 preprocessor 和 detector 配置
    preprocessor_config = config.get('preprocessor', {})
    detector_config = config.get('detector', {})

    default_config = {
        # 预处理器配置
        'width': preprocessor_config.get('width', 640),
        'height': preprocessor_config.get('height', 640),
        'model_input_size': preprocessor_config.get('model_input_size', 640),
        'rgb_mean': preprocessor_config.get('rgb_mean', [0.0, 0.0, 0.0]),
        'rgb_std': preprocessor_config.get('rgb_std', [1.0, 1.0, 1.0]),

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

    # 注意：target_classes是任务表的单独字段，不从配置文件读取

    return default_config


def get_default_tracker_config() -> Dict:
    """获取默认跟踪器配置（从配置文件读取）"""
    config = load_yaml_config(TRACKER_CONFIG_PATH)

    # 注意：enabled是任务表的单独字段（tracking_enabled），不从配置文件读取

    # 获取 tracker_type 和 debug_print_interval
    tracker_type = config.get('tracker_type', 'botsort')
    debug_print_interval = config.get('debug_print_interval', 0)

    # 获取 botsort 配置
    botsort_config = config.get('botsort', {})

    default_config = {
        # 注意：不包含enabled字段（enabled是任务表的单独字段tracking_enabled）
        'tracker_type': tracker_type,

        # ✅ 添加调试打印间隔配置
        'debug_print_interval': debug_print_interval,

        # botsort配置
        'track_thresh': botsort_config.get('track_thresh', 0.30),
        'low_score_thresh': botsort_config.get('low_score_thresh', 0.10),
        'match_thresh': botsort_config.get('match_thresh', 0.50),
        'max_age': botsort_config.get('max_age', 45),
        'n_init': botsort_config.get('n_init', 2),
        'max_history_length': botsort_config.get('max_history_length', 30),
        'use_reid': botsort_config.get('use_reid', False),
        'reid_model_type': botsort_config.get('reid_model_type', 'none'),
        'reid_model_path': botsort_config.get('reid_model_path', ''),
        'reid_thresh': botsort_config.get('reid_thresh', 0.3),
        'device': botsort_config.get('device', 'cpu'),
        'half': botsort_config.get('half', False)
    }

    return default_config


def get_default_motion_config() -> Dict:
    """获取默认运动检测配置（从配置文件读取）"""
    config = load_yaml_config(MOTION_CONFIG_PATH)

    # 注意：enabled是任务表的单独字段（motion_enabled），不从配置文件读取

    # motion_config.yaml 的结构是顶层直接有字段，没有 'motion' 顶层键
    # 需要读取 fusion 子配置
    fusion_config = config.get('fusion', {})

    default_config = {
        # 注意：不包含enabled字段（enabled是任务表的单独字段motion_enabled）

        # 核心参数
        'history': config.get('history', 500),
        'var_threshold': config.get('var_threshold', 16.0),
        'detect_shadows': config.get('detect_shadows', False),
        'min_area': config.get('min_area', 500),
        'blur_size': config.get('blur_size', 5),
        'morph_size': config.get('morph_size', 5),
        'learning_rate': config.get('learning_rate', -1),
        'motion_history_length': config.get('motion_history_length', 10),
        'init_threshold': config.get('init_threshold', 30),
        'foreground_threshold': config.get('foreground_threshold', 0.3),
        'max_motion_regions': config.get('max_motion_regions', 20),

        # ✅ 添加可视化配置（固定，不从前端读取）
        'show_visualization': config.get('show_visualization', False),

        # 融合策略配置
        'confidence_boost': fusion_config.get('confidence_boost', 0.15),
        'confidence_penalty': fusion_config.get('confidence_penalty', 0.05),

        # ✅ 添加ReID配置（固定，不从前端读取）
        'use_for_reid': fusion_config.get('use_for_reid', True)
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
        'debug_info': config.get('debug_info', {})
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
    model_type: str = "yolov11n"  # 新增：模型类型字段（如 yolov11n, yolov8n, yolo11n）
    target_classes: List[str] = field(default_factory=lambda: ["person", "car"])

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

    # Zone区域配置（JSON）- 任务级别默认配置
    zones_config: List[Dict] = field(default_factory=list)

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
            'model_type': self.model_type,  # 新增：模型类型字段
            'target_classes': self.target_classes,
            'motion_detection_enabled': self.motion_detection_enabled,
            'tracking_enabled': self.tracking_enabled,
            'decoder_config': self.decoder_config,
            'detector_config': self.detector_config,
            'tracker_config': self.tracker_config,
            'motion_config': self.motion_config,
            'event_config': self.event_config,
            'display_config': self.display_config,
            'zones_config': self.zones_config,  # 新增：Zone区域配置
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
            model_type=data.get('model_type', 'yolov11n'),  # 新增：模型类型字段
            target_classes=data.get('target_classes', ['person', 'car']),
            motion_detection_enabled=data.get('motion_detection_enabled', True),  # 默认启用
            tracking_enabled=data.get('tracking_enabled', True),
            decoder_config=data.get('decoder_config', get_default_decoder_config()),
            detector_config=data.get('detector_config', get_default_detector_config()),
            tracker_config=data.get('tracker_config', get_default_tracker_config()),
            motion_config=data.get('motion_config', get_default_motion_config()),
            event_config=data.get('event_config', get_default_event_config()),
            display_config=data.get('display_config', get_default_display_config()),
            zones_config=data.get('zones_config', []),  # 新增：Zone区域配置
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
            'model_type': self.model_type,  # 新增：模型类型字段
            'target_classes': self.target_classes,
            'motion_detection_enabled': self.motion_detection_enabled,
            'tracking_enabled': self.tracking_enabled,
            'decoder_config': self.decoder_config,
            'detector_config': self.detector_config,
            'tracker_config': self.tracker_config,
            'motion_config': self.motion_config,
            'event_config': self.event_config,
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
                    model_type VARCHAR(50) DEFAULT 'yolov11n',  -- 新增：模型类型字段
                    target_classes TEXT DEFAULT '["person", "car"]',

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
                print("[TaskManager] events 表添加 local_gif_url 字段")
            if 'minio_gif_url' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN minio_gif_url VARCHAR(500)")
                print("[TaskManager] events 表添加 minio_gif_url 字段")
            if 'ftp_gif_url' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN ftp_gif_url VARCHAR(500)")
                print("[TaskManager] events 表添加 ftp_gif_url 字段")
            
            # 迁移：添加 start_time 和 end_time 字段
            if 'start_time' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN start_time DATETIME")
                print("[TaskManager] events 表添加 start_time 字段")
            if 'end_time' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN end_time DATETIME")
                print("[TaskManager] events 表添加 end_time 字段")
            
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
            
            # ✓ 新增：添加model_type字段到detection_tasks表
            if 'model_type' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN model_type VARCHAR(50) DEFAULT 'yolov11n'")
                logger.info("数据库迁移: 添加model_type字段到detection_tasks表")
            
            # algorithms 表保留（不删除），避免旧触发器/外键引用失败
            # 实际算法配置已迁移到 algorithm_configs 表
            
            conn.commit()
            print(f"[TaskManager] 数据库初始化完成: {self.db_path}")
    
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
                (task_name, enabled, status, detection_type, algorithm, model_path, model_type, target_classes,
                 motion_detection_enabled, tracking_enabled,
                 decoder_config, detector_config,
                 tracker_config, motion_config, event_config, display_config, zones_config,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.task_name,
                task.enabled,
                task.status,
                task.detection_type,
                task.algorithm,
                task.model_path,
                task.model_type,  # 新增：model_type字段
                json.dumps(task.target_classes),
                task.motion_detection_enabled,
                task.tracking_enabled,
                json.dumps(task.decoder_config),
                json.dumps(task.detector_config),
                json.dumps(task.tracker_config),
                json.dumps(task.motion_config),
                json.dumps(task.event_config),
                json.dumps(task.display_config),
                json.dumps(task.zones_config),  # 新增：zones_config字段
                now,
                now
            ))

            conn.commit()
            task_id = cursor.lastrowid
            print(f"[TaskManager] 添加任务成功: ID={task_id}, Name={task.task_name}")
            return task_id
    
    def get_task(self, task_id: int) -> Optional[DetectionTask]:
        """获取单个任务"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM detection_tasks WHERE id = ?', (task_id,))
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
            
            query = 'SELECT * FROM detection_tasks WHERE 1=1'
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
        print(f"[TaskManager] 更新任务: ID={task_id}")
        print(f"[TaskManager] 接收到的更新字段: {list(updates.keys())}")
        if 'event_config' in updates:
            print(f"[TaskManager] event_config 内容: {json.dumps(updates['event_config'], ensure_ascii=False, indent=2)}")
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # JSON 字段列表
            json_fields = ['target_classes', 'decoder_config', 'detector_config',
                          'tracker_config', 'motion_config', 'event_config', 'display_config', 'zones_config']  # 新增：zones_config

            updatable_fields = {
                'task_name': 'task_name',
                'enabled': 'enabled',
                'status': 'status',
                'detection_type': 'detection_type',
                'algorithm': 'algorithm',
                'model_path': 'model_path',
                'model_type': 'model_type',  # 新增：model_type字段支持更新
                'target_classes': 'target_classes',
                'motion_detection_enabled': 'motion_detection_enabled',
                'tracking_enabled': 'tracking_enabled',
                'decoder_config': 'decoder_config',
                'detector_config': 'detector_config',
                'tracker_config': 'tracker_config',
                'motion_config': 'motion_config',
                'event_config': 'event_config',
                'display_config': 'display_config',
                'zones_config': 'zones_config'  # 新增：zones_config字段支持更新
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
                print(f"[TaskManager] 更新任务成功: ID={task_id}")
            else:
                print(f"[TaskManager] 更新任务失败: ID={task_id}")
            
            return success
    
    def delete_task(self, task_id: int) -> bool:
        """删除任务"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 先删除关联的事件
            cursor.execute('DELETE FROM events WHERE task_id = ?', (task_id,))
            
            # 删除任务-设备通道关联
            cursor.execute('DELETE FROM task_device_channel_bindings WHERE task_id = ?', (task_id,))
            
            cursor.execute('DELETE FROM detection_tasks WHERE id = ?', (task_id,))
            conn.commit()
            
            success = cursor.rowcount > 0
            if success:
                print(f"[TaskManager] 删除任务成功: ID={task_id}")
            
            return success
    
    def _row_to_task(self, row: tuple) -> DetectionTask:
        """将数据库行转换为 DetectionTask 对象"""
        # 安全解析 JSON 字段
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
            model_type=row[7] if len(row) > 7 and row[7] else 'yolov11n',  # 新增：model_type字段（索引调整）
            target_classes=safe_json_loads_list(row[8] if len(row) > 8 else None, ['person', 'car']),  # 索引调整：7 → 8
            motion_detection_enabled=row[9] if len(row) > 9 else True,  # 索引调整：8 → 9，默认启用
            tracking_enabled=row[10] if len(row) > 10 else True,  # 索引调整：9 → 10
            decoder_config=safe_json_loads_with_defaults(row[11] if len(row) > 11 else None, get_default_decoder_config()),  # 索引调整：10 → 11
            detector_config=safe_json_loads_with_defaults(row[12] if len(row) > 12 else None, get_default_detector_config()),  # 索引调整：11 → 12
            tracker_config=safe_json_loads_with_defaults(row[13] if len(row) > 13 else None, get_default_tracker_config()),  # 索引调整：12 → 13
            motion_config=safe_json_loads_with_defaults(row[14] if len(row) > 14 else None, get_default_motion_config()),  # 索引调整：13 → 14
            event_config=safe_json_loads_with_defaults(row[15] if len(row) > 15 else None, get_default_event_config()),  # 索引调整：14 → 15
            display_config=safe_json_loads_with_defaults(row[16] if len(row) > 16 else None, {}),  # 索引调整：15 → 16，显示配置为空字典
            zones_config=safe_json_loads_list(row[19] if len(row) > 19 else None, []),  # 新增：zones_config字段（索引19）
            created_at=parse_datetime(row[17] if len(row) > 17 else None),  # 索引调整：16 → 17
            updated_at=parse_datetime(row[18] if len(row) > 18 else None)  # 索引调整：17 → 18
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
                 metadata, start_time, end_time, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                datetime.now()
            ))
            
            conn.commit()
            return cursor.lastrowid
    
    def get_events(
        self, 
        task_id: int = None, 
        camera_id: str = None,
        algorithm: str = None,
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
                       metadata, start_time, end_time, created_at FROM events WHERE 1=1'''
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
            
            query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            return [self._row_to_event(row) for row in rows]
    
    def get_event_count(
        self, 
        task_id: int = None, 
        camera_id: str = None,
        algorithm: str = None
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
            print(f"[TaskManager] 清理了 {deleted} 条超过 {days} 天的事件")
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
        
        created_at = parse_datetime(row[18])  # created_at 在 index 18
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
            print(f"[TaskManager] 获取摄像机统计失败: {e}")
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
    # ✅ 新增：Zone配置管理（通道级别）
    # ========================================
    
    def get_channel_zones(self, task_id: int, channel_id: int) -> List[Dict]:
        """
        获取通道的Zone配置
        
        Args:
            task_id: 任务ID
            channel_id: 通道ID（设备通道池ID）
            
        Returns:
            Zone配置列表
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT zones_config 
                FROM task_device_channel_bindings 
                WHERE task_id = ? AND channel_id = ?
            ''', (task_id, channel_id))
            
            row = cursor.fetchone()
            
            if row and row[0]:
                try:
                    zones = json.loads(row[0])
                    return zones if isinstance(zones, list) else []
                except (json.JSONDecodeError, TypeError):
                    return []
            
            return []
    
    def save_channel_zones(self, task_id: int, channel_id: int, zones_config: List[Dict]) -> bool:
        """
        保存通道的Zone配置
        
        Args:
            task_id: 任务ID
            channel_id: 通道ID（设备通道池ID）
            zones_config: Zone配置列表
            
        Returns:
            是否保存成功
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 检查绑定记录是否存在
                cursor.execute('''
                    SELECT id FROM task_device_channel_bindings 
                    WHERE task_id = ? AND channel_id = ?
                ''', (task_id, channel_id))
                
                if not cursor.fetchone():
                    logger.error(f"绑定记录不存在: task_id={task_id}, channel_id={channel_id}")
                    return False
                
                # 更新Zone配置
                cursor.execute('''
                    UPDATE task_device_channel_bindings 
                    SET zones_config = ? 
                    WHERE task_id = ? AND channel_id = ?
                ''', (json.dumps(zones_config), task_id, channel_id))
                
                conn.commit()
                logger.info(f"Zone配置已保存: task_id={task_id}, channel_id={channel_id}, zones_count={len(zones_config)}")
                return True
                
        except Exception as e:
            logger.error(f"保存Zone配置失败: {e}")
            return False
    
    def delete_channel_zones(self, task_id: int, channel_id: int) -> bool:
        """
        删除通道的Zone配置（重置为空）
        
        Args:
            task_id: 任务ID
            channel_id: 通道ID
            
        Returns:
            是否删除成功
        """
        return self.save_channel_zones(task_id, channel_id, [])


# 全局任务管理器实例
_task_manager = None


def get_task_manager() -> TaskManager:
    """获取任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager