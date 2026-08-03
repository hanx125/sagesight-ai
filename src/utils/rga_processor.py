"""
RGA 硬件 2D 加速处理器（ctypes 封装）

通过 ctypes 调用 Rockchip librga.so，实现 region 裁剪 + 缩放 + YUV→RGB 的硬件加速。
仅在 RK3588 平台可用；加载失败或调用异常时由上层自动回退到 cv2 路径。

核心 RGA 调用链（缓冲区池化复用，handle 永久持有）：
  1. 首次按尺寸 importbuffer_virtualaddr(va, param) → 注册外部缓冲，返回 handle（仅一次）
  2. wrapbuffer_handle_t(handle, w,h, wstride,hstride, format) → 包装成 rga_buffer_t（仅一次）
  3. 每帧 improcess(src, dst, pat, srect, drect, prect, usage) → 裁剪+缩放+格式转换
  4. 进程退出时 releasebuffer_handle(handle) → 释放（仅一次）

为何池化：importbuffer/releasebuffer 每次都向内核 dma-buf 注册/注销 sg 表，
高频调用（每秒数十次）会耗尽内核资源导致 RGA_BLIT fail: Invalid argument。

格式说明：
  - 源：RK_FORMAT_YCbCr_420_P（标准 I420：Y(H×W) + Cb(H/2×W/2) + Cr(H/2×W/2) 连续）
  - 目的：RK_FORMAT_RGB_888（H×W×3 RGB）

注意：项目 merge_yuv_planes 输出的是 cv2 优化的非标准 I420 布局（U/V reshape 为 (H/4,W)），
      RGA 需要标准 I420，故本模块从合并帧重建标准布局再交给 RGA。
"""

import ctypes
import logging
import os
import threading
from typing import Dict, Optional, Tuple

import numpy as np

from src.utils.platform_utils import get_rk_lib_path, is_rk3588

logger = logging.getLogger(__name__)

# ========================================
# RGA 格式常量（来自 rga.h，左移 8 位）
# ========================================
RK_FORMAT_RGB_888 = 0x2 << 8      # R:G:B 8:8:8
RK_FORMAT_YCbCr_420_P = 0xb << 8  # 标准 I420（3 平面 YUV420p）

# 使用标志（来自 im2d_type.h IM_USAGE）
IM_SYNC = 1 << 19  # 同步等待完成

# imconfig 配置项（来自 im2d_type.h IM_CONFIG）
IM_CONFIG_SCHEDULER_CORE = 1 << 0  # 调度核心选择

# RGA 调度核心位掩码（来自 im2d_type.h IM_SCHEDULER_CORE）
# RK3588 有 RGA3 core0/core1 与 RGA2 core
IM_SCHEDULER_RGA3_CORE0 = 1 << 0
IM_SCHEDULER_RGA3_CORE1 = 1 << 1
IM_SCHEDULER_RGA2_CORE  = 1 << 2
# I420（YCbCr_420_P 全平面分离格式）仅 RGA2 核支持，RGA3 核不支持该格式。
# 强制 RGA3 会导致 RGA_BLIT fail: Invalid argument，故 I420→RGB 走 RGA2 核。
IM_SCHEDULER_FOR_I420 = IM_SCHEDULER_RGA2_CORE

# 状态码（来自 im2d_type.h IM_STATUS）
IM_STATUS_SUCCESS = 1
IM_STATUS_NOERROR = 2

# 插值模式（来自 im2d_type.h IM_SCALE_MODE）
INTER_LINEAR = 1


# ========================================
# ctypes 结构体定义（来自 im2d_type.h）
# ========================================
class im_rect(ctypes.Structure):
    """RGA 矩形（im2d_type.h:262）"""
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
    ]


class im_colorkey_range(ctypes.Structure):
    _fields_ = [
        ("max", ctypes.c_int),
        ("min", ctypes.c_int),
    ]


class im_nn_t(ctypes.Structure):
    _fields_ = [
        ("scale_r", ctypes.c_int),
        ("scale_g", ctypes.c_int),
        ("scale_b", ctypes.c_int),
        ("offset_r", ctypes.c_int),
        ("offset_g", ctypes.c_int),
        ("offset_b", ctypes.c_int),
    ]


