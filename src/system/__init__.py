"""
SageSight AI - 系统管理模块
"""

from src.system.system_manager import SystemManager, get_system_manager, StreamingServer
from src.system.system_api import system_bp, register_system_blueprint

__all__ = [
    'SystemManager',
    'get_system_manager',
    'StreamingServer',
    'system_bp',
    'register_system_blueprint'
]