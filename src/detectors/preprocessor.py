"""
预处理器 

架构流程：
    [共享内存 YUV帧]
        ↓ frame_manager.get()
    [帧数据]
        ↓ motion_detector.detect()
    [运动区域]
        ↓ 判断是否有运动
    ├─ 有运动：create_tensor_input() → [目标检测]
    └─ 无运动：跳过检测（优化性能）

核心功能：
    - 复用 src/utils/object.py 的 create_tensor_input
    - 复用 src/utils/image.py 的 YUV 处理工具
    - 支持 NV12 格式（新增）
    - 支持归一化（新增）
"""

import numpy as np
import cv2
import time
from typing import Tuple, Optional, Dict, Any

# 导入 YUV 处理工具
try:
    # 直接从 src.utils 导入工具函数
    import sys
    from pathlib import Path
    
    # 添加项目根目录到路径（如果还没有）
    project_root = Path(__file__).parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    # 现在可以导入了
    from src.utils.image import (
        yuv_region_2_rgb,
        yuv_region_2_bgr,
        yuv_region_2_yuv,
        yuv_crop_and_resize
    )
    YUV_TOOLS_AVAILABLE = True
    print("[OK] 成功导入 YUV 处理工具")
except ImportError as e:
    YUV_TOOLS_AVAILABLE = False
    print(f"[警告] 无法导入 YUV 处理工具，将使用备用实现: {e}")