class rga_buffer_t(ctypes.Structure):
    """RGA 缓冲描述符（im2d_type.h:285-307）

    字段顺序与 C 结构体严格一致，ctypes 按 C ABI 处理对齐填充。
    """
    _fields_ = [
        ("vir_addr", ctypes.c_void_p),
        ("phy_addr", ctypes.c_void_p),
        ("fd", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("wstride", ctypes.c_int),
        ("hstride", ctypes.c_int),
        ("format", ctypes.c_int),
        ("color_space_mode", ctypes.c_int),
        ("global_alpha", ctypes.c_int),
        ("rd_mode", ctypes.c_int),
        ("color", ctypes.c_int),
        ("colorkey_range", im_colorkey_range),
        ("nn", im_nn_t),
        ("rop_code", ctypes.c_int),
        ("handle", ctypes.c_uint),
    ]


class im_handle_param_t(ctypes.Structure):
    """缓冲导入参数（im2d_type.h:430）"""
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("format", ctypes.c_uint32),
    ]


# ========================================
# 池化缓冲区：复用 handle，避免高频 import/release 耗尽内核 dma-buf 资源
# ========================================
class _PooledBuffer:
    """固定 VA 的 RGA 缓冲区：importbuffer 仅调用一次，handle 永久持有。

    每次 RGA 调用只更新 srect 和拷入新数据，不再 import/release，
    从根本上消除"每帧向内核驱动注册/注销外部缓冲"导致的资源耗尽。
    """

    __slots__ = ("width", "height", "format", "array", "va", "handle", "rga_buf")

    def __init__(self, lib, width: int, height: int, rga_format: int):
        self.width = width
        self.height = height
        self.format = rga_format
        # 按格式计算一维连续缓冲大小
        if rga_format == RK_FORMAT_YCbCr_420_P:
            buf_size = width * height * 3 // 2
        elif rga_format == RK_FORMAT_RGB_888:
            buf_size = width * height * 3
        else:
            buf_size = width * height * 4
        # np.zeros 保证内存连续且零初始化，VA 在对象生命周期内稳定不变
        self.array = np.zeros(buf_size, dtype=np.uint8)
        self.va = self.array.ctypes.data_as(ctypes.c_void_p)
        # import 一次，永久持有 handle
        param = im_handle_param_t(
            width=ctypes.c_uint32(width),
            height=ctypes.c_uint32(height),
            format=ctypes.c_uint32(rga_format),
        )
        self.handle = lib.importbuffer_virtualaddr(self.va, ctypes.byref(param))
        if self.handle == 0:
            raise RuntimeError(
                f"importbuffer_virtualaddr 失败 (w={width},h={height},fmt={rga_format})"
            )
        # wrap 一次，永久持有 rga_buffer_t
        self.rga_buf = lib.wrapbuffer_handle_t(
            self.handle, width, height, width, height, rga_format)
        self.rga_buf.vir_addr = self.va

    def release(self, lib):
        """释放 handle（仅在处理器关闭/尺寸变更时调用）"""
        if self.handle:
            try:
                lib.releasebuffer_handle(self.handle)
            except Exception:
                pass
            self.handle = 0


