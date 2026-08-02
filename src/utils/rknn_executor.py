"""
RKNN Executor - RK3588/RKNN 推理引擎包装类

用于在瑞芯微 RK3588 等平台上加载和运行 RKNN 模型。

依赖说明：
- 盒子上需 pip install rknn-toolkit-lite2（提供 rknnlite.api.RKNNLite）
- 项目自带 librknnrt.so（src/lib/rk3588/），导入 rknnlite 前预加载兜底，
  确保运行时符号可见（rknnlite wheel 通常自带 librknnrt.so，预加载仅作兜底）。

支持两套 API：
- rknnlite（rknn-toolkit-lite2，板端推理）：init_runtime(core_mask=...)
- rknn（rknn-toolkit2，PC 端推理/调试）：init_runtime(target=..., device_id=...)
"""

import ctypes
import logging
import os

from src.utils.platform_utils import get_rk_lib_path, is_rk3588

logger = logging.getLogger(__name__)

# ========================================
# 预加载 librknnrt.so（确保 rknnlite 能解析运行时符号）
# 仅在 RK3588 平台执行——Windows 上加载 .so 会触发"映像损坏"系统弹窗
# ========================================
if is_rk3588():
    try:
        _librknnrt_path = get_rk_lib_path('librknnrt.so')
        if os.path.exists(_librknnrt_path):
            ctypes.CDLL(_librknnrt_path)
    except OSError:
        pass
    except Exception:
        pass

# ========================================
# RKNN API 导入（仅在 RK3588 平台可用）
# ========================================
RKNN_AVAILABLE = False
RKNN_BACKEND = None  # 'rknnlite' 或 'rknn'

try:
    # 优先：板端推理 API（rknn-toolkit-lite2）
    from rknnlite.api import RKNNLite
    RKNN_AVAILABLE = True
    RKNN_BACKEND = 'rknnlite'
    logger.debug("[RKNNExecutor] RKNNLite API 已加载（rknn-toolkit-lite2）")
except ImportError:
    try:
        # 回退：PC 端 API（rknn-toolkit2，仅用于调试/转换后本地推理）
        from rknn.api import RKNN
        RKNN_AVAILABLE = True
        RKNN_BACKEND = 'rknn'
        logger.debug("[RKNNExecutor] RKNN API 已加载（rknn-toolkit2）")
    except ImportError:
        logger.debug("[RKNNExecutor] RKNN/RKNNLite API 未安装（仅 RK3588 平台可用）")


# RK3588 NPU 核心掩码常量（RKNNLite 专用）
# 用于 core_mask 参数：0=自动，1=单核NPU0，2=单核NPU1，3=双核负载均衡
RKNN_NPU_CORE_0 = 1
RKNN_NPU_CORE_1 = 2
RKNN_NPU_CORE_0_1 = 3  # 双核负载均衡（RK3588 专属）


