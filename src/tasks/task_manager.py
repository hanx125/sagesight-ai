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
        'tensorrt_engine_path': detector_config.get('tensorrt_engine_path', ''),
        'tensorrt_lib_path': detector_config.get('tensorrt_lib_path', ''),
        'device_id': detector_config.get('device_id', 0),
        'gpu_mem_limit': detector_config.get('gpu_mem_limit', 0),
        # RKNN 配置（backend=rknn 时生效）
        'rknn_target': detector_config.get('rknn_target', 'rk3588'),
        'rknn_device_id': detector_config.get('rknn_device_id', ''),
        'rknn_core_mask': detector_config.get('rknn_core_mask', 0),
        # 检测器进程数（null 表示按摄像机数自动计算）
        'num_detectors': detector_config.get('num_detectors'),
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
    # 颜色格式统一为 RGB（详见 display_config.yaml），带标记供迁移脚本识别
    display_config = {
        'display': config.get('display', {}),
        'detection_box': config.get('detection_box', {}),
        'track_box': config.get('track_box', {}),
        'trajectory': config.get('trajectory', {}),
        'motion_region': config.get('motion_region', {}),
        'alarm': config.get('alarm', {}),
        '__color_format': 'rgb'
    }

    return display_config


# ========================================
# 事件输出 / 显示 开关（任务级独立列）
# ========================================
# config/event_output.yaml、config/display_config.yaml 中的布尔开关统一提升为
# 任务级独立列（不使用嵌套 JSON），其余非开关参数仍由这两个 YAML 全局提供。
# 每项：列名 -> (在配置中的路径, YAML 缺失时的兜底默认值)

EVENT_SWITCH_PATHS = (
    ('event_enabled',                    ('enabled',),                        True),
    ('event_image_local_enabled',        ('image_output', 'local_enabled'),   True),
    ('event_image_minio_enabled',        ('image_output', 'minio_enabled'),   True),
    ('event_image_ftp_enabled',          ('image_output', 'ftp_enabled'),     False),
    ('event_image_auto_cleanup',         ('image_output', 'auto_cleanup'),    True),
    ('event_image_draw_bbox',            ('image_output', 'draw_bbox'),       True),
    ('event_compression_enabled',        ('compression', 'enabled'),          True),
    ('event_gif_enabled',                ('gif', 'enabled'),                  True),
    ('event_gif_local_enabled',          ('gif', 'local_enabled'),            True),
    ('event_gif_minio_enabled',          ('gif', 'minio_enabled'),            True),
    ('event_gif_ftp_enabled',            ('gif', 'ftp_enabled'),              False),
    ('event_gif_auto_cleanup',           ('gif', 'auto_cleanup'),             True),
    ('event_gif_draw_trajectory',        ('gif', 'draw_trajectory'),          True),
    ('event_gif_draw_bbox',              ('gif', 'draw_bbox'),                True),
    ('event_capture_save_full_on_start', ('capture', 'save_full_on_start'),   True),
    ('event_capture_save_full_on_end',   ('capture', 'save_full_on_end'),     True),
)

DISPLAY_SWITCH_PATHS = (
    ('display_show_detection_boxes',   ('display', 'show_detection_boxes'),   True),
    ('display_show_track_boxes',       ('display', 'show_track_boxes'),       True),
    ('display_show_trajectories',      ('display', 'show_trajectories'),      True),
    ('display_show_motion_regions',    ('display', 'show_motion_regions'),    True),
    ('display_show_zones',             ('display', 'show_zones'),             True),
    ('display_show_counting_line',     ('display', 'show_counting_line'),     False),
    ('display_loitering_blink',        ('track_box', 'loitering_blink'),      True),
    ('display_alarm_zone_enter_enabled', ('alarm', 'zone_enter_enabled'),     True),
    ('display_alarm_loitering_enabled',  ('alarm', 'loitering_enabled'),      True),
    ('display_alarm_show_banner',        ('alarm', 'show_banner'),            True),
)

EVENT_SWITCH_COLUMNS = tuple(item[0] for item in EVENT_SWITCH_PATHS)
DISPLAY_SWITCH_COLUMNS = tuple(item[0] for item in DISPLAY_SWITCH_PATHS)
TASK_SWITCH_COLUMNS = EVENT_SWITCH_COLUMNS + DISPLAY_SWITCH_COLUMNS

# 任务表标量字段（不含 id 与时间戳）：写入与查询共用同一份列清单，
# 避免 ALTER TABLE 造成的列顺序依赖
TASK_FIELD_COLUMNS = (
    'task_name', 'enabled', 'status', 'detection_type', 'event_type',
    'polling_interval_frames', 'motion_gate_fallback_interval',
    'schedule_cron', 'schedule_source', 'file_paths', 'file_loop',
    'dedup_enabled', 'dedup_window_seconds', 'dedup_iou_threshold',
    'voice_enabled', 'voice_mp3', 'voice_loop_count', 'voice_interval_ms',
    'voice_volume',
    'algorithm', 'model_path', 'model_type', 'labels_file', 'target_classes',
    'conf_threshold', 'motion_detection_enabled', 'tracking_enabled',
    # 边缘库比对（任务级）：是否启用 + 人脸库/车牌库名称（边缘集成平台）
    'library_match_enabled', 'face_library_name', 'plate_library_name',
)
TASK_SELECT_COLUMNS = ('id',) + TASK_FIELD_COLUMNS + TASK_SWITCH_COLUMNS + ('created_at', 'updated_at')


def _config_path_get(config: Dict, path: tuple, default):
    """按键路径读取嵌套配置值，任一层缺失时返回默认值"""
    current = config
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _config_path_set(config: Dict, path: tuple, value) -> None:
    """按键路径写入嵌套配置值，缺失的中间层自动补空字典"""
    current = config
    for key in path[:-1]:
        nxt = current.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            current[key] = nxt
        current = nxt
    current[path[-1]] = value


def _load_switch_defaults(config: Dict, mapping: tuple) -> Dict[str, bool]:
    """从配置文件默认值中提取各开关列的默认值"""
    return {
        column: bool(_config_path_get(config, path, fallback))
        for column, path, fallback in mapping
    }


def _coerce_bool(value, default: bool) -> bool:
    """把外部传入的开关值（bool/字符串/数字）统一转换为 bool"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


# 开关默认值唯一来源为 config/*.yaml（新增任务与数据库建列均以此为准）
_EVENT_SWITCH_DEFAULTS = _load_switch_defaults(get_default_event_config(), EVENT_SWITCH_PATHS)
_DISPLAY_SWITCH_DEFAULTS = _load_switch_defaults(get_default_display_config(), DISPLAY_SWITCH_PATHS)
_SWITCH_DEFAULTS = {**_EVENT_SWITCH_DEFAULTS, **_DISPLAY_SWITCH_DEFAULTS}


def coerce_switch_value(column: str, value) -> bool:
    """任务级开关取值归一化（None 时回落到 config/*.yaml 默认值）"""
    return _coerce_bool(value, _SWITCH_DEFAULTS[column])


def _switch_columns_ddl() -> str:
    """生成开关列的建表片段（默认值取自 config/*.yaml）"""
    lines = ['-- 事件输出开关（任务级；其余事件参数由 config/event_output.yaml 全局提供）']
    for column in EVENT_SWITCH_COLUMNS:
        lines.append(f"                    {column} BOOLEAN DEFAULT {1 if _SWITCH_DEFAULTS[column] else 0},")
    lines.append('')
    lines.append('-- 显示开关（任务级；其余显示参数由 config/display_config.yaml 全局提供）')
    for column in DISPLAY_SWITCH_COLUMNS:
        lines.append(f"                    {column} BOOLEAN DEFAULT {1 if _SWITCH_DEFAULTS[column] else 0},")
    return '\n'.join(lines)


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
    detection_type: str = "realtime"  # realtime（实时检测）, polling（轮询检测）, motion_gated（运动门控检测）, file（离线文件检测）, scheduled（定时批量检测）
    event_type: str = "tracking"  # 语义检测类型（事件类型）：tracking（目标跟踪）, zone（区域入侵）, loitering（徘徊检测）, people_counting（人员统计）, face（人脸检测）, plate（车牌检测）
    # 轮询检测（polling）：每 N 帧推理一次，其余帧跳过推理仅保留跟踪状态
    polling_interval_frames: int = 5
    # 运动门控检测（motion_gated）：无运动时跳过推理，每 N 帧补抽一次以防漏检静止目标
    motion_gate_fallback_interval: int = 30
    # 定时批量检测（scheduled）：cron 表达式（如 "0 2 * * *"），命中时启动批量检测
    schedule_cron: str = ""
    # 定时批量检测（scheduled）：扫描来源（录像目录），为空时用 file_paths
    schedule_source: str = ""
    # 离线文件检测（file）/ 定时（scheduled）：待处理的视频文件路径列表
    file_paths: List[str] = field(default_factory=list)
    # 离线文件检测（file）：是否循环处理文件列表（True 时无限循环）
    file_loop: bool = False
    # 事件规则去重：配置唯一来源为 system_config.yaml 的 event_dedup 段。
    # 以下三个字段为历史任务表列，保留以兼容既有数据库记录，运行时不再读取。
    dedup_enabled: bool = True
    dedup_window_seconds: int = 300
    dedup_iou_threshold: float = 0.85
    status: str = "stopped"  # running, stopped, error

    # 报警语音播报配置（任务级，独立字段）
    # 触发时由服务端通过本地音频设备播放任务指定的 MP3 预置文件
    voice_enabled: bool = False  # 是否启用语音播报
    voice_mp3: str = ""  # 选定的 MP3 预置文件名（static/alarm_mp3/ 下的文件名）
    voice_loop_count: int = 1  # 循环播放次数
    voice_interval_ms: int = 1000  # 循环播放时间间隙（毫秒）
    voice_volume: float = 1.0  # 播放音量（0-1）

    # 检测算法配置
    algorithm: str = "yolov11"
    model_path: str = "models/yolov11n.onnx"
    model_type: str = "yolov11n"  # 模型类型字段（如 yolov11n, yolov8n, yolo11n）
    labels_file: str = ""  # 标签文件路径（创建任务时从算法库 labels_file 拷贝，独立字段避免嵌套 JSON）
    target_classes: List[str] = field(default_factory=list)  # 空列表表示不限制检测类别
    # 检测置信度阈值（任务级独立字段，前端可调）
    # 解码/检测/跟踪/运动四类配置统一由 config/*.yaml 提供（全局一致），不再保存在任务表
    conf_threshold: float = 0.25

    # 功能开关（独立字段，便于任务管理）
    motion_detection_enabled: bool = True  # 是否启用运动检测（默认启用）
    tracking_enabled: bool = True  # 是否启用跟踪功能（默认启用）

    # 边缘库比对（任务级）：检测到人脸/车牌后调用边缘集成平台库接口比对，
    # 结果随事件上报云平台。连接参数（地址/令牌/超时）由 system_config 全局提供。
    library_match_enabled: bool = False  # 是否启用边缘库比对
    face_library_name: str = ''  # 人脸库名称（空表示不比对，运行时按名称解析库 ID）
    plate_library_name: str = ''  # 车牌库名称（空表示不比对，运行时按名称解析库 ID）

    # 事件输出开关（任务级独立列）
    # 其余事件参数由 config/event_output.yaml 全局提供（所有任务一致），运行期合并
    event_enabled: bool = _SWITCH_DEFAULTS['event_enabled']
    event_image_local_enabled: bool = _SWITCH_DEFAULTS['event_image_local_enabled']
    event_image_minio_enabled: bool = _SWITCH_DEFAULTS['event_image_minio_enabled']
    event_image_ftp_enabled: bool = _SWITCH_DEFAULTS['event_image_ftp_enabled']
    event_image_auto_cleanup: bool = _SWITCH_DEFAULTS['event_image_auto_cleanup']
    event_image_draw_bbox: bool = _SWITCH_DEFAULTS['event_image_draw_bbox']
    event_compression_enabled: bool = _SWITCH_DEFAULTS['event_compression_enabled']
    event_gif_enabled: bool = _SWITCH_DEFAULTS['event_gif_enabled']
    event_gif_local_enabled: bool = _SWITCH_DEFAULTS['event_gif_local_enabled']
    event_gif_minio_enabled: bool = _SWITCH_DEFAULTS['event_gif_minio_enabled']
    event_gif_ftp_enabled: bool = _SWITCH_DEFAULTS['event_gif_ftp_enabled']
    event_gif_auto_cleanup: bool = _SWITCH_DEFAULTS['event_gif_auto_cleanup']
    event_gif_draw_trajectory: bool = _SWITCH_DEFAULTS['event_gif_draw_trajectory']
    event_gif_draw_bbox: bool = _SWITCH_DEFAULTS['event_gif_draw_bbox']
    event_capture_save_full_on_start: bool = _SWITCH_DEFAULTS['event_capture_save_full_on_start']
    event_capture_save_full_on_end: bool = _SWITCH_DEFAULTS['event_capture_save_full_on_end']

    # 显示开关（任务级独立列）
    # 其余显示参数（颜色、线宽、FPS 等）由 config/display_config.yaml 全局提供
    display_show_detection_boxes: bool = _SWITCH_DEFAULTS['display_show_detection_boxes']
    display_show_track_boxes: bool = _SWITCH_DEFAULTS['display_show_track_boxes']
    display_show_trajectories: bool = _SWITCH_DEFAULTS['display_show_trajectories']
    display_show_motion_regions: bool = _SWITCH_DEFAULTS['display_show_motion_regions']
    display_show_zones: bool = _SWITCH_DEFAULTS['display_show_zones']
    display_show_counting_line: bool = _SWITCH_DEFAULTS['display_show_counting_line']
    display_loitering_blink: bool = _SWITCH_DEFAULTS['display_loitering_blink']
    display_alarm_zone_enter_enabled: bool = _SWITCH_DEFAULTS['display_alarm_zone_enter_enabled']
    display_alarm_loitering_enabled: bool = _SWITCH_DEFAULTS['display_alarm_loitering_enabled']
    display_alarm_show_banner: bool = _SWITCH_DEFAULTS['display_alarm_show_banner']

    # 时间戳
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def build_event_config(self) -> Dict:
        """组装任务生效的事件输出配置

        以 config/event_output.yaml 为基准（全局唯一来源），用任务级开关列覆盖对应开关。
        """
        config = get_default_event_config()
        for column, path, _ in EVENT_SWITCH_PATHS:
            _config_path_set(config, path, bool(getattr(self, column)))
        config = normalize_event_config_storage(config)
        # 边缘库比对：连接参数来自 system_config 全局段，库名称为任务级列
        from src.system.system_config_loader import get_edge_library_config
        edge_cfg = get_edge_library_config()
        config['library_match'] = {
            'enabled': bool(self.library_match_enabled),
            'edge_api_url': edge_cfg['edge_api_url'],
            'agent_token': edge_cfg['agent_token'],
            'timeout': edge_cfg['timeout'],
            'crop_margin': edge_cfg['crop_margin'],
            'face_classes': edge_cfg['face_classes'],
            'plate_classes': edge_cfg['plate_classes'],
            'face_library_name': self.face_library_name or '',
            'plate_library_name': self.plate_library_name or '',
        }
        return config

    def build_display_config(self) -> Dict:
        """组装任务生效的显示配置

        以 config/display_config.yaml 为基准（全局唯一来源），用任务级开关列覆盖对应开关。
        """
        config = get_default_display_config()
        for column, path, _ in DISPLAY_SWITCH_PATHS:
            _config_path_set(config, path, bool(getattr(self, column)))
        return config

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
            'event_type': self.event_type,  # 语义检测类型（事件类型）
            'polling_interval_frames': self.polling_interval_frames,  # 轮询抽样间隔
            'motion_gate_fallback_interval': self.motion_gate_fallback_interval,  # 运动门控兜底补抽间隔
            'schedule_cron': self.schedule_cron,  # 定时批量 cron 表达式
            'schedule_source': self.schedule_source,  # 定时批量扫描来源（录像目录）
            'file_paths': self.file_paths,  # 离线文件路径列表
            'file_loop': self.file_loop,  # 是否循环处理文件
            'dedup_enabled': self.dedup_enabled,  # 事件规则去重开关
            'dedup_window_seconds': self.dedup_window_seconds,  # 去重时间窗口（秒）
            'dedup_iou_threshold': self.dedup_iou_threshold,  # 去重 IoU 阈值
            'voice_enabled': self.voice_enabled,  # 报警语音播报开关
            'voice_mp3': self.voice_mp3,  # 报警语音 MP3 预置文件名
            'voice_loop_count': self.voice_loop_count,  # 播放循环次数
            'voice_interval_ms': self.voice_interval_ms,  # 循环播放时间间隙（毫秒）
            'voice_volume': self.voice_volume,  # 播放音量
            'algorithm': self.algorithm,
            'model_path': self.model_path,
            'model_type': self.model_type,  # 模型类型字段
            'labels_file': self.labels_file,  # 标签文件路径
            'target_classes': self.target_classes,
            'conf_threshold': self.conf_threshold,
            'motion_detection_enabled': self.motion_detection_enabled,
            'tracking_enabled': self.tracking_enabled,
            # 边缘库比对（任务级）
            'library_match_enabled': self.library_match_enabled,
            'face_library_name': self.face_library_name,
            'plate_library_name': self.plate_library_name,
            'event_config': self.build_event_config(),
            'display_config': self.build_display_config(),
            # 任务级开关（独立列，供前端「参数配置」对话框按列名读写）
            **{column: bool(getattr(self, column)) for column in TASK_SWITCH_COLUMNS},
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
            event_type=cls._resolve_event_type(data),  # 语义检测类型（事件类型）
            polling_interval_frames=data.get('polling_interval_frames', 5),  # 轮询抽样间隔
            motion_gate_fallback_interval=data.get('motion_gate_fallback_interval', 30),  # 运动门控兜底补抽间隔
            schedule_cron=data.get('schedule_cron', ''),  # 定时批量 cron 表达式
            schedule_source=data.get('schedule_source', ''),  # 定时批量扫描来源
            file_paths=data.get('file_paths', []),  # 离线文件路径列表
            file_loop=data.get('file_loop', False),  # 是否循环处理文件
            dedup_enabled=data.get('dedup_enabled', True),  # 事件规则去重开关
            dedup_window_seconds=data.get('dedup_window_seconds', 300),  # 去重时间窗口（秒）
            dedup_iou_threshold=data.get('dedup_iou_threshold', 0.85),  # 去重 IoU 阈值
            voice_enabled=data.get('voice_enabled', False),  # 报警语音播报开关
            voice_mp3=data.get('voice_mp3', ''),  # 报警语音 MP3 预置文件名
            voice_loop_count=data.get('voice_loop_count', 1),  # 播放循环次数
            voice_interval_ms=data.get('voice_interval_ms', 1000),  # 循环播放时间间隙（毫秒）
            voice_volume=data.get('voice_volume', 1.0),  # 播放音量
            algorithm=data.get('algorithm', 'yolov11'),
            model_path=data.get('model_path', 'models/yolov11n.onnx'),
            model_type=data.get('model_type', 'yolov11n'),  # 模型类型字段
            labels_file=data.get('labels_file', ''),  # 标签文件路径
            target_classes=data.get('target_classes', []),  # 空列表表示不限制检测类别
            conf_threshold=float(data.get('conf_threshold') if data.get('conf_threshold') is not None else 0.25),
            motion_detection_enabled=data.get('motion_detection_enabled', True),  # 默认启用
            tracking_enabled=data.get('tracking_enabled', True),
            # 边缘库比对（任务级）
            library_match_enabled=bool(data.get('library_match_enabled', False)),
            face_library_name=(data.get('face_library_name') or '').strip(),
            plate_library_name=(data.get('plate_library_name') or '').strip(),
            **{
                column: _coerce_bool(data.get(column), _SWITCH_DEFAULTS[column])
                for column in TASK_SWITCH_COLUMNS
            },
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else None,
            updated_at=datetime.fromisoformat(data['updated_at']) if data.get('updated_at') else None
        )

    @classmethod
    def _resolve_event_type(cls, data: Dict) -> str:
        """解析语义检测类型（事件类型）：显式指定时采用，未指定时默认 tracking。"""
        return data.get('event_type') or 'tracking'

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
            'conf_threshold': self.conf_threshold,
            'detection_type': self.detection_type,  # 检测类型
            'event_type': self.event_type,  # 语义检测类型（事件类型）
            'polling_interval_frames': self.polling_interval_frames,  # 轮询抽样间隔
            'motion_gate_fallback_interval': self.motion_gate_fallback_interval,  # 运动门控兜底补抽间隔
            'schedule_cron': self.schedule_cron,  # 定时批量 cron 表达式
            'schedule_source': self.schedule_source,  # 定时批量扫描来源
            'file_paths': self.file_paths,  # 离线文件路径列表
            'file_loop': self.file_loop,  # 是否循环处理文件
            'dedup_enabled': self.dedup_enabled,  # 事件规则去重开关
            'dedup_window_seconds': self.dedup_window_seconds,  # 去重时间窗口（秒）
            'dedup_iou_threshold': self.dedup_iou_threshold,  # 去重 IoU 阈值
            'motion_detection_enabled': self.motion_detection_enabled,
            'tracking_enabled': self.tracking_enabled,
            'event_config': self.build_event_config(),
            'display_config': self.build_display_config()
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
    status: str = "tracking_start"  # 事件状态：tracking_start/tracking_update/initial/final/duplicate/tracking_end
    entered_zones: List[str] = field(default_factory=list)  # 目标进入过的区域（只增不减）
    zone_name: str = ""  # 当前所在区域（多个用逗号分隔）
    loitered_zones: List[str] = field(default_factory=list)  # 生命周期内累计曾徘徊的区域（只增不减）
    # 大模型描述 + 去重字段
    ai_description: Optional[str] = None  # 多模态大模型生成的事件描述
    ai_description_model: str = ""  # 生成描述使用的模型名
    ai_description_time: Optional[datetime] = None  # 描述生成时间
    dedup_status: str = ""  # 去重状态：normal（正常）/duplicate（重复）
    dedup_reason: str = ""  # 去重判定原因
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
            'ai_description': self.ai_description,
            'ai_description_model': self.ai_description_model,
            'ai_description_time': format_datetime(self.ai_description_time),
            'dedup_status': self.dedup_status,
            'dedup_reason': self.dedup_reason,
            'created_at': format_datetime(self.created_at)
        }


# ========================================
# 数据库迁移：任务表全局配置列清理
# ========================================

# 已统一由 config/*.yaml 提供（全局一致、所有任务相同）的配置列，任务表不再保存
GLOBAL_CONFIG_COLUMNS = ('decoder_config', 'detector_config', 'tracker_config', 'motion_config')


def migrate_task_global_configs(cursor) -> None:
    """迁移 detection_tasks 表：新增任务级 conf_threshold，删除全局配置列。

    解码/检测/跟踪/运动四类配置已统一由 config/*.yaml 提供（全局一致）、
    不再保存在任务表；旧的 detector_config.conf_threshold 搬迁为独立列。
    幂等：表结构已符合目标时不执行任何操作。
    """
    cursor.execute("PRAGMA table_info(detection_tasks)")
    columns = [column[1] for column in cursor.fetchall()]

    # 1) 新增任务级 conf_threshold，并把旧 detector_config 中的取值搬迁过来
    if 'conf_threshold' not in columns:
        cursor.execute("ALTER TABLE detection_tasks ADD COLUMN conf_threshold REAL DEFAULT 0.25")
        logger.info("数据库迁移: 添加 conf_threshold 字段到 detection_tasks 表")
        if 'detector_config' in columns:
            _backfill_conf_threshold(cursor)

    # 2) 物理删除已废弃的全局配置列
    deprecated = [column for column in GLOBAL_CONFIG_COLUMNS if column in columns]
    if deprecated:
        _drop_task_columns(cursor, deprecated)
        logger.info(f"数据库迁移: 已删除 detection_tasks 全局配置列 {deprecated}")

    # 3) 人数报警阈值已改为区域级（counting_zones.alarm_threshold / counting_lines 内），
    #    删除任务级废弃列
    if 'count_alarm_threshold' in columns:
        _drop_task_columns(cursor, ['count_alarm_threshold'])
        logger.info("数据库迁移: 已删除 detection_tasks 任务级 count_alarm_threshold 列")


def _backfill_conf_threshold(cursor) -> None:
    """把旧 detector_config JSON 中的 conf_threshold 搬迁到独立列"""
    cursor.execute("SELECT id, detector_config FROM detection_tasks")
    updated = 0
    for task_id, raw in cursor.fetchall():
        if not raw:
            continue
        try:
            config = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(config, dict) or config.get('conf_threshold') is None:
            continue
        cursor.execute(
            "UPDATE detection_tasks SET conf_threshold = ? WHERE id = ?",
            (float(config['conf_threshold']), task_id)
        )
        updated += 1
    if updated:
        logger.info(f"数据库迁移: 已从 detector_config 搬迁 conf_threshold 的任务数: {updated}")


def _drop_task_columns(cursor, columns: List[str]) -> None:
    """删除 detection_tasks 指定列（SQLite 3.35+ 直接 DROP COLUMN，旧版本重建表）"""
    if sqlite3.sqlite_version_info >= (3, 35, 0):
        for column in columns:
            cursor.execute(f"ALTER TABLE detection_tasks DROP COLUMN {column}")
        return

    # 旧版 SQLite 不支持 DROP COLUMN：按保留列重建表并搬移数据
    cursor.execute("PRAGMA table_info(detection_tasks)")
    keep = [row for row in cursor.fetchall() if row[1] not in columns]

    definitions = []
    for _, name, column_type, notnull, default, primary_key in keep:
        if primary_key:
            definitions.append(f"{name} INTEGER PRIMARY KEY AUTOINCREMENT")
            continue
        definition = f"{name} {column_type or 'TEXT'}"
        if notnull:
            definition += " NOT NULL"
        if default is not None:
            definition += f" DEFAULT {default}"
        definitions.append(definition)

    kept_columns = ', '.join(row[1] for row in keep)
    cursor.execute("ALTER TABLE detection_tasks RENAME TO detection_tasks_legacy")
    cursor.execute(f"CREATE TABLE detection_tasks ({', '.join(definitions)})")
    cursor.execute(
        f"INSERT INTO detection_tasks ({kept_columns}) "
        f"SELECT {kept_columns} FROM detection_tasks_legacy"
    )
    cursor.execute("DROP TABLE detection_tasks_legacy")


def migrate_task_event_display_switches(cursor) -> None:
    """迁移 detection_tasks 表：把事件输出/显示配置的布尔开关提升为独立列。

    config/event_output.yaml 与 config/display_config.yaml 仍为这两个配置的
    全局唯一来源，其中的布尔开关改为任务级独立列（与 conf_threshold 同级），
    任务表不再保存整块 JSON。幂等：结构已符合目标时不执行任何操作。
    """
    cursor.execute("PRAGMA table_info(detection_tasks)")
    columns = [column[1] for column in cursor.fetchall()]

    # 1) 缺失的开关列按 config/*.yaml 默认值补齐
    missing = [column for column in TASK_SWITCH_COLUMNS if column not in columns]
    for column in missing:
        default = 1 if _SWITCH_DEFAULTS[column] else 0
        cursor.execute(
            f"ALTER TABLE detection_tasks ADD COLUMN {column} BOOLEAN DEFAULT {default}"
        )
    if missing:
        logger.info(f"数据库迁移: 已添加事件输出/显示开关列 {len(missing)} 个")

    # 2) 从旧的 JSON 列回填开关取值，再物理删除旧列
    legacy = [column for column in ('event_config', 'display_config') if column in columns]
    if legacy:
        _backfill_task_switches(cursor)
        _drop_task_columns(cursor, legacy)
        logger.info(f"数据库迁移: 已删除 detection_tasks 事件/显示 JSON 列 {legacy}")


def _backfill_task_switches(cursor) -> None:
    """把旧 event_config / display_config JSON 中的开关值搬迁到独立列"""
    cursor.execute("SELECT id, event_config, display_config FROM detection_tasks")
    rows = cursor.fetchall()
    if not rows:
        return

    updated = 0
    for task_id, event_raw, display_raw in rows:
        values = {}
        for raw, mapping in ((event_raw, EVENT_SWITCH_PATHS), (display_raw, DISPLAY_SWITCH_PATHS)):
            config = _load_legacy_config(raw, mapping is EVENT_SWITCH_PATHS)
            if not isinstance(config, dict):
                continue
            for column, path, _ in mapping:
                value = _config_path_get(config, path, None)
                if value is not None:
                    values[column] = 1 if _coerce_bool(value, _SWITCH_DEFAULTS[column]) else 0

        if not values:
            continue

        assignments = ', '.join(f"{column} = ?" for column in values)
        cursor.execute(
            f"UPDATE detection_tasks SET {assignments} WHERE id = ?",
            list(values.values()) + [task_id]
        )
        updated += 1

    if updated:
        logger.info(f"数据库迁移: 已从事件/显示 JSON 搬迁开关的任务数: {updated}")


def _load_legacy_config(raw, is_event_config: bool) -> Dict:
    """解析旧 JSON 配置列（事件配置先做一次旧字段归一化）"""
    if not raw:
        return {}
    try:
        config = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(config, dict):
        return {}
    return normalize_event_config_storage(config) if is_event_config else config


def migrate_task_config_schema(cursor) -> None:
    """任务表配置结构迁移总入口"""
    migrate_task_global_configs(cursor)
    migrate_task_event_display_switches(cursor)
    migrate_counting_zone_alarm_threshold(cursor)


def migrate_counting_zone_alarm_threshold(cursor) -> None:
    """counting_zones 新增区域级人数报警阈值列（幂等）。

    人数报警阈值由任务级改为区域级：画线区域随 counting_lines JSON 存储，
    多机进出方向区域使用本列。0 表示不按人数触发。
    """
    cursor.execute("PRAGMA table_info(counting_zones)")
    columns = [row[1] for row in cursor.fetchall()]
    if not columns or 'alarm_threshold' in columns:
        return
    cursor.execute("ALTER TABLE counting_zones ADD COLUMN alarm_threshold INTEGER DEFAULT 0")
    logger.info("数据库迁移: 添加 alarm_threshold 字段到 counting_zones 表")


def migrate_counting_zone_levels(cursor) -> None:
    """计数区域分层迁移：单机画线 → 通道级；counting_zones 收敛为多机进出方向专用。

    区域配置属于通道级别，画线区域本质是某一路相机画面上的分界线，
    因此搬迁到 device_channel_pool.counting_lines（与通道级 zones_config 同级）；
    多机进出的 in/out 通道对天然跨通道，保留在任务级 counting_zones。
    幂等：counting_zones 已无画线专用列时直接返回。
    """
    cursor.execute("PRAGMA table_info(counting_zones)")
    columns = [row[1] for row in cursor.fetchall()]
    if not columns:
        return

    line_only_columns = ('mode', 'channel_id', 'cross_line', 'in_side', 'min_displacement')
    if not any(column in columns for column in line_only_columns):
        return

    # 确保通道表存在 counting_lines 列（历史库可能缺失；独立执行迁移脚本时同样生效）
    cursor.execute("PRAGMA table_info(device_channel_pool)")
    if 'counting_lines' not in [row[1] for row in cursor.fetchall()]:
        cursor.execute("ALTER TABLE device_channel_pool ADD COLUMN counting_lines TEXT")

    _migrate_line_zones_to_channels(cursor)

    cursor.execute("ALTER TABLE counting_zones RENAME TO counting_zones_legacy")
    cursor.execute('''
        CREATE TABLE counting_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            zone_name VARCHAR(100) NOT NULL,
            in_channels TEXT,
            out_channels TEXT,
            alarm_threshold INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    direction_filter = "WHERE mode = 'direction'" if 'mode' in columns else ''
    cursor.execute(f'''
        INSERT INTO counting_zones
            (task_id, zone_name, in_channels, out_channels, created_at, updated_at)
        SELECT task_id, zone_name, in_channels, out_channels, created_at, updated_at
        FROM counting_zones_legacy {direction_filter}
    ''')
    cursor.execute("DROP TABLE counting_zones_legacy")
    logger.info("[TaskManager] counting_zones 表已收敛为多机进出方向专用")


def _migrate_line_zones_to_channels(cursor) -> None:
    """把旧 counting_zones 中的画线行按通道合并搬迁到通道级 counting_lines"""
    cursor.execute('''
        SELECT channel_id, zone_name, cross_line, in_side, inertia, min_displacement
        FROM counting_zones
        WHERE mode = 'line' AND channel_id IS NOT NULL
        ORDER BY channel_id, id
    ''')

    grouped: Dict[int, Dict[str, Dict]] = {}
    duplicates = 0
    for channel_id, zone_name, cross_line, in_side, inertia, min_displacement in cursor.fetchall():
        zones = grouped.setdefault(int(channel_id), {})
        # 同一通道下同名画线区域仅保留首个（跨任务定义合并为一个通道级配置）
        if zone_name in zones:
            duplicates += 1
            continue
        zones[zone_name] = {
            'zone_name': zone_name,
            'cross_line': safe_json_loads(cross_line, None),
            'in_side': int(in_side) if in_side is not None else 1,
            'inertia': int(inertia) if inertia is not None else 3,
            'min_displacement': float(min_displacement) if min_displacement is not None else 0.02,
        }

    migrated = 0
    for channel_id, zones in grouped.items():
        cursor.execute("SELECT counting_lines FROM device_channel_pool WHERE id = ?", (channel_id,))
        row = cursor.fetchone()
        # 通道已有画线配置或通道已不存在时跳过，避免覆盖与悬空引用
        if not row or row[0]:
            continue
        cursor.execute(
            "UPDATE device_channel_pool SET counting_lines = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(list(zones.values()), ensure_ascii=False), channel_id)
        )
        migrated += 1

    if migrated:
        logger.info(f"[TaskManager] 已将 {migrated} 个通道的单机画线计数区域迁移到通道级")
    if duplicates:
        logger.warning(f"[TaskManager] 跨任务重名画线区域 {duplicates} 个未迁移（同一通道同名仅保留首个）")


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
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS detection_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name VARCHAR(100) NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    status VARCHAR(20) DEFAULT 'stopped',
                    detection_type VARCHAR(20) DEFAULT 'realtime',
                    event_type VARCHAR(20) DEFAULT 'tracking',  -- 语义检测类型（事件类型）

                    -- 检测算法配置
                    algorithm VARCHAR(50) DEFAULT 'yolov11',
                    model_path VARCHAR(200) DEFAULT 'models/yolov11n.onnx',
                    model_type VARCHAR(50) DEFAULT 'yolov11n',  -- 模型类型字段
                    labels_file VARCHAR(500) DEFAULT '',  -- 标签文件路径（创建任务时从算法库拷贝）
                    target_classes TEXT DEFAULT '[]',  -- 空数组表示不限制检测类别
                    conf_threshold REAL DEFAULT 0.25,  -- 检测置信度阈值（任务级）

                    -- 功能开关
                    motion_detection_enabled BOOLEAN DEFAULT 1,  -- 默认启用运动检测
                    tracking_enabled BOOLEAN DEFAULT 1,  -- 默认启用跟踪功能,

                    -- 边缘库比对（任务级；连接参数由 system_config.edge_library 全局提供）
                    library_match_enabled BOOLEAN DEFAULT 0,  -- 是否启用边缘库比对
                    face_library_name TEXT DEFAULT '',  -- 人脸库名称（空表示不比对）
                    plate_library_name TEXT DEFAULT '',  -- 车牌库名称（空表示不比对）

                    -- 事件输出 / 显示 开关（任务级；其余参数由 config/*.yaml 全局提供）
{_switch_columns_ddl()}

                    -- 事件规则去重（任务级）
                    dedup_enabled BOOLEAN DEFAULT 1,
                    dedup_window_seconds INTEGER DEFAULT 300,
                    dedup_iou_threshold REAL DEFAULT 0.85,

                    -- 报警语音播报（任务级）
                    voice_enabled BOOLEAN DEFAULT 0,
                    voice_mp3 VARCHAR(255) DEFAULT '',
                    voice_loop_count INTEGER DEFAULT 1,
                    voice_interval_ms INTEGER DEFAULT 1000,
                    voice_volume REAL DEFAULT 1.0,

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
                    counting_lines TEXT,
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

            # 人员统计计数区域存储位置统一：单机画线为通道级（device_channel_pool.counting_lines）
            # 画线区域本质属于某一路相机的画面，与通道级 zones_config 同级存储
            if 'counting_lines' not in dcp_cols:
                cursor.execute("ALTER TABLE device_channel_pool ADD COLUMN counting_lines TEXT")
                logger.info("[TaskManager] device_channel_pool 表添加 counting_lines 字段")

            # 数据迁移：把任务-通道绑定表中的 zones_config 迁移到通道表（按 channel_id 取非空值）
            # 迁移后清空绑定表的 zones_config，使通道表成为唯一数据源
            # 注意：新版建表已不在 task_device_channel_bindings 中创建 zones_config 列，
            #       仅旧库可能残留该列，需先检查列存在性再迁移，避免 "no such column" 报错。
            try:
                cursor.execute("PRAGMA table_info(task_device_channel_bindings)")
                binding_cols = [row[1] for row in cursor.fetchall()]
                if 'zones_config' not in binding_cols:
                    # 绑定表无 zones_config 列（新库或已迁移完成），跳过迁移
                    pass
                else:
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

            # 旧事件兜底：metadata 无 status 的行视为最终事件
            try:
                cursor.execute('''
                    UPDATE events
                    SET status = 'final'
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

            # 迁移：添加大模型描述 + 去重字段
            if 'ai_description' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN ai_description TEXT")
                logger.debug("[TaskManager] events 表添加 ai_description 字段")
            if 'ai_description_model' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN ai_description_model VARCHAR(100)")
                logger.debug("[TaskManager] events 表添加 ai_description_model 字段")
            if 'ai_description_time' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN ai_description_time DATETIME")
                logger.debug("[TaskManager] events 表添加 ai_description_time 字段")
            if 'dedup_status' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN dedup_status VARCHAR(20)")
                logger.debug("[TaskManager] events 表添加 dedup_status 字段")
            if 'dedup_reason' not in columns:
                cursor.execute("ALTER TABLE events ADD COLUMN dedup_reason TEXT")
                logger.debug("[TaskManager] events 表添加 dedup_reason 字段")

            # ========================================
            # 人员统计（people counting）数据表
            # 计数区域按层级拆分存储：
            #   - 单机画线（模式A）：通道级，存于 device_channel_pool.counting_lines
            #   - 多机进出（模式B）：任务级，本表按 in/out 通道对描述跨通道关系
            # counting_stats: 按天复位的计数状态（唯一键 task_id+zone_name+channel_id+count_date）
            # 按天复位：所有读写都以 count_date 为键，跨天即视为从零开始。
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS counting_zones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    zone_name VARCHAR(100) NOT NULL,
                    in_channels TEXT,
                    out_channels TEXT,
                    alarm_threshold INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS counting_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    zone_name VARCHAR(100) NOT NULL,
                    channel_id INTEGER NOT NULL,
                    count_date VARCHAR(10) NOT NULL,
                    in_count INTEGER DEFAULT 0,
                    out_count INTEGER DEFAULT 0,
                    current_count INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_counting_stats_unique
                ON counting_stats (task_id, zone_name, channel_id, count_date)
            ''')

            # 计数区域分层迁移：单机画线行搬迁到通道级，counting_zones 收敛为方向专用
            migrate_counting_zone_levels(cursor)

            # ========================================
            # 迁移：历史事件状态归一化（保证事件列表只有 initial/final/duplicate 三种状态）
            # 事件状态机升级为"跟踪中不写库"后，事件首次落库即为 initial/duplicate，
            # 再切换为 final/duplicate。旧逻辑可能残留 tracking_start / tracking_update
            # （跟踪中被中断的孤儿事件）和 tracking_end（已废弃状态），此处统一转为
            # 最终事件 final，避免事件列表出现中间状态。
            # ========================================
            try:
                cursor.execute('''
                    UPDATE events SET status = 'final'
                    WHERE status IN ('tracking_start', 'tracking_update', 'tracking_end')
                ''')
                if cursor.rowcount > 0:
                    logger.info(
                        f"[TaskManager] 已归一化 {cursor.rowcount} 条历史事件状态 "
                        f"(tracking_start/tracking_update/tracking_end → final)"
                    )
            except Exception as e:
                logger.warning(f"历史事件状态归一化失败: {e}")

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

            # 添加检测类型参数：polling 轮询抽样间隔
            if 'polling_interval_frames' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN polling_interval_frames INTEGER DEFAULT 5")
                logger.info("数据库迁移: 添加 polling_interval_frames 字段到 detection_tasks 表")

            # 添加检测类型参数：motion_gated 无运动兜底补抽间隔
            if 'motion_gate_fallback_interval' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN motion_gate_fallback_interval INTEGER DEFAULT 30")
                logger.info("数据库迁移: 添加 motion_gate_fallback_interval 字段到 detection_tasks 表")

            # 添加检测类型参数：scheduled 定时批量 cron 表达式
            if 'schedule_cron' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN schedule_cron VARCHAR(100) DEFAULT ''")
                logger.info("数据库迁移: 添加 schedule_cron 字段到 detection_tasks 表")

            # 添加检测类型参数：scheduled 扫描来源（录像目录）
            if 'schedule_source' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN schedule_source VARCHAR(500) DEFAULT ''")
                logger.info("数据库迁移: 添加 schedule_source 字段到 detection_tasks 表")

            # 添加检测类型参数：file 离线文件路径列表（JSON）
            if 'file_paths' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN file_paths TEXT DEFAULT '[]'")
                logger.info("数据库迁移: 添加 file_paths 字段到 detection_tasks 表")

            # 添加检测类型参数：file 是否循环处理文件
            if 'file_loop' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN file_loop INTEGER DEFAULT 0")
                logger.info("数据库迁移: 添加 file_loop 字段到 detection_tasks 表")

            # 添加事件规则去重字段（任务级）
            if 'dedup_enabled' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN dedup_enabled BOOLEAN DEFAULT 1")
                logger.info("数据库迁移: 添加 dedup_enabled 字段到 detection_tasks 表")
            if 'dedup_window_seconds' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN dedup_window_seconds INTEGER DEFAULT 300")
                logger.info("数据库迁移: 添加 dedup_window_seconds 字段到 detection_tasks 表")
            if 'dedup_iou_threshold' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN dedup_iou_threshold REAL DEFAULT 0.85")
                logger.info("数据库迁移: 添加 dedup_iou_threshold 字段到 detection_tasks 表")

            # ✓ 新增：添加model_type字段到detection_tasks表
            if 'model_type' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN model_type VARCHAR(50) DEFAULT 'yolov11n'")
                logger.info("数据库迁移: 添加model_type字段到detection_tasks表")

            # 添加 labels_file 字段到 detection_tasks 表（标签文件路径，创建任务时从算法库拷贝）
            if 'labels_file' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN labels_file VARCHAR(500) DEFAULT ''")
                logger.info("数据库迁移: 添加 labels_file 字段到 detection_tasks 表")

            # 添加报警语音播报字段（任务级）
            if 'voice_enabled' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN voice_enabled BOOLEAN DEFAULT 0")
                logger.info("数据库迁移: 添加 voice_enabled 字段到 detection_tasks 表")
            if 'voice_mp3' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN voice_mp3 VARCHAR(255) DEFAULT ''")
                logger.info("数据库迁移: 添加 voice_mp3 字段到 detection_tasks 表")
            if 'voice_loop_count' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN voice_loop_count INTEGER DEFAULT 1")
                logger.info("数据库迁移: 添加 voice_loop_count 字段到 detection_tasks 表")
            if 'voice_interval_ms' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN voice_interval_ms INTEGER DEFAULT 1000")
                logger.info("数据库迁移: 添加 voice_interval_ms 字段到 detection_tasks 表")
            if 'voice_volume' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN voice_volume REAL DEFAULT 1.0")
                logger.info("数据库迁移: 添加 voice_volume 字段到 detection_tasks 表")

            # 添加语义检测类型（事件类型）字段
            if 'event_type' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN event_type VARCHAR(20) DEFAULT 'tracking'")
                logger.info("数据库迁移: 添加 event_type 字段到 detection_tasks 表")

            # 添加边缘库比对字段（任务级）
            if 'library_match_enabled' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN library_match_enabled BOOLEAN DEFAULT 0")
                logger.info("数据库迁移: 添加 library_match_enabled 字段到 detection_tasks 表")
            # 旧版 face_library_id/plate_library_id 数值列保留不再读写，改用库名称列
            if 'face_library_name' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN face_library_name TEXT DEFAULT ''")
                logger.info("数据库迁移: 添加 face_library_name 字段到 detection_tasks 表")
            if 'plate_library_name' not in columns:
                cursor.execute("ALTER TABLE detection_tasks ADD COLUMN plate_library_name TEXT DEFAULT ''")
                logger.info("数据库迁移: 添加 plate_library_name 字段到 detection_tasks 表")

            # 配置结构迁移总入口：
            # 1) 解码/检测/跟踪/运动配置改由 config/*.yaml 统一提供，删除四个全局配置列并新增任务级 conf_threshold
            # 2) 事件输出/显示配置的布尔开关提升为任务级独立列，删除 event_config/display_config JSON 列
            migrate_task_config_schema(cursor)

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

            # 列顺序：标量字段 + 事件输出/显示开关列（任务级独立列）+ 时间戳
            insert_columns = list(TASK_FIELD_COLUMNS) + list(TASK_SWITCH_COLUMNS) + ['created_at', 'updated_at']

            insert_values = [
                task.task_name,
                task.enabled,
                task.status,
                task.detection_type,
                task.event_type,
                task.polling_interval_frames,
                task.motion_gate_fallback_interval,
                task.schedule_cron,
                task.schedule_source,
                json.dumps(task.file_paths),
                task.file_loop,
                task.dedup_enabled,
                task.dedup_window_seconds,
                task.dedup_iou_threshold,
                task.voice_enabled,
                task.voice_mp3,
                task.voice_loop_count,
                task.voice_interval_ms,
                task.voice_volume,
                task.algorithm,
                task.model_path,
                task.model_type,  # 模型类型字段
                task.labels_file,  # 标签文件路径
                json.dumps(task.target_classes),
                task.conf_threshold,
                task.motion_detection_enabled,
                task.tracking_enabled,
                bool(task.library_match_enabled),
                task.face_library_name or '',
                task.plate_library_name or '',
            ] + [bool(getattr(task, column)) for column in TASK_SWITCH_COLUMNS] + [now, now]

            placeholders = ', '.join(['?'] * len(insert_columns))
            cursor.execute(
                f"INSERT INTO detection_tasks ({', '.join(insert_columns)}) VALUES ({placeholders})",
                insert_values
            )

            conn.commit()
            task_id = cursor.lastrowid
            logger.debug(f"[TaskManager] 添加任务成功: ID={task_id}, Name={task.task_name}")
            return task_id
    
    def get_task(self, task_id: int) -> Optional[DetectionTask]:
        """获取单个任务"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # 显式指定列名并按名称访问，避免 ALTER TABLE 导致的列顺序问题
            cursor.execute(
                f"SELECT {', '.join(TASK_SELECT_COLUMNS)} FROM detection_tasks WHERE id = ?",
                (task_id,)
            )
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
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 显式指定列名并按名称访问，避免 ALTER TABLE 导致的列顺序问题
            query = f"SELECT {', '.join(TASK_SELECT_COLUMNS)} FROM detection_tasks WHERE 1=1"
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
        """更新任务（仅接受任务表已有的标量列与开关列）"""
        logger.debug(f"[TaskManager] 更新任务: ID={task_id}, 字段={list(updates.keys())}")

        json_fields = ('target_classes', 'file_paths')

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            set_clause = []
            params = []

            for field in TASK_FIELD_COLUMNS + TASK_SWITCH_COLUMNS:
                if field not in updates:
                    continue
                value = updates[field]
                if field in TASK_SWITCH_COLUMNS:
                    value = _coerce_bool(value, _SWITCH_DEFAULTS[field])
                elif field in json_fields:
                    value = json.dumps(value)
                set_clause.append(f'{field} = ?')
                params.append(value)
            
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
    
    def _row_to_task(self, row: sqlite3.Row) -> DetectionTask:
        """将数据库行转换为 DetectionTask 对象（按列名访问，避免列顺序依赖）"""

        def cell(name: str, default=None):
            """读取列值；列为 NULL 或不存在时返回默认值"""
            try:
                value = row[name]
            except (IndexError, KeyError):
                return default
            return default if value is None else value

        def json_list(name: str) -> List:
            """解析列表类型的 JSON 列"""
            raw = cell(name)
            if not raw:
                return []
            try:
                loaded = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return []
            return loaded if isinstance(loaded, list) else []

        task = DetectionTask(
            id=cell('id'),
            task_name=cell('task_name', '') or '',
            enabled=bool(cell('enabled', True)),
            status=cell('status', 'stopped') or 'stopped',
            detection_type=cell('detection_type', 'realtime') or 'realtime',
            event_type=cell('event_type', 'tracking') or 'tracking',
            polling_interval_frames=cell('polling_interval_frames', 5),
            motion_gate_fallback_interval=cell('motion_gate_fallback_interval', 30),
            schedule_cron=cell('schedule_cron', '') or '',
            schedule_source=cell('schedule_source', '') or '',
            file_paths=json_list('file_paths'),
            file_loop=bool(cell('file_loop', False)),
            dedup_enabled=bool(cell('dedup_enabled', True)),
            dedup_window_seconds=cell('dedup_window_seconds', 300),
            dedup_iou_threshold=float(cell('dedup_iou_threshold', 0.85)),
            voice_enabled=bool(cell('voice_enabled', False)),
            voice_mp3=cell('voice_mp3', '') or '',
            voice_loop_count=cell('voice_loop_count', 1),
            voice_interval_ms=cell('voice_interval_ms', 1000),
            voice_volume=float(cell('voice_volume', 1.0)),
            algorithm=cell('algorithm', 'yolov11') or 'yolov11',
            model_path=cell('model_path', 'models/yolov11n.onnx') or 'models/yolov11n.onnx',
            model_type=cell('model_type', 'yolov11n') or 'yolov11n',
            labels_file=cell('labels_file', '') or '',
            target_classes=json_list('target_classes'),
            conf_threshold=float(cell('conf_threshold', 0.25)),
            motion_detection_enabled=bool(cell('motion_detection_enabled', True)),
            tracking_enabled=bool(cell('tracking_enabled', True)),
            library_match_enabled=bool(cell('library_match_enabled', False)),
            face_library_name=cell('face_library_name', '') or '',
            plate_library_name=cell('plate_library_name', '') or '',
            created_at=parse_datetime(cell('created_at')),
            updated_at=parse_datetime(cell('updated_at')),
        )

        # 事件输出 / 显示开关列（任务级独立列）
        for column in TASK_SWITCH_COLUMNS:
            setattr(task, column, _coerce_bool(cell(column), _SWITCH_DEFAULTS[column]))

        return task
    
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

            # 检测事件推送（后台线程，仅报警类事件）
            if event.status in ('initial', 'tracking_start'):
                try:
                    from src.push.push_service import get_push_service
                    get_push_service().push_event(event.to_dict())
                except Exception as e:
                    logger.error(f"事件推送调度失败: {e}")

            return cursor.lastrowid

    def update_event(self, event_id: str, event_type: str = None,
                     metadata: Dict = None, image_urls: Dict = None,
                     gif_urls: Dict = None, start_time=None, end_time=None,
                     bbox: List[float] = None, confidence: float = None,
                     class_name: str = None,
                     status: str = None,
                     entered_zones: List[str] = None,
                     zone_name: str = None,
                     loitered_zones: List[str] = None,
                     ai_description: str = None,
                     ai_description_model: str = None,
                     ai_description_time=None,
                     dedup_status: str = None,
                     dedup_reason: str = None,
                     status_guard: bool = False) -> bool:
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
            status: 事件状态（tracking_start/tracking_update/initial/final/duplicate/tracking_end），写入独立 status 列
            entered_zones: 目标进入过的区域列表（只增不减）
            zone_name: 当前所在区域（多个用逗号分隔）
            loitered_zones: 生命周期内累计曾徘徊的区域列表（只增不减，写入独立列）
            ai_description: 多模态大模型生成的事件描述
            ai_description_model: 生成描述使用的模型名
            ai_description_time: 描述生成时间
            dedup_status: 去重状态（normal/duplicate）
            dedup_reason: 去重判定原因

        Returns:
            是否更新成功
        """
        updates = []
        params = []

        if event_type is not None:
            updates.append('type = ?')
            params.append(event_type)

        if status is not None:
            if status_guard:
                # 终态保护：事件已为终态(final/duplicate)时不允许被非终态覆盖，
                # 原子条件更新，避免异步写库晚于终态写库执行时把状态改回 initial
                updates.append(
                    "status = CASE WHEN status IN ('final','duplicate') "
                    "THEN status ELSE ? END"
                )
                params.append(status)
            else:
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

        if ai_description is not None:
            updates.append('ai_description = ?')
            params.append(ai_description)
        if ai_description_model is not None:
            updates.append('ai_description_model = ?')
            params.append(ai_description_model)
        if ai_description_time is not None:
            updates.append('ai_description_time = ?')
            params.append(ai_description_time)
        if dedup_status is not None:
            updates.append('dedup_status = ?')
            params.append(dedup_status)
        if dedup_reason is not None:
            updates.append('dedup_reason = ?')
            params.append(dedup_reason)

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
        status: str = None,
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
                       metadata, start_time, end_time, status, entered_zones, zone_name, loitered_zones,
                       ai_description, ai_description_model, ai_description_time, dedup_status, dedup_reason,
                       created_at FROM events WHERE 1=1'''
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
            if status:
                query += ' AND status = ?'
                params.append(status)

            query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])

            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [self._row_to_event(row) for row in rows]

    def get_recent_events(
        self,
        camera_id: str = None,
        class_name: str = None,
        seconds: int = 300,
        exclude_event_id: str = None,
        limit: int = 20,
    ) -> List[EventRecord]:
        """获取近期事件（供大模型语义去重使用）

        Args:
            camera_id: 摄像机ID（同相机内去重）
            class_name: 目标类别（同类内去重）
            seconds: 时间窗口（秒），默认 5 分钟
            exclude_event_id: 排除的事件ID（当前事件本身）
            limit: 最多返回条数

        Returns:
            近期事件列表（按时间倒序）
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # created_at 由 add_event 以本地时间(datetime.now())写入，
            # 窗口比较必须用 localtime，否则 UTC 与本地时间错位 8 小时会把窗口放大
            query = '''SELECT id, event_id, task_id, camera_id, algorithm, type, class_name,
                       confidence, bbox, local_image_url, minio_image_url, ftp_image_url,
                       local_gif_url, minio_gif_url, ftp_gif_url,
                       metadata, start_time, end_time, status, entered_zones, zone_name, loitered_zones,
                       ai_description, ai_description_model, ai_description_time, dedup_status, dedup_reason,
                       created_at FROM events
                       WHERE created_at >= datetime('now', 'localtime', ?)
                       AND (dedup_status IS NULL OR dedup_status != 'duplicate')'''
            params = [f'-{seconds} seconds']

            if camera_id:
                query += ' AND camera_id = ?'
                params.append(camera_id)
            if class_name:
                query += ' AND class_name = ?'
                params.append(class_name)
            if exclude_event_id:
                query += ' AND event_id != ?'
                params.append(exclude_event_id)

            query += ' ORDER BY created_at DESC LIMIT ?'
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_event(row) for row in rows]

    def get_event_count(
        self,
        task_id: int = None,
        camera_id: str = None,
        algorithm: str = None,
        event_type: str = None,
        status: str = None
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
            if status:
                query += ' AND status = ?'
                params.append(status)

            cursor.execute(query, params)
            return cursor.fetchone()[0]

    def get_event_by_id(self, event_id: str) -> Optional[EventRecord]:
        """按 event_id 查询单条完整事件记录

        用于 GIF 就绪后获取完整数据（含 GIF URL、最终类型、最终状态、zone 轨迹）
        补推 MQTT，更新事件创建时推送的不完整数据。

        Args:
            event_id: 事件 UUID

        Returns:
            EventRecord 或 None（未找到）
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''SELECT id, event_id, task_id, camera_id, algorithm, type, class_name,
                          confidence, bbox, local_image_url, minio_image_url, ftp_image_url,
                          local_gif_url, minio_gif_url, ftp_gif_url,
                          metadata, start_time, end_time, status, entered_zones, zone_name, loitered_zones,
                          ai_description, ai_description_model, ai_description_time, dedup_status, dedup_reason,
                          created_at
                   FROM events WHERE event_id = ?''',
                (event_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_event(row)

    def delete_event(self, event_id: str) -> bool:
        """按 event_id 删除事件记录（用于未通过区域过滤的事件直接丢弃）"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM events WHERE event_id = ?', (event_id,))
            conn.commit()
            return cursor.rowcount > 0

    def clear_old_events(self, days: int = 7) -> int:
        """清理旧事件"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                'DELETE FROM events WHERE created_at < datetime("now", "localtime", ?)',
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
        
        created_at = parse_datetime(row[27])  # created_at 在 index 27
        start_time = parse_datetime(row[16])  # start_time 在 index 16
        end_time = parse_datetime(row[17])    # end_time 在 index 17
        ai_description_time = parse_datetime(row[24])  # ai_description_time 在 index 24

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
            ai_description=row[22],
            ai_description_model=row[23] or '',
            ai_description_time=ai_description_time,
            dedup_status=row[25] or '',
            dedup_reason=row[26] or '',
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
            'supported_classes': getattr(algo, 'target_classes', []),
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

    # ========================================
    # 人员统计计数区域（区域配置属于通道级别）
    #   单机画线（模式A）：通道级，存于 device_channel_pool.counting_lines
    #   多机进出（模式B）：任务级 counting_zones（in/out 通道对，跨通道关系）
    # ========================================

    def get_channel_counting_lines(self, channel_id: int) -> List[Dict]:
        """获取通道的单机画线计数区域（通道级）"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT counting_lines FROM device_channel_pool WHERE id = ?",
                (channel_id,)
            )
            row = cursor.fetchone()

        if not row:
            return []
        lines = safe_json_loads(row[0], [])
        return lines if isinstance(lines, list) else []

    def save_channel_counting_lines(self, channel_id: int, lines: List[Dict]) -> bool:
        """保存通道的单机画线计数区域（清空替换 + 通道内 zone_name 唯一校验）

        Args:
            channel_id: 通道ID（device_channel_pool.id）
            lines: 画线区域列表，每项含 {zone_name, cross_line, in_side, inertia, min_displacement, alarm_threshold}

        Returns:
            是否保存成功
        """
        try:
            names = set()
            normalized = []
            for line in lines:
                name = (line.get('zone_name') or '').strip()
                if not name:
                    raise ValueError("计数区域名称不能为空")
                if name in names:
                    raise ValueError(f"计数区域名称重复: {name}")
                names.add(name)

                cross_line = line.get('cross_line')
                if not isinstance(cross_line, list) or len(cross_line) != 2:
                    raise ValueError(f"计数区域 {name} 缺少有效的分界线（需两个点）")

                normalized.append({
                    'zone_name': name,
                    'cross_line': cross_line,
                    'in_side': int(line.get('in_side', 1)),
                    'inertia': int(line.get('inertia', 3)),
                    'min_displacement': float(line.get('min_displacement', 0.02)),
                    'alarm_threshold': max(0, int(line.get('alarm_threshold', 0) or 0)),
                })

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM device_channel_pool WHERE id = ?", (channel_id,))
                if not cursor.fetchone():
                    logger.error(f"保存通道画线计数区域失败: 通道不存在 channel_id={channel_id}")
                    return False

                cursor.execute(
                    "UPDATE device_channel_pool SET counting_lines = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(normalized, ensure_ascii=False), channel_id)
                )
                conn.commit()
                # 强制 WAL checkpoint，确保任务子进程能立即读到最新配置
                try:
                    cursor.execute("PRAGMA wal_checkpoint(FULL)")
                except Exception:
                    pass

            logger.info(f"通道画线计数区域已保存: channel_id={channel_id}, lines_count={len(normalized)}")
            return True
        except Exception as e:
            logger.error(f"保存通道画线计数区域失败: {e}")
            return False

    def delete_channel_counting_lines(self, channel_id: int) -> bool:
        """删除通道的单机画线计数区域（重置为空）"""
        return self.save_channel_counting_lines(channel_id, [])

    def get_counting_directions(self, task_id: int) -> List[Dict]:
        """获取任务的多机进出计数区域（任务级，仅方向模式）"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM counting_zones WHERE task_id = ? ORDER BY id",
                (task_id,)
            )
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_counting_zones(self, task_id: int, channel_id: int = None) -> List[Dict]:
        """按相机获取计数区域（通道级画线 + 任务级方向合并）并标注相机角色

        Args:
            task_id: 任务ID
            channel_id: 可选。提供时返回该通道参与的计数区域：
                - 单机画线：该通道 counting_lines 的全部区域，role='line'
                - 多机进出：任务级方向区域中 in/out_channels 含该通道的行，role='in'/'out'
                不提供时仅返回任务级方向区域（role 留空）。

        Returns:
            计数区域字典列表（结构与 CountingZone.from_row 兼容）
        """
        if channel_id is None:
            return self.get_counting_directions(task_id)

        channel_id = int(channel_id)
        zones = []

        for line in self.get_channel_counting_lines(channel_id):
            zones.append({
                'task_id': task_id,
                'zone_name': line.get('zone_name', ''),
                'mode': 'line',
                'channel_id': channel_id,
                'role': 'line',
                'cross_line': line.get('cross_line'),
                'in_side': line.get('in_side', 1),
                'inertia': line.get('inertia', 3),
                'min_displacement': line.get('min_displacement', 0.02),
                'alarm_threshold': int(line.get('alarm_threshold', 0) or 0),
                'in_channels': [],
                'out_channels': [],
            })

        for zone in self.get_counting_directions(task_id):
            in_channels = [int(x) for x in safe_json_loads(zone.get('in_channels'), [])]
            out_channels = [int(x) for x in safe_json_loads(zone.get('out_channels'), [])]
            if channel_id in in_channels:
                role = 'in'
            elif channel_id in out_channels:
                role = 'out'
            else:
                continue
            zone['role'] = role
            zone['mode'] = 'direction'
            zone['channel_id'] = channel_id
            zones.append(zone)

        return zones

    def save_counting_directions(self, task_id: int, zones: List[Dict]) -> bool:
        """保存任务的多机进出计数区域（清空替换 + task_id 内 zone_name 唯一校验）

        Args:
            task_id: 任务ID
            zones: 方向区域列表，每项含 {zone_name, in_channels, out_channels, alarm_threshold}

        Returns:
            是否保存成功
        """
        try:
            names = set()
            normalized = []
            for z in zones:
                name = (z.get('zone_name') or '').strip()
                if not name:
                    raise ValueError("计数区域名称不能为空")
                if name in names:
                    raise ValueError(f"计数区域名称重复: {name}")
                names.add(name)

                in_channels = [int(x) for x in (z.get('in_channels') or [])]
                out_channels = [int(x) for x in (z.get('out_channels') or [])]
                if not in_channels and not out_channels:
                    raise ValueError(f"计数区域 {name} 至少需要一个入口机或出口机")

                normalized.append((
                    name,
                    in_channels,
                    out_channels,
                    max(0, int(z.get('alarm_threshold', 0) or 0)),
                ))

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM counting_zones WHERE task_id = ?", (task_id,))
                for name, in_channels, out_channels, alarm_threshold in normalized:
                    cursor.execute('''
                        INSERT INTO counting_zones
                            (task_id, zone_name, in_channels, out_channels, alarm_threshold, updated_at)
                        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ''', (
                        task_id,
                        name,
                        json.dumps(in_channels),
                        json.dumps(out_channels),
                        alarm_threshold,
                    ))
                conn.commit()
                # 强制 WAL checkpoint，确保任务子进程能立即读到最新配置
                try:
                    cursor.execute("PRAGMA wal_checkpoint(FULL)")
                except Exception:
                    pass

            logger.info(f"多机进出计数区域已保存: task_id={task_id}, zones_count={len(normalized)}")
            return True
        except Exception as e:
            logger.error(f"保存多机进出计数区域失败: {e}")
            return False

    def get_counting_stats(self, task_id: int = None,
                           count_date: str = None) -> List[Dict]:
        """查询计数统计（默认今天；task_id 为空查询全部）

        Returns:
            [{task_id, zone_name, channel_id, in_count, out_count, current_count, count_date}]
        """
        where = []
        params = []
        if task_id is not None:
            where.append("task_id = ?")
            params.append(task_id)
        if count_date:
            where.append("count_date = ?")
            params.append(count_date)
        sql = "SELECT * FROM counting_stats"
        if where:
            sql += " WHERE " + " AND ".join(where)
        else:
            sql += " WHERE count_date = ?"
            params.append(count_date or datetime.now().strftime('%Y-%m-%d'))
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params))
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, r)) for r in cursor.fetchall()]

    def get_task_counting_total(self, task_id: int, count_date: str = None) -> Dict:
        """任务级计数汇总（按区域 group by，含任务总人数）"""
        d = count_date or datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT zone_name, SUM(in_count), SUM(out_count), SUM(current_count)
                FROM counting_stats
                WHERE task_id = ? AND count_date = ?
                GROUP BY zone_name
            ''', (task_id, d))
            rows = cursor.fetchall()
        zones = [
            {'zone_name': r[0], 'in_count': int(r[1]), 'out_count': int(r[2]),
             'current_count': int(r[3])}
            for r in rows
        ]
        total = sum(z['current_count'] for z in zones)
        return {'zones': zones, 'task_total': total, 'date': d}

    def reset_task_counting_day(self, task_id: int, count_date: str = None) -> int:
        """手动复位指定任务当日计数，返回删除行数"""
        d = count_date or datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM counting_stats WHERE task_id = ? AND count_date = ?",
                (task_id, d))
            conn.commit()
            return cursor.rowcount


# 全局任务管理器实例
_task_manager = None


def get_task_manager() -> TaskManager:
    """获取任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager