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

import glob
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


# CUDA/cuDNN 运行库目录的常见安装位置（仅 Windows 需要显式注册）
_CUDA_DIR_PATTERN = r'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.*\bin'
_CUDNN_DIR_PATTERN = r'C:\Program Files\NVIDIA\CUDNN\v9*\bin\12.*\x64'

_dll_dirs_registered = False


def _detect_cuda_dirs() -> tuple:
    """自动探测 CUDA 与 cuDNN 运行库目录（取版本号最高的一个）"""
    cuda_dirs = sorted(glob.glob(_CUDA_DIR_PATTERN))
    cudnn_dirs = sorted(glob.glob(_CUDNN_DIR_PATTERN))
    return (cuda_dirs[-1] if cuda_dirs else ''), (cudnn_dirs[-1] if cudnn_dirs else '')


def _load_lib_paths_from_config() -> tuple:
    """从 config/detector_config.yaml 读取 cuda_lib_path / cudnn_lib_path"""
    try:
        import yaml

        config_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'detector_config.yaml')
        )
        with open(config_path, 'r', encoding='utf-8') as f:
            detector = (yaml.safe_load(f) or {}).get('detector', {}) or {}
        return detector.get('cuda_lib_path') or '', detector.get('cudnn_lib_path') or ''
    except Exception as e:
        logger.debug(f"[平台] 读取 CUDA 运行库路径配置失败，改用自动探测: {e}")
        return '', ''


def register_cuda_dll_dirs(cuda_lib_path: str = '', cudnn_lib_path: str = '') -> list:
    """
    将 CUDA / cuDNN 运行库目录注册到进程的 DLL 搜索路径

    背景：Python 3.8+ 收紧了 Windows 的 DLL 搜索策略，仅把目录放进 PATH
    并不保证能被加载到。ONNX Runtime 的 CUDA EP 找不到 cuDNN 时，
    卷积等算子会退化为兜底实现（日志出现 Conv running in Fallback mode），
    推理速度大幅下降，因此需要在建会话前显式注册目录。

    路径优先级：显式入参 > 配置文件 > 自动探测。仅 Windows 生效，幂等。

    Returns:
        实际注册成功的目录列表
    """
    global _dll_dirs_registered

    if os.name != 'nt' or _dll_dirs_registered:
        return []

    cfg_cuda, cfg_cudnn = _load_lib_paths_from_config()
    auto_cuda, auto_cudnn = _detect_cuda_dirs()

    registered = []
    for path in (cuda_lib_path or cfg_cuda or auto_cuda,
                 cudnn_lib_path or cfg_cudnn or auto_cudnn):
        if not path or not os.path.isdir(path):
            continue
        try:
            os.add_dll_directory(path)
            # 关键：ONNX Runtime 的原生代码通过进程环境变量 PATH 解析依赖，
            # 仅调用 add_dll_directory 不足以让 CUDA/cuDNN 被找到，必须同时加入 PATH
            os.environ['PATH'] = path + os.pathsep + os.environ.get('PATH', '')
            registered.append(path)
        except OSError as e:
            logger.warning(f"[平台] 注册 DLL 目录失败: {path}, error={e}")

    _dll_dirs_registered = True
    if registered:
        logger.info(f"[平台] 已注册 CUDA/cuDNN 运行库目录: {registered}")
    else:
        logger.warning("[平台] 未找到 CUDA/cuDNN 运行库目录，GPU 推理可能无法启用 cuDNN")
    return registered