class RKNNExecutor:
    """
    RKNN 模型推理引擎

    支持平台：
    - RK3588 (RKNPU2，双核 NPU)
    - RK3566 / RK3568 (RKNPU1)
    - RV1103 / RV1106

    使用方式：
        executor = RKNNExecutor('models/yolov11n.rknn', target='rk3588', core_mask=3)
        outputs = executor.run(input_image)
        executor.release()
    """

    def __init__(self, model_path: str, target: str = None,
                 device_id: str = None, core_mask: int = 0):
        """
        初始化 RKNN 推理引擎

        Args:
            model_path: RKNN 模型文件路径（.rknn 格式）
            target: 目标平台（'rk3588' / 'rk3566' / 'rk3568' / 'rv1103' / 'rv1106'）
                    仅 rknn-toolkit2 API 使用；rknnlite 自动探测板端平台
            device_id: 设备 ID（多设备时使用，仅 rknn-toolkit2 API）
            core_mask: NPU 核心掩码（仅 rknnlite API，RK3588 双核场景）
                       0=自动, 1=NPU0, 2=NPU1, 3=双核负载均衡

        Raises:
            ImportError: RKNN API 未安装
            RuntimeError: 模型加载或 runtime 初始化失败
        """
        if not RKNN_AVAILABLE:
            raise ImportError(
                "RKNN API 未安装，请在 RK3588 平台执行：\n"
                "  pip install rknn-toolkit-lite2"
            )

        # 根据后端类型创建实例
        if RKNN_BACKEND == 'rknnlite':
            self.rknn = RKNNLite(verbose=False)
            self._backend = 'rknnlite'
        else:
            self.rknn = RKNN()
            self._backend = 'rknn'

        self.model_path = model_path
        self.target = target
        self.device_id = device_id
        self.core_mask = core_mask

        # 加载 RKNN 模型
        logger.debug(f"[RKNNExecutor] 加载模型: {model_path} (后端: {self._backend})")
        ret = self.rknn.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f"加载 RKNN 模型失败: {model_path}, 错误码: {ret}")

        # 初始化 runtime 环境
        logger.debug(f"[RKNNExecutor] 初始化 runtime 环境 (core_mask={core_mask}, target={target})")
        if self._backend == 'rknnlite':
            # 板端：core_mask 选择 NPU 核心
            ret = self.rknn.init_runtime(core_mask=core_mask)
        else:
            # PC 端：target + device_id 连接板端（adb 或本地）
            if target is None:
                ret = self.rknn.init_runtime()
            else:
                ret = self.rknn.init_runtime(target=target, device_id=device_id)

        if ret != 0:
            raise RuntimeError(
                f"初始化 runtime 失败, target={target}, core_mask={core_mask}, 错误码: {ret}"
            )

        logger.debug("[RKNNExecutor] Runtime 环境初始化完成")

    def run(self, inputs):
        """
        执行推理

        Args:
            inputs: 输入数据（numpy 数组或列表）。RKNN YOLO 模型通常接受
                    (1, H, W, 3) uint8 NHWC（归一化已烘焙进 .rknn）

        Returns:
            list[ndarray]: 模型输出张量列表
            （YOLO11 RKNN 输出 6 个张量：3 尺度 × {box_dfl, class_conf}）
        """
        if self.rknn is None:
            raise RuntimeError("RKNN 引擎已释放，无法推理")

        # 统一输入为列表
        if not isinstance(inputs, (list, tuple)):
            inputs = [inputs]

        # 执行推理（rknnlite 与 rknn 均提供 inference 方法）
        result = self.rknn.inference(inputs=inputs)
        return result

    def release(self):
        """释放 RKNN 引擎资源"""
        if self.rknn is not None:
            self.rknn.release()
            self.rknn = None
            logger.debug("[RKNNExecutor] RKNN 引擎已释放")

    def __del__(self):
        """析构函数，自动释放资源"""
        try:
            self.release()
        except Exception:
            pass


def is_rknn_available() -> bool:
    """
    检查 RKNN API 是否可用

    Returns:
        bool: True 表示 RKNN API 已安装
    """
    return RKNN_AVAILABLE


def get_rknn_backend() -> str:
    """
    获取 RKNN 后端类型

    Returns:
        str: 'rknnlite' / 'rknn' / None
    """
    return RKNN_BACKEND


def get_rknn_platform_info() -> dict:
    """
    获取 RKNN 平台信息

    Returns:
        dict: 平台信息字典
    """
    if not RKNN_AVAILABLE:
        return {
            'available': False,
            'message': 'RKNN API 未安装（请在 RK3588 平台 pip install rknn-toolkit-lite2）',
        }

    return {
        'available': True,
        'backend': RKNN_BACKEND,
        'supported_targets': ['rk3588', 'rk3566', 'rk3568', 'rv1103', 'rv1106'],
        'message': f'RKNN API 已加载（{RKNN_BACKEND}）',
    }