# ========================================
# RGAProcessor 单例
# ========================================
class RGAProcessor:
    """
    RGA 硬件加速处理器

    使用方式：
        proc = RGAProcessor()
        if proc.is_available():
            rgb = proc.crop_resize_yuv420p_to_rgb(yuv_frame, region, dst_w, dst_h)

    若 librga.so 加载失败或自检失败，is_available() 返回 False，上层回退 cv2。
    """

    _instance: Optional["RGAProcessor"] = None

    def __init__(self):
        self._lib = None
        self._available = False
        self._has_imconfig = False
        self._has_wrapbuffer_va = False
        # 按尺寸池化缓冲：key=(w,h) → _PooledBuffer，handle 永久复用
        self._src_pool: Dict[Tuple[int, int], _PooledBuffer] = {}
        self._dst_pool: Dict[Tuple[int, int], _PooledBuffer] = {}
        # RGA 是单一硬件设备，improcess 调用需串行化
        self._lock = threading.Lock()
        self._init_librga()
        # 加载成功后指定调度核心为 RGA2（I420 仅 RGA2 核支持）
        if self.is_available() and self._has_imconfig:
            try:
                ret = self._lib.imconfig(IM_CONFIG_SCHEDULER_CORE, IM_SCHEDULER_FOR_I420)
                if ret != IM_STATUS_SUCCESS and ret != IM_STATUS_NOERROR:
                    logger.warning(f"[RGAProcessor] imconfig 设置调度核心返回：{ret}")
                else:
                    logger.info("[RGAProcessor] 调度核心已设为 RGA2（I420 兼容）")
            except Exception as e:
                logger.warning(f"[RGAProcessor] imconfig 设置调度核心失败：{e}")

    def _init_librga(self):
        """加载 librga.so 并绑定 C 函数符号"""
        lib_path = get_rk_lib_path('librga.so')

        # 仅在 Linux/RK 平台尝试加载（Windows 上 .so 无法加载）
        if os.name != 'nt' and os.path.exists(lib_path):
            try:
                self._lib = ctypes.CDLL(lib_path)
                self._bind_functions()
                logger.info(f"[RGAProcessor] librga.so 加载成功：{lib_path}")
            except OSError as e:
                logger.warning(f"[RGAProcessor] librga.so 加载失败：{e}")
                self._lib = None
        else:
            # 非目标平台：尝试系统 librga.so（盒子可能装在 /usr/lib）
            if is_rk3588():
                try:
                    self._lib = ctypes.CDLL('librga.so')
                    self._bind_functions()
                    logger.info("[RGAProcessor] 系统库 librga.so 加载成功")
                except OSError as e:
                    logger.warning(f"[RGAProcessor] 系统库 librga.so 加载失败：{e}")
                    self._lib = None

    def _bind_functions(self):
        """绑定 RGA C 函数符号与类型签名"""
        lib = self._lib

        # importbuffer_virtualaddr(void* va, im_handle_param_t* param) -> uint32 handle
        lib.importbuffer_virtualaddr.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(im_handle_param_t)
        ]
        lib.importbuffer_virtualaddr.restype = ctypes.c_uint

        # releasebuffer_handle(uint32 handle) -> int
        lib.releasebuffer_handle.argtypes = [ctypes.c_uint]
        lib.releasebuffer_handle.restype = ctypes.c_int

        # wrapbuffer_handle_t(handle, w, h, wstride, hstride, format) -> rga_buffer_t
        lib.wrapbuffer_handle_t.argtypes = [
            ctypes.c_uint, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        lib.wrapbuffer_handle_t.restype = rga_buffer_t

        # wrapbuffer_virtualaddr_t(va, w, h, wstride, hstride, format) -> rga_buffer_t
        # 官方一步式 API（注意：wrapbuffer_virtualaddr 无 _t 后缀是 C 宏，ctypes 无法调用，
        # 必须直接调用 _t 版本的 6 参数函数）。内部完整填充 rga_buffer_t 全字段（含 vir_addr
        # 和 wstride/hstride），避免两步法 wrapbuffer_handle_t 不回填 vir_addr 的问题。
        # 关键：wstride/hstride 必须显式传入，否则默认为 0，RGA 按 0 步长读取 → 雪花。
        if hasattr(lib, 'wrapbuffer_virtualaddr_t'):
            lib.wrapbuffer_virtualaddr_t.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ]
            lib.wrapbuffer_virtualaddr_t.restype = rga_buffer_t
            self._has_wrapbuffer_va = True
        else:
            self._has_wrapbuffer_va = False

        # improcess(src, dst, pat, srect, drect, prect, usage) -> int  （按值传结构体）
        lib.improcess.argtypes = [
            rga_buffer_t, rga_buffer_t, rga_buffer_t,
            im_rect, im_rect, im_rect, ctypes.c_int,
        ]
        lib.improcess.restype = ctypes.c_int

        # imconfig(int name, int value) -> int  设置全局配置（如调度核心）
        # imconfig 可选符号：旧版 librga 可能未导出，缺失时忽略（退回驱动默认调度）
        if hasattr(lib, 'imconfig'):
            lib.imconfig.argtypes = [ctypes.c_int, ctypes.c_int]
            lib.imconfig.restype = ctypes.c_int
            self._has_imconfig = True
        else:
            self._has_imconfig = False

        self._available = True

    def is_available(self) -> bool:
        """RGA 是否可用（库已加载并绑定符号）"""
        return self._lib is not None and self._available

    def crop_resize_yuv420p_to_rgb(
        self, yuv_frame: np.ndarray, region, dst_w: int, dst_h: int
    ) -> np.ndarray:
        """
        从 I420 帧裁剪 region 并缩放为 (dst_h, dst_w, 3) RGB uint8

        单次 RGA 硬件调用完成：裁剪（srect）+ 缩放（src→dst 尺寸）+ 格式转换（I420→RGB）。
        缓冲区 handle 按尺寸池化复用，不再每帧 import/release，避免内核 dma-buf 资源耗尽。

        Args:
            yuv_frame: (H*3//2, W) 合并 I420 帧（merge_yuv_planes 输出，非标准布局）
            region: (x1, y1, x2, y2) 原图坐标，必须在帧范围内（越界由上层回退 cv2）
            dst_w: 目标宽度（模型输入宽）
            dst_h: 目标高度（模型输入高）

        Returns:
            (dst_h, dst_w, 3) uint8 RGB（独立副本，池化缓冲不会被后续调用覆盖）

        Raises:
            RuntimeError: RGA 调用失败（上层应捕获并回退 cv2）
        """
        if not self.is_available():
            raise RuntimeError("RGA 不可用")

        H = yuv_frame.shape[0] // 3 * 2
        W = yuv_frame.shape[1]

        # region 边界检查：必须在帧内（越界场景由 cv2 路径处理 padding）
        x1 = max(0, int(region[0]))
        y1 = max(0, int(region[1]))
        x2 = min(W, int(region[2]))
        y2 = min(H, int(region[3]))
        if x2 <= x1 or y2 <= y1:
            raise RuntimeError(f"region 越界或为空：{region}，帧尺寸({W}x{H})")

        # RGA 对 YUV420p 源格式的 srect 宽高要求为偶数（U/V 按 2x2 下采样）
        if (x2 - x1) % 2 != 0:
            x2 -= 1
        if (y2 - y1) % 2 != 0:
            y2 -= 1
        if x2 <= x1 or y2 <= y1:
            raise RuntimeError(f"region 对齐后为空：{region}，帧尺寸({W}x{H})")

        with self._lock:
            # ---- 获取/创建池化缓冲（handle 永久复用，不每帧 import）----
            src_key = (W, H)
            src_pooled = self._src_pool.get(src_key)
            if src_pooled is None:
                src_pooled = _PooledBuffer(self._lib, W, H, RK_FORMAT_YCbCr_420_P)
                self._src_pool[src_key] = src_pooled
                logger.info(f"[RGA] 源缓冲池化: {W}x{H} I420, handle={src_pooled.handle}")

            dst_key = (dst_w, dst_h)
            dst_pooled = self._dst_pool.get(dst_key)
            if dst_pooled is None:
                dst_pooled = _PooledBuffer(self._lib, dst_w, dst_h, RK_FORMAT_RGB_888)
                self._dst_pool[dst_key] = dst_pooled
                logger.info(f"[RGA] 目的缓冲池化: {dst_w}x{dst_h} RGB, handle={dst_pooled.handle}")

            # ---- 重建标准 I420 布局并拷入池化缓冲（VA 不变，handle 有效）----
            # merge_yuv_planes 把 U/V reshape 为 (H//4, W)，需还原为 (H//2, W//2)
            y_plane = yuv_frame[:H, :W]
            u_plane = yuv_frame[H:H + H // 4, :W].reshape(H // 2, W // 2)
            v_plane = yuv_frame[H + H // 4:H + H // 2, :W].reshape(H // 2, W // 2)

            y_size = H * W
            c_size = (H // 2) * (W // 2)
            # 直接写入池化一维数组，避免 np.concatenate 每帧分配新内存
            src_pooled.array[:y_size] = y_plane.ravel()
            src_pooled.array[y_size:y_size + c_size] = u_plane.ravel()
            src_pooled.array[y_size + c_size:y_size + 2 * c_size] = v_plane.ravel()

            # ---- 构造矩形 ----
            srect = im_rect(x1, y1, x2 - x1, y2 - y1)
            drect = im_rect(0, 0, dst_w, dst_h)
            prect = im_rect(0, 0, 0, 0)

            # ---- 单次硬件处理：裁剪 + 缩放 + YUV→RGB（复用 handle/rga_buf）----
            ret = self._lib.improcess(
                src_pooled.rga_buf, dst_pooled.rga_buf, rga_buffer_t(),
                srect, drect, prect, IM_SYNC,
            )
            if ret != IM_STATUS_SUCCESS and ret != IM_STATUS_NOERROR:
                raise RuntimeError(f"improcess 失败，错误码：{ret}")

            # 必须复制：池化目的缓冲会被下一帧覆盖，返回独立副本
            return dst_pooled.array[:dst_h * dst_w * 3].reshape(dst_h, dst_w, 3).copy()

    def release(self):
        """释放所有池化缓冲的 handle（处理器关闭时调用）"""
        for buf in self._src_pool.values():
            buf.release(self._lib)
        for buf in self._dst_pool.values():
            buf.release(self._lib)
        self._src_pool.clear()
        self._dst_pool.clear()


# ========================================
# 单例访问
# ========================================
_processor_instance: Optional[RGAProcessor] = None


def get_rga_processor() -> Optional[RGAProcessor]:
    """获取 RGAProcessor 单例（首次调用延迟初始化）"""
    global _processor_instance
    if _processor_instance is None:
        try:
            _processor_instance = RGAProcessor()
        except Exception as e:
            logger.warning(f"[RGAProcessor] 初始化失败：{e}")
            _processor_instance = None
    return _processor_instance


def release_rga_processor():
    """释放单例处理器及其池化缓冲 handle（进程退出时调用）"""
    global _processor_instance
    if _processor_instance is not None:
        _processor_instance.release()
        _processor_instance = None
