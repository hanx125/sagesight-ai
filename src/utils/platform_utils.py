"""
平台检测工具

提供运行平台识别能力，用于：
- RKNN 后端可用性校验（RK3588 → RGA 预处理 + NPU 推理）
- RGA 预处理路径启用判断
- OpenCV CUDA 预处理路径启用判断（Linux）

注意：解码器 preset 由 decoder_config 独立设置，本模块不参与解码 preset 选择
（解码与检测配置职责分离，互不干预）。

支持平台：
- rk3588: 瑞芯微 RK3588（NPU + RGA + RKMPP）
- windows: Windows（DXVA2 / CUDA）
- linux: 通用 Linux（VAAPI / CUDA）
- unknown: 未知平台
"""

import os
import platform
import logging

logger = logging.getLogger(__name__)

# RK 平台库目录（项目自带 aarch64 Linux 库）
_RK_LIB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'lib', 'rk3588')
)


def get_rk_lib_dir() -> str:
    """返回 RK 平台库目录绝对路径（src/lib/rk3588）"""
    return _RK_LIB_DIR


def get_rk_lib_path(lib_name: str) -> str:
    """返回指定 RK 库文件的绝对路径"""
    return os.path.join(_RK_LIB_DIR, lib_name)


def is_rk3588() -> bool:
    """
    检测当前是否运行在 RK3588 平台

    判断优先级：
    1. 设备树 compatible 含 'rk3588'（最可靠，仅在 RK 平台可用）
    2. 回退：aarch64 架构 + 项目自带 librknnrt.so 存在

    Returns:
        bool: 是否在 RK3588 平台
    """
    # 1. 设备树 compatible（RK 平台特有，含 null 分隔符需按字节读）
    try:
        with open('/proc/device-tree/compatible', 'rb') as f:
            compat = f.read().decode('utf-8', errors='ignore')
        if 'rk3588' in compat:
            return True
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # 2. 回退：aarch64 + 项目自带 RK 运行时库存在
    if platform.machine() == 'aarch64':
        return os.path.exists(get_rk_lib_path('librknnrt.so'))

    return False


def get_platform() -> str:
    """
    返回当前平台标识

    Returns:
        str: 'rk3588' / 'windows' / 'linux' / 'unknown'
    """
    if is_rk3588():
        return 'rk3588'
    if os.name == 'nt':
        return 'windows'
    if platform.system() == 'Linux':
        return 'linux'
    return 'unknown'


def is_rk_platform() -> bool:
    """是否为瑞芯微 RK 系列 NPU 平台（当前仅识别 RK3588）"""
    return is_rk3588()
