"""
全局系统配置加载器

提供统一的 system_config.yaml 读取接口，消除所有硬编码端口。
所有需要读取系统配置的模块（主进程和子进程）都应使用此加载器。
"""

import os
import yaml
import logging
from typing import Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# 系统配置文件路径
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CONFIG_PATH = _PROJECT_ROOT / 'config' / 'system_config.yaml'

# 缓存（进程内单例，避免重复读取文件）
_cached_config: Dict[str, Any] = {}
_config_loaded = False


def load_system_config(force_reload: bool = False) -> Dict[str, Any]:
    """加载 system_config.yaml

    Args:
        force_reload: 强制重新读取文件（忽略缓存）

    Returns:
        完整的系统配置字典
    """
    global _cached_config, _config_loaded

    if _config_loaded and not force_reload:
        return _cached_config

    if not _CONFIG_PATH.exists():
        logger.warning(f"系统配置文件不存在: {_CONFIG_PATH}")
        _cached_config = {}
        _config_loaded = True
        return _cached_config

    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            _cached_config = yaml.safe_load(f) or {}
        _config_loaded = True
    except Exception as e:
        logger.error(f"读取系统配置文件失败: {_CONFIG_PATH}, 错误: {e}")
        _cached_config = {}
        _config_loaded = True

    return _cached_config


def get_web_server_config() -> Dict[str, Any]:
    """获取 Web 服务器配置

    Returns:
        web_server 配置字典，包含 port、host 等
    """
    config = load_system_config()
    return config.get('web_server', {})


def get_web_server_port(default: int = 5556) -> int:
    """获取 Web 服务器端口

    Args:
        default: 配置文件缺失时的默认端口

    Returns:
        Web 服务器端口号
    """
    web_cfg = get_web_server_config()
    port = web_cfg.get('port', default)
    try:
        return int(port)
    except (TypeError, ValueError):
        return default


def get_web_server_host(default: str = '0.0.0.0') -> str:
    """获取 Web 服务器监听地址

    Args:
        default: 配置文件缺失时的默认地址

    Returns:
        Web 服务器监听地址
    """
    web_cfg = get_web_server_config()
    return web_cfg.get('host', default)


def get_webhook_base_url() -> str:
    """获取 Web 服务器的基础 URL（用于子进程回调主进程）

    Returns:
        如 http://127.0.0.1:5556
    """
    port = get_web_server_port()
    return f'http://127.0.0.1:{port}'


def get_storage_config() -> Dict[str, Any]:
    """获取存储配置（MinIO/FTP 全局配置）"""
    config = load_system_config()
    return config.get('storage', {})


def get_mqtt_config() -> Dict[str, Any]:
    """获取 MQTT 配置"""
    config = load_system_config()
    return config.get('mqtt', {})


def get_voice_config() -> Dict[str, Any]:
    """获取报警语音的输出设备配置（服务端播放用）"""
    config = load_system_config()
    return config.get('voice', {})


def get_websocket_config() -> Dict[str, Any]:
    """获取 WebSocket 配置"""
    config = load_system_config()
    return config.get('websocket', {})


def get_media_server_config() -> Dict[str, Any]:
    """获取流媒体服务器配置"""
    config = load_system_config()
    return config.get('media_server', {})


def get_database_config() -> Dict[str, Any]:
    """获取数据库配置

    Returns:
        数据库配置字典，包含 type, path, pool_enabled, pool_size
    """
    config = load_system_config()
    return config.get('database', {})


def get_db_path(default: str = 'data/tasks.db') -> str:
    """获取数据库文件路径（统一入口，消除所有硬编码）

    Args:
        default: 配置文件缺失时的默认路径

    Returns:
        数据库文件路径（相对于项目根目录）
    """
    db_cfg = get_database_config()
    return db_cfg.get('path', default)


def get_events_cleanup_config() -> Dict[str, Any]:
    """获取事件清理配置（自动清理过期事件及其存储文件）

    配置来源为 system_config.yaml 的 events_cleanup 段（单一真相源）。
    归一化字段，确保缺失时回退默认值。

    Returns:
        事件清理配置字典，包含：
        - enabled: 是否启用自动清理后台线程（默认 False）
        - retention_days: 事件保留天数，超过则清理（默认 7）
        - cleanup_interval_hours: 自动清理间隔小时数（默认 24）
        - startup_delay_seconds: 启动延迟秒数，避开系统启动高峰（默认 120）
        - batch_size: 每批清理的事件数，批间休眠让出 SQLite 锁（默认 200）
    """
    config = load_system_config()
    cleanup_cfg = config.get('events_cleanup', {})
    return {
        'enabled': cleanup_cfg.get('enabled', False),
        'retention_days': cleanup_cfg.get('retention_days', 7),
        'cleanup_interval_hours': cleanup_cfg.get('cleanup_interval_hours', 24),
        'startup_delay_seconds': cleanup_cfg.get('startup_delay_seconds', 120),
        'batch_size': cleanup_cfg.get('batch_size', 200),
    }


def get_event_dedup_config() -> Dict[str, Any]:
    """获取事件规则去重配置（事件管理功能，独立于大模型，不消耗 token）

    配置来源为 system_config.yaml 的 event_dedup 段（单一真相源）。
    归一化字段，确保缺失时回退默认值。

    Returns:
        事件规则去重配置字典，包含：
        - enabled: 是否启用规则去重（默认 True）
        - window_seconds: 去重时间窗口秒数（默认 300）
        - iou_threshold: 目标框 IoU 重叠阈值（默认 0.3）
    """
    config = load_system_config()
    dedup_cfg = config.get('event_dedup', {})
    return {
        'enabled': dedup_cfg.get('enabled', True),
        'window_seconds': dedup_cfg.get('window_seconds', 300),
        'iou_threshold': dedup_cfg.get('iou_threshold', 0.3),
    }


def get_node_management_config() -> Dict[str, Any]:
    """获取节点管理配置（向边缘平台注册/上报心跳的客户端配置）

    配置来源为 system_config.yaml 的 node_management 段（单一真相源）。
    归一化关键字段类型，缺失时回退默认值。

    Returns:
        节点管理配置字典，包含 enabled/node_id/agent_token/control_plane_url 等
    """
    config = load_system_config()
    raw = config.get('node_management', {}) or {}
    return {
        'enabled': bool(raw.get('enabled', False)),
        'node_id': str(raw.get('node_id', '') or ''),
        'agent_token': str(raw.get('agent_token', '') or ''),
        'control_plane_url': str(raw.get('control_plane_url', 'http://localhost:48080/admin-api/node/agent') or ''),
        'heartbeat_interval': int(raw.get('heartbeat_interval', 10) or 10),
        'agent_listen_host': str(raw.get('agent_listen_host', '0.0.0.0') or '0.0.0.0'),
        'agent_listen_port': int(raw.get('agent_listen_port', 9100) or 9100),
        'agent_version': str(raw.get('agent_version', '1.0.0') or '1.0.0'),
        'ai_root': str(raw.get('ai_root', '/opt/AI') or '/opt/AI'),
        'video_root': str(raw.get('video_root', '/opt/VIDEO') or '/opt/VIDEO'),
        'media_cluster_root': str(raw.get('media_cluster_root', '/opt/media-cluster') or '/opt/media-cluster'),
    }
