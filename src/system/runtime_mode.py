# -*- coding: utf-8 -*-
"""
运行模式（标准 / 接入边缘）读写

模式为部署级全局配置，持久化在 data 库 system_settings 表 key=runtime_mode：
- standalone：标准模式，本机独立运行，设备/流媒体/驾驶舱均由本平台管理（默认值）
- cluster：接入模式，设备与流媒体由边缘平台统一下发和管理

单一真相源：本模块只读写 system_settings，不落 system_config.yaml，
避免配置文件与数据库出现两份模式值。
"""

import logging

logger = logging.getLogger(__name__)

MODE_STANDALONE = 'standalone'
MODE_CLUSTER = 'cluster'

VALID_MODES = (MODE_STANDALONE, MODE_CLUSTER)

MODE_LABELS = {
    MODE_STANDALONE: '标准模式',
    MODE_CLUSTER: '接入模式',
}

SETTING_KEY = 'runtime_mode'

# 各模式不可用页面的提示文案（前端登录页/导航/系统管理标签共用）
MODE_BLOCKED_MESSAGES = {
    MODE_STANDALONE: {
        'node': '标准模式下无需配置边缘节点；如需接入边缘平台，请在登录页选择“接入模式”后重新登录。',
    },
    MODE_CLUSTER: {
        'monitor': '接入模式下，驾驶舱由边缘平台统一提供，本页面不可用。请在边缘平台查看驾驶舱。',
        'cameras': '接入模式下，设备由边缘平台统一下发与管理，本页面不可用。请在边缘平台进行设备管理。',
        'streaming': '接入模式下，流媒体服务器由边缘平台统一管理，本页面不可用。请在边缘平台配置流媒体。',
    },
}


def get_runtime_mode() -> str:
    """读取当前运行模式；配置缺失或非法时回退标准模式"""
    try:
        from src.system.system_manager import get_system_manager
        value = get_system_manager().get_setting(SETTING_KEY, MODE_STANDALONE)
    except Exception as e:
        logger.warning(f'读取运行模式失败，按标准模式处理: {e}')
        return MODE_STANDALONE
    if value not in VALID_MODES:
        logger.warning(f'运行模式配置非法（{value}），按标准模式处理')
        return MODE_STANDALONE
    return value


def set_runtime_mode(mode: str) -> bool:
    """持久化运行模式；非法值直接拒绝"""
    if mode not in VALID_MODES:
        logger.warning(f'拒绝写入非法运行模式: {mode}')
        return False
    try:
        from src.system.system_manager import get_system_manager
        return bool(get_system_manager().set_setting(SETTING_KEY, mode))
    except Exception as e:
        logger.error(f'写入运行模式失败: {e}')
        return False


def get_blocked_message(page_key: str, mode: str = None) -> str:
    """取指定页面在当前模式下不可用的提示文案"""
    mode = mode or get_runtime_mode()
    return MODE_BLOCKED_MESSAGES.get(mode, {}).get(page_key, '当前运行模式下该页面不可用')
