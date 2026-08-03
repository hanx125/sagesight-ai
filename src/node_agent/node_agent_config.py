"""节点管理配置加载器。

从 system_config.yaml 的 node_management 段读取配置，并提供给节点管理
各子模块使用。同时将关键参数同步写入进程环境变量，使工作负载/媒体栈
管理代码无需改造即可读取（它们沿用 os.environ 的读取方式）。
"""

import logging
import os
from typing import Any, Dict

from src.system.system_config_loader import get_node_management_config

logger = logging.getLogger(__name__)


def sync_to_environment(config: Dict[str, Any] = None) -> None:
    """将节点管理配置同步到环境变量。

    工作负载管理与媒体栈管理内部沿用了 os.environ 读取参数的方式，
    在启动时统一注入，避免改造这些子模块的读取逻辑。
    """
    cfg = config or get_node_management_config()
    env_map = {
        'NODE_ID': cfg.get('node_id', ''),
        'AGENT_TOKEN': cfg.get('agent_token', ''),
        'CONTROL_PLANE_URL': cfg.get('control_plane_url', ''),
        'HEARTBEAT_INTERVAL': str(cfg.get('heartbeat_interval', 10)),
        'AGENT_LISTEN_HOST': cfg.get('agent_listen_host', '0.0.0.0'),
        'AGENT_LISTEN_PORT': str(cfg.get('agent_listen_port', 9100)),
        'AI_ROOT': cfg.get('ai_root', '/opt/AI'),
        'VIDEO_ROOT': cfg.get('video_root', '/opt/VIDEO'),
        'MEDIA_CLUSTER_ROOT': cfg.get('media_cluster_root', '/opt/media-cluster'),
    }
    for key, value in env_map.items():
        if value:
            os.environ[key] = value