class OptimizedPreprocessor:
    """优化版预处理器"""

    def __init__(
        self,
        width: int = 640,
        height: int = 640,
        model_input_size: int = 640,
        rgb_mean: tuple = (0.0, 0.0, 0.0),
        rgb_std: tuple = (1.0, 1.0, 1.0)
    ):
        """
        初始化预处理器

        Args:
            width: 输入图像宽度
            height: 输入图像高度
            model_input_size: YOLO 模型输入尺寸（默认 640）
            rgb_mean: RGB 均值（用于归一化）
            rgb_std: RGB 标准差（用于归一化）
        """
        self.width = width
        self.height = height
        self.model_input_size = model_input_size
        self.rgb_mean = np.array(rgb_mean, dtype=np.float32)
        self.rgb_std = np.array(rgb_std, dtype=np.float32)
        
        # 预分配缓冲区，避免每帧重新分配内存
        # RGB 图像缓冲区（BGR 格式，用于保存和显示）
        self._bgr_buffer = np.zeros((model_input_size, model_input_size, 3), dtype=np.uint8)
        # 归一化缓冲区（float32）
        self._norm_buffer = np.zeros((model_input_size, model_input_size, 3), dtype=np.float32)
        # 最终输入张量缓冲区
        self._input_tensor = np.zeros((1, 3, model_input_size, model_input_size), dtype=np.float32)
        
        # 检查是否为默认归一化参数（mean=0, std=1）
        self._is_default_norm = (self.rgb_mean.sum() == 0.0) and (self.rgb_std == 1.0).all()
    
    def preprocess(self, y: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        """
        预处理：YUV420p → RGB → 归一化 → CHW
        
        Args:
            y: Y 平面 (height, width)，亮度分量
            u: U 平面 (height//2, width//2)，色度分量
            v: V 平面 (height//2, width//2)，色度分量
        
        Returns:
            模型输入张量 (1, 3, height, width) float32
        
        Raises:
            ValueError: 输入形状不符合预期
        """
        h, w = self.height, self.width
        
        # 输入验证
        if y.shape != (h, w):
            raise ValueError(f"Y 平面形状错误：期望 ({h}, {w}), 得到 {y.shape}")
        if u.shape != (h // 2, w // 2):
            raise ValueError(f"U 平面形状错误：期望 ({h // 2}, {w // 2}), 得到 {u.shape}")
        if v.shape != (h // 2, w // 2):
            raise ValueError(f"V 平面形状错误：期望 ({h // 2}, {w // 2}), 得到 {v.shape}")
        
        # YUV420p → RGB 转换
        # 方法：先上采样 U/V 到完整分辨率，然后合并为 YUV 三通道图像
        # 使用 INTER_LINEAR 插值保持颜色平滑
        # 注意：U/V 平面原始尺寸是 (h//2, w//2)，上采样到 (h, w)
        u_resized = cv2.resize(u, (w, h), interpolation=cv2.INTER_LINEAR)
        v_resized = cv2.resize(v, (w, h), interpolation=cv2.INTER_LINEAR)

        # 合并为 YUV 三通道图像 (H, W, 3)
        # OpenCV 的 COLOR_YUV2RGB 期望 YUV 格式为 (Y, U, V) 三通道
        yuv_image = np.stack([y, u_resized, v_resized], axis=-1)

        # YUV → RGB
        rgb = cv2.cvtColor(yuv_image, cv2.COLOR_YUV2RGB)
        
        # === 修复：resize 到 YOLO 模型期望的尺寸 ===
        # 问题：配置文件中 height=639（满足 OpenCV YUV420p 要求），但 YOLO 模型期望 640x640
        # 解决方案：在 YUV 转 RGB 后，resize 到模型期望的尺寸
        # 原因：YOLO 模型输入尺寸是固定的（通常为 640x640），需要匹配
        if rgb.shape[0] != self.model_input_size or rgb.shape[1] != self.model_input_size:
            rgb = cv2.resize(rgb, (self.model_input_size, self.model_input_size), interpolation=cv2.INTER_LINEAR)
        
        # 归一化：(pixel / 255 - mean) / std
        # 如果 mean=0, std=1，则结果等同于 rgb / 255.0
        rgb_float = rgb.astype(np.float32) / 255.0
        rgb_norm = (rgb_float - self.rgb_mean) / self.rgb_std
        
        # HWC → CHW
        chw = np.transpose(rgb_norm, (2, 0, 1))
        # 添加 batch 维度
        input_tensor = np.expand_dims(chw, axis=0)

        return input_tensor

    def preprocess_nv12(self, y: np.ndarray, uv: np.ndarray) -> np.ndarray:
        """
        预处理：NV12 → RGB → 归一化 → CHW

        Args:
            y: Y 平面 (height, width)，亮度分量
            uv: UV 交错平面 (height//2, width)，色度分量交错存储

        Returns:
            模型输入张量 (1, 3, height, width) float32

        Raises:
            ValueError: 输入形状不符合预期
        """
        # 性能测试：开始
        perf_times = {}
        perf_start = time.time()
        
        h, w = self.height, self.width
        
        # 输入验证
        if y.shape != (h, w):
            raise ValueError(f"Y 平面形状错误：期望 ({h}, {w}), 得到 {y.shape}")
        if uv.shape != (h // 2, w):
            raise ValueError(f"UV 平面形状错误：期望 ({h // 2}, {w}), 得到 {uv.shape}")
        
        perf_times['validation'] = time.time() - perf_start
        
        # NV12 格式解析：UV 交错平面分离为 U 和 V
        # NV12 格式：Y 平面 (h, w) + UV 交错平面 (h//2, w)
        # UV 平面是 U 和 V 值在内存中交错存储，每两个像素共用一个 UV 值（水平下采样）
        # 正确理解：(h//2, w) 形状的 UV 平面，交替存储 U 和 V
        # 第0行: U0 V0 U1 V1 ... U(w-1) V(w-1)  (w=640, U 和 V 交替)
        # 提取 U 和 V 分量（交替列提取）
        # 注意：如果存在 stride 对齐，w 可能大于实际宽度，需要正确处理
        perf_times['before_uv_extract'] = time.time()

        # 优化：直接使用 OpenCV 的 NV12 → BGR 转换
        # NV12 格式：Y 平面 (h, w) + UV 交错平面 (h//2, w)
        # 拼接为连续的 NV12 缓冲区，一步完成颜色转换 + UV 上采样
        yuv_nv12 = np.ascontiguousarray(np.concatenate([y, uv], axis=0))
        bgr = cv2.cvtColor(yuv_nv12, cv2.COLOR_YUV2BGR_NV12)
        # 转为 RGB（模型期望 RGB 输入）
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        perf_times['after_uv_extract'] = time.time()
        perf_times['uv_extract'] = perf_times['after_uv_extract'] - perf_times['before_uv_extract']

        # 以下字段保留兼容性能日志（已合并到上面的转换中）
        perf_times['resize'] = 0
        perf_times['stack'] = 0
        perf_times['yuv2rgb'] = 0
        
        # resize 到 YOLO 模型期望的尺寸
        perf_times['before_model_resize'] = time.time()
        if rgb.shape[0] != self.model_input_size or rgb.shape[1] != self.model_input_size:
            rgb = cv2.resize(rgb, (self.model_input_size, self.model_input_size), interpolation=cv2.INTER_LINEAR)
        perf_times['after_model_resize'] = time.time()
        perf_times['model_resize'] = perf_times['after_model_resize'] - perf_times['before_model_resize']
        
        # 保存BGR图像用于运动检测和显示（使用归一化前的RGB，值范围0-255）
        self._last_rgb = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        
        # 归一化：(pixel / 255 - mean) / std
        # 使用预分配缓冲区，避免每帧重新分配内存
        perf_times['before_normalize'] = time.time()
        
        if self._is_default_norm:
            # 默认归一化：rgb / 255.0（一步完成，使用预分配缓冲区）
            np.divide(rgb.astype(np.float32), 255.0, out=self._norm_buffer)
            rgb = self._norm_buffer
        else:
            # 自定义归一化：使用预分配缓冲区
            np.divide(rgb.astype(np.float32), 255.0, out=self._norm_buffer)
            rgb = self._norm_buffer
            rgb -= self.rgb_mean
            rgb /= self.rgb_std
        
        perf_times['after_normalize'] = time.time()
        perf_times['normalize'] = perf_times['after_normalize'] - perf_times['before_normalize']
        
        # HWC → CHW（使用预分配的张量缓冲区）
        perf_times['before_transpose'] = time.time()
        # 使用切片赋值，避免额外内存分配
        self._input_tensor[0] = np.transpose(rgb, (2, 0, 1))
        perf_times['after_transpose'] = time.time()
        perf_times['transpose'] = perf_times['after_transpose'] - perf_times['before_transpose']
        
        perf_times['before_batch'] = time.time()
        input_tensor = self._input_tensor
        perf_times['after_batch'] = time.time()
        perf_times['batch'] = perf_times['after_batch'] - perf_times['before_batch']
        
        # 性能测试：结束
        perf_times['total'] = time.time() - perf_start
        
        # 打印性能日志（只在首次或每100次打印一次）
        if not hasattr(self, '_perf_counter'):
            self._perf_counter = 0
        self._perf_counter += 1
        if self._perf_counter % 100 == 1 or self._perf_counter == 1:
            print(f"[预处理器] 性能分析（第{self._perf_counter}帧）：")
            print(f"  验证: {perf_times.get('validation', 0)*1000:.2f}ms")
            print(f"  UV提取: {perf_times.get('uv_extract', 0)*1000:.2f}ms")
            print(f"  上采样: {perf_times.get('resize', 0)*1000:.2f}ms")
            print(f"  合并YUV: {perf_times.get('stack', 0)*1000:.2f}ms")
            print(f"  YUV2RGB: {perf_times.get('yuv2rgb', 0)*1000:.2f}ms")
            print(f"  模型Resize: {perf_times.get('model_resize', 0)*1000:.2f}ms")
            print(f"  归一化: {perf_times.get('normalize', 0)*1000:.2f}ms")
            print(f"  Transpose: {perf_times.get('transpose', 0)*1000:.2f}ms")
            print(f"  Batch: {perf_times.get('batch', 0)*1000:.2f}ms")
            print(f"  总耗时: {perf_times['total']*1000:.2f}ms")
            print(f"  FPS: {1000/(perf_times['total']*1000):.1f}")
        
        return input_tensor

    def preprocess_drm_prime(self, frame_data: dict) -> np.ndarray:
        """
        预处理：DRM Prime（RK3588 MPP硬件解码输出）→ RGB → 归一化 → CHW

        DRM Prime格式是RK3588 MPP硬件解码的输出格式，包含DMA缓冲区信息。
        实际像素数据通常是NV12格式，存储在DRM缓冲区中。

        Args:
            frame_data: 包含DRM Prime帧数据的字典，格式如下：
                {
                    'format': 'drm_prime',
                    'width': int,
                    'height': int,
                    'y_plane': np.ndarray,  # Y平面数据
                    'uv_plane': np.ndarray,  # UV交错平面数据（NV12格式）
                    'drm_fd': int,  # DRM文件描述符（可选）
                    'handles': list,  # DMA缓冲区句柄（可选）
                }

        Returns:
            模型输入张量 (1, 3, height, width) float32

        注意：
            - DRM Prime格式的实际像素布局与NV12相同
            - 此方法假设DMA缓冲区已映射为numpy数组
            - 如果FFmpeg已将drm_prime转换为NV12，可直接使用preprocess_nv12方法
        """
        # DRM Prime格式的像素数据实际上是NV12布局
        # 直接调用NV12预处理方法
        y = frame_data.get('y_plane')
        uv = frame_data.get('uv_plane')

        if y is None or uv is None:
            raise ValueError("DRM Prime帧数据缺少y_plane或uv_plane")

        # 调用NV12预处理（像素布局相同）
        return self.preprocess_nv12(y, uv)

    def create_tensor_input(
        self,
        frame_data: Dict[str, Any],
        region: Optional[Tuple[int, int, int, int]] = None,
        frame_shape: Optional[Tuple[int, int]] = None
    ) -> np.ndarray:
        """
        从帧数据创建张量输入

        这是核心的预处理方法，用于目标检测前的数据准备。

        架构流程：
            [共享内存 YUV帧]
                ↓ frame_manager.get()
            [帧数据]
                ↓ create_tensor_input()
            ├─ YUV420p：使用 YUV 处理工具
            ├─ NV12：新实现
            └─ DRM Prime：新实现
                ↓
            [目标检测]

        Args:
            frame_data: 帧数据字典，包含：
                - 'y': Y 平面 (height, width)
                - 'u': U 平面 (height//2, width//2) [YUV420p]
                - 'v': V 平面 (height//2, width//2) [YUV420p]
                - 'uv': UV 交错平面 (height//2, width) [NV12]
                - 'format': 帧格式 ('yuv420p' 或 'nv12')

            region: 检测区域 (x1, y1, x2, y2)，如果指定则裁剪该区域
            frame_shape: 完整帧的形状 (height, width)，用于区域裁剪

        Returns:
            模型输入张量 (1, 3, model_input_size, model_input_size) float32

        使用示例：
            # 方式1：处理完整帧
            tensor = preprocessor.create_tensor_input({
                'y': y_plane,
                'uv': uv_plane,
                'format': 'nv12'
            })

            # 方式2：处理运动区域（裁剪）
            tensor = preprocessor.create_tensor_input(
                frame_data={'y': y, 'uv': uv, 'format': 'nv12'},
                region=(100, 100, 500, 500),
                frame_shape=(640, 640)
            )
        """
        # 获取帧格式
        frame_format = frame_data.get('format', 'nv12')

        # ========== YUV420p 格式：使用 YUV 处理工具 ==========
        if frame_format == 'yuv420p' and YUV_TOOLS_AVAILABLE:
            # 合并 Y/U/V 平面为完整帧（YUV420p I420 格式）
            y = frame_data['y']
            u = frame_data['u']
            v = frame_data['v']

            height, width = y.shape

            # 创建完整的 YUV420p 帧（I420 格式：height*1.5 x width）
            # 内存布局：Y平面（height x width）+ U平面（height//2 x width//2）+ V平面（height//2 x width//2）
            yuv_frame = np.zeros((height + height // 2, width), dtype=np.uint8)
            
            # 填充 Y 平面
            yuv_frame[0:height, :] = y
            
            # 填充 U 平面（连续存储在 height 位置开始）
            u_height = height // 2
            u_width = width // 2
            yuv_frame[height:height + u_height, 0:u_width] = u
            
            # 填充 V 平面（连续存储在 height + height//4 位置开始）
            v_height = height // 2
            v_width = width // 2
            yuv_frame[height + u_height:height + height // 2, 0:v_width] = v

            # 使用 YUV 处理工具进行裁剪和转换
            if region:
                rgb_cropped = yuv_region_2_rgb(yuv_frame, region)
            else:
                # 完整帧转换（I420 → RGB）
                rgb_cropped = cv2.cvtColor(yuv_frame, cv2.COLOR_YUV2RGB_I420)

            # Resize 到模型输入尺寸
            if rgb_cropped.shape[0] != self.model_input_size or rgb_cropped.shape[1] != self.model_input_size:
                rgb_cropped = cv2.resize(rgb_cropped, (self.model_input_size, self.model_input_size))

            # 归一化
            rgb_normalized = rgb_cropped.astype(np.float32) / 255.0
            if not self._is_default_norm:
                rgb_normalized = (rgb_normalized - self.rgb_mean) / self.rgb_std

            # HWC → CHW
            chw = np.transpose(rgb_normalized, (2, 0, 1))
            input_tensor = np.expand_dims(chw, axis=0)

            return input_tensor

        # ========== NV12 和 DRM Prime 格式：使用新实现 ==========
        elif frame_format == 'nv12':
            y = frame_data['y']
            uv = frame_data['uv']

            # 如果指定了区域，先裁剪
            if region is not None and frame_shape is not None:
                return self._create_tensor_from_region_nv12(
                    frame_data, region, frame_shape
                )
            else:
                return self.preprocess_nv12(y, uv)

        elif frame_format == 'drm_prime':
            return self.preprocess_drm_prime(frame_data)

        else:
            raise ValueError(f"不支持的帧格式: {frame_format}")

    def _create_tensor_from_region_nv12(
        self,
        frame_data: Dict[str, Any],
        region: Tuple[int, int, int, int],
        frame_shape: Tuple[int, int]
    ) -> np.ndarray:
        """
        从 NV12 格式的指定区域创建张量输入（内部方法）

        Args:
            frame_data: 帧数据字典
            region: 检测区域 (x1, y1, x2, y2)
            frame_shape: 完整帧形状 (height, width)

        Returns:
            模型输入张量 (1, 3, model_input_size, model_input_size) float32
        """
        x1, y1, x2, y2 = region
        frame_height, frame_width = frame_shape

        # 确保区域在帧范围内
        x1 = max(0, min(x1, frame_width))
        y1 = max(0, min(y1, frame_height))
        x2 = max(0, min(x2, frame_width))
        y2 = max(0, min(y2, frame_height))

        # 检查区域有效性
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"无效的区域: {region}")

        # 裁剪区域尺寸
        region_height = y2 - y1
        region_width = x2 - x1

        y = frame_data['y']
        uv = frame_data['uv']

        # Y 平面裁剪
        y_cropped = y[y1:y2, x1:x2]

        # UV 平面裁剪（注意：UV 平面尺寸是 Y 平面的一半）
        uv_x1 = x1 // 2
        uv_x2 = x2 // 2
        uv_y1 = y1 // 2
        uv_y2 = y2 // 2
        uv_cropped = uv[uv_y1:uv_y2, uv_x1:uv_x2]

        # 转换为 RGB（临时使用完整尺寸的 preprocessor）
        temp_preprocessor = OptimizedPreprocessor(
            width=region_width,
            height=region_height,
            model_input_size=self.model_input_size,
            rgb_mean=self.rgb_mean,
            rgb_std=self.rgb_std
        )
        return temp_preprocessor.preprocess_nv12(y_cropped, uv_cropped)

    def get_last_bgr(self) -> Optional[np.ndarray]:
        """
        获取最后一次预处理的 BGR 图像（用于运动检测和显示）

        Returns:
            BGR 图像 (model_input_size, model_input_size, 3) 或 None
        """
        return getattr(self, '_last_rgb', None)


def benchmark():
    """性能测试"""
    import time
    
    print("\n" + "="*70)
    print("预处理器性能测试")
    print("="*70)
    
    y = np.random.randint(0, 255, (640, 640), dtype=np.uint8)
    u = np.random.randint(0, 255, (320, 320), dtype=np.uint8)
    v = np.random.randint(0, 255, (320, 320), dtype=np.uint8)
    
    preprocessor = OptimizedPreprocessor(640, 640)
    
    print("\n测试 100 次...")
    times = []
    for i in range(100):
        start = time.time()
        result = preprocessor.preprocess(y, u, v)
        times.append(time.time() - start)
    
    avg_ms = np.mean(times[10:]) * 1000
    print(f"  平均耗时：{avg_ms:.2f}ms")
    print(f"  理论 FPS: {1000/avg_ms:.1f}")
    print(f"  输出形状：{result.shape}")
    
    print("\n[OK] 测试完成")


if __name__ == "__main__":
    benchmark()