"""
OpenCV CUDA 硬件预处理处理器（GpuMat）

通过 cv2.cuda.GpuMat 把 region 裁剪后的 RGB 上传到 GPU，用 GPU 做 resize，
加速预处理中的缩放环节。仅在带 NVIDIA GPU 且 opencv 编译了 CUDA 的 Linux 上可用；
不可用时由上层自动回退到 cv2 CPU 路径（create_tensor_input）。

设计说明：
- 裁剪 + YUV→RGB 复用现有 CPU 实现（yuv_region_2_rgb，已验证正确，
  处理 region 越界/letterbox padding），保证与 CPU 路径像素级一致，
  避免检测框与视频帧错位。
- 仅 resize 交给 GPU（resize 是预处理中计算量最大的环节）；
  cv2.cuda.cvtColor 对 I420 的支持因 opencv 构建而异，为避免像素错位风险，
  不把 YUV→RGB 放到 GPU。
- 每帧每 region 一次 upload/download，PCIe 带宽足够（640×640×3 ≈ 1.2MB）。

可用性判定：
- cv2.cuda 模块存在（opencv 编译时启用了 CUDA）
- cv2.cuda.getCudaEnabledDeviceCount() > 0（有可用 CUDA 设备）
两者都满足才返回 True，否则上层回退 cv2 CPU。
"""

import logging
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CudaProcessor:
    """
    OpenCV CUDA 硬件加速处理器

    使用方式：
        proc = CudaProcessor()
        if proc.is_available():
            rgb = proc.crop_resize_yuv420p_to_rgb(yuv_frame, region, dst_w, dst_h)

    若 cv2 未编译 CUDA 或无可用设备，is_available() 返回 False，上层回退 cv2。
    """

    _instance: Optional["CudaProcessor"] = None

    def __init__(self):
        self._available = False
        self._device_count = 0
        self._init_cuda()

    def _init_cuda(self):
        """检测 cv2.cuda 可用性"""
        if not hasattr(cv2, 'cuda'):
            logger.info("[CudaProcessor] cv2 未编译 CUDA 模块，不可用")
            return
        try:
            self._device_count = int(cv2.cuda.getCudaEnabledDeviceCount())
        except Exception as e:
            logger.warning(f"[CudaProcessor] 获取 CUDA 设备数失败：{e}")
            return

        if self._device_count <= 0:
            logger.info("[CudaProcessor] 无可用 CUDA 设备，不可用")
            return

        # 验证 GpuMat 可创建（确保 cv2.cuda 真正可用）
        try:
            _ = cv2.cuda_GpuMat()
            self._available = True
            logger.info(
                f"[CudaProcessor] OpenCV CUDA 可用，设备数={self._device_count}"
            )
        except Exception as e:
            logger.warning(f"[CudaProcessor] GpuMat 创建失败，不可用：{e}")

    def is_available(self) -> bool:
        """CUDA 预处理是否可用"""
        return self._available

    def crop_resize_yuv420p_to_rgb(
        self, yuv_frame: np.ndarray, region, dst_w: int, dst_h: int
    ) -> np.ndarray:
        """
        从 I420 帧裁剪 region → YUV→RGB → GPU resize 为 (dst_h, dst_w, 3) RGB uint8

        裁剪 + YUV→RGB 在 CPU 完成（复用 yuv_region_2_rgb，与 CPU 路径像素级一致），
        resize 在 GPU 完成（INTER_LINEAR）。

        Args:
            yuv_frame: (H*3//2, W) 合并 I420 帧（merge_yuv_planes 输出）
            region: (x1, y1, x2, y2) 原图坐标
            dst_w: 目标宽度（模型输入宽）
            dst_h: 目标高度（模型输入高）

        Returns:
            (dst_h, dst_w, 3) uint8 RGB

        Raises:
            RuntimeError: CUDA 不可用或 GPU resize 失败（上层应捕获并回退 cv2）
        """
        if not self._available:
            raise RuntimeError("CUDA 不可用")

        # 延迟导入避免循环依赖
        from src.detectors.region_utils import yuv_region_2_rgb

        # 1. CPU 裁剪 + YUV→RGB（与 create_tensor_input 像素级一致）
        rgb_cropped = yuv_region_2_rgb(yuv_frame, region)

        # 2. 上传到 GPU
        gpu_rgb = cv2.cuda_GpuMat()
        gpu_rgb.upload(rgb_cropped)

        # 3. GPU resize（已与目标尺寸一致时跳过 resize）
        cropped_h, cropped_w = rgb_cropped.shape[:2]
        if cropped_w != dst_w or cropped_h != dst_h:
            gpu_resized = cv2.cuda.resize(
                gpu_rgb, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR
            )
        else:
            gpu_resized = gpu_rgb

        # 4. 下载回 CPU
        result = gpu_resized.download()
        if result is None or result.size == 0:
            raise RuntimeError("GPU resize 下载失败")
        return result


# ========================================
# 单例访问
# ========================================
_processor_instance: Optional[CudaProcessor] = None


def get_cuda_processor() -> Optional[CudaProcessor]:
    """获取 CudaProcessor 单例（首次调用延迟初始化）"""
    global _processor_instance
    if _processor_instance is None:
        try:
            _processor_instance = CudaProcessor()
        except Exception as e:
            logger.warning(f"[CudaProcessor] 初始化失败：{e}")
            _processor_instance = None
    return _processor_instance
