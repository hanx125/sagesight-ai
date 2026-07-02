"""
摄像机管理模块

提供摄像机设备的注册、管理、ONVIF发现等功能。
"""

from src.cameras.camera_manager import CameraManager, get_camera_manager
from src.cameras.camera_api import register_camera_blueprint

__all__ = [
    'CameraManager',
    'get_camera_manager',
    'register_camera_blueprint'
]