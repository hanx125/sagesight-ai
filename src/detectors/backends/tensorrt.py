"""
TensorRT 检测器后端

直接加载 TensorRT Engine 文件进行推理。
适用于：
- 已有预编译的 TensorRT Engine 文件（.engine/.plan）
- 需要极致推理性能的场景
- 生产环境部署

特性：
- 直接加载 TensorRT Engine（无需 ONNX 转换）
- 支持 FP16 精度
- 高性能推理（比 ONNX Runtime 更快）
- 需要手动管理 GPU 内存（需要 cuda-python 或 pycuda）

注意：
- 此后端需要预先生成 TensorRT Engine 文件
- 需要安装 tensorrt Python 包
- 需要安装 cuda-python 或 pycuda 进行 GPU 内存管理
"""

import os
import sys
import time
from typing import List, Dict, Optional, Tuple

import numpy as np

from src.detectors.base import BaseDetector, Detection, DetectorConfig

# TensorRT 支持
TRT_AVAILABLE = False
try:
    import tensorrt as trt
    TRT_AVAILABLE = True
    print(f"[TensorRTDetector] TensorRT 已加载")
except ImportError:
    print(f"[TensorRTDetector] TensorRT 未安装")


def _setup_tensorrt_lib_path(config_tensorrt_lib_path: str = "") -> str:
    """
    设置 TensorRT lib 路径
    
    Args:
        config_tensorrt_lib_path: 配置文件中的 TensorRT lib 路径
    
    Returns:
        TensorRT lib 路径
    """
    tensorrt_lib = ""
    
    # 1. 使用配置文件中的路径
    if config_tensorrt_lib_path and os.path.exists(config_tensorrt_lib_path):
        tensorrt_lib = config_tensorrt_lib_path
    
    # 2. 使用环境变量
    if not tensorrt_lib:
        tensorrt_lib = os.environ.get("TENSORRT_LIB_PATH", "")
    
    # 3. 自动检测常见路径（Windows）
    if not tensorrt_lib and os.name == 'nt':
        default_paths = [
            "D:\\TensorRT\\lib",
            "C:\\Program Files\\TensorRT\\lib",
        ]
        for path in default_paths:
            if os.path.exists(path):
                tensorrt_lib = path
                break
    
    # 添加到系统 PATH
    if tensorrt_lib and tensorrt_lib not in os.environ.get("PATH", ""):
        if os.name == 'nt':
            os.environ["PATH"] = tensorrt_lib + ";" + os.environ.get("PATH", "")
        else:
            os.environ["PATH"] = tensorrt_lib + ":" + os.environ.get("PATH", "")
    
    return tensorrt_lib


class TensorRTDetector(BaseDetector):
    """
    TensorRT 检测器
    
    直接加载 TensorRT Engine 文件进行推理。
    
    与 ONNXTensorRTDetector 的区别：
    - 后者：加载 ONNX 模型，自动转换为 TensorRT Engine
    - 此类：直接加载预编译的 TensorRT Engine 文件
    
    优势：
    - 无需 ONNX 转换时间
    - 推理性能略优于 ONNX Runtime
    - 可以优化 Engine 参数（最大批次大小等）
    
    注意：
    - 需要使用 trtexec 或 tensorrt Python API 预先生成 Engine 文件
    - 需要安装 cuda-python 或 pycuda 进行 GPU 内存管理
    """
    
    def __init__(self, config: DetectorConfig):
        """
        初始化 TensorRT 检测器
        
        Args:
            config: 检测器配置对象
        """
        super().__init__(config)
        
        # TensorRT 组件
        self._logger = None
        self._runtime = None
        self._engine = None
        self._context = None
        
        # TensorRT 输入输出信息
        self._input_shape_trt = None
        self._output_shape_trt = None
        self._output_name = None
        
        # GPU 内存（需要 cuda-python）
        # 由于直接 TensorRT 推理需要 GPU 内存管理，这里简化处理
        # 实际生产环境建议使用 ONNX Runtime TensorRT EP
        
        print(f"[TensorRTDetector] 初始化完成")
    
    def initialize(self):
        """
        初始化检测器（加载模型）
        """
        try:
            print(f"[TensorRTDetector] 正在初始化检测器...")
            print(f"[TensorRTDetector]   Engine路径: {self.config.tensorrt_engine_path}")
            sys.stdout.flush()
            
            self.load_model()
            
            print(f"[TensorRTDetector] 检测器初始化完成")
            sys.stdout.flush()
        except Exception as e:
            print(f"[TensorRTDetector] 检测器初始化失败: {e}")
            sys.stdout.flush()
            raise
    
    def load_model(self, model_path: str = None):
        """
        加载 TensorRT Engine
        
        Args:
            model_path: Engine 文件路径（可选，通常使用 config.tensorrt_engine_path）
        
        Raises:
            ImportError: TensorRT 未安装
            FileNotFoundError: Engine 文件不存在
            RuntimeError: Engine 加载失败
        """
        # 检查 TensorRT 是否可用
        if not TRT_AVAILABLE:
            raise ImportError(
                "TensorRT 未安装，无法使用 TensorRT 后端。\n"
                "请安装 TensorRT Python 包:\n"
                "pip install tensorrt"
            )
        
        # 设置 TensorRT lib 路径
        _setup_tensorrt_lib_path(self.config.tensorrt_lib_path)
        
        # 使用配置中的 Engine 路径
        engine_path = self.config.tensorrt_engine_path
        
        if not engine_path:
            raise FileNotFoundError("TensorRT Engine 路径未指定（请在 config.tensorrt_engine_path 中配置）")
        
        if not os.path.exists(engine_path):
            raise FileNotFoundError(f"TensorRT Engine 文件不存在：{engine_path}")
        
        try:
            # ========================================
            # 创建 TensorRT Logger
            # ========================================
            self._logger = trt.Logger(trt.Logger.INFO)
            
            # ========================================
            # 创建 TensorRT Runtime
            # ========================================
            self._runtime = trt.Runtime(self._logger)
            
            # ========================================
            # 加载 Engine
            # ========================================
            with open(engine_path, 'rb') as f:
                engine_data = f.read()
            
            self._engine = self._runtime.deserialize_cuda_engine(engine_data)
            
            # ========================================
            # 创建 Execution Context
            # ========================================
            self._context = self._engine.create_execution_context()
            
            # ========================================
            # 获取输入输出信息
            # ========================================
            for i in range(self._engine.num_io_tensors):
                name = self._engine.get_tensor_name(i)
                mode = self._engine.get_tensor_mode(name)
                shape = self._engine.get_tensor_shape(name)
                dtype = self._engine.get_tensor_dtype(name)
                
                if mode == trt.TensorIOMode.INPUT:
                    self.input_name = name
                    self._input_shape_trt = shape
                    self.input_shape = tuple(shape)
                    print(f"  [INPUT] {name}: 形状={shape}, 类型={dtype}")
                else:
                    self._output_name = name
                    self._output_shape_trt = shape
                    print(f"  [OUTPUT] {name}: 形状={shape}, 类型={dtype}")
            
            # 打印加载信息
            engine_size_mb = os.path.getsize(engine_path) / (1024 * 1024)
            print(f"[TensorRTDetector] TensorRT Engine 加载成功：{engine_path}")
            print(f"  输入名称：{self.input_name}")
            print(f"  输入形状：{self.input_shape}")
            print(f"  Engine 大小：{engine_size_mb:.1f} MB")
            
        except Exception as e:
            raise RuntimeError(f"TensorRT Engine 加载失败：{e}")
    
    def infer(self, input_tensor: np.ndarray) -> List[Detection]:
        """
        执行推理（TensorRT）
        
        注意：直接 TensorRT 推理需要 GPU 内存管理。
        此实现使用简化的 CPU 回退方式。
        实际生产环境建议：
        - 使用 ONNX Runtime TensorRT EP
        - 或安装 cuda-python 进行 GPU 内存管理
        
        Args:
            input_tensor: 预处理后的张量 (1, 3, H, W) float32，范围 [0, 1]
        
        Returns:
            检测结果列表
        
        Raises:
            RuntimeError: Engine 未加载或推理失败
        """
        if self._engine is None or self._context is None:
            raise RuntimeError("TensorRT Engine 未加载，请先调用 load_model()")
        
        # ========================================
        # 重要提示：GPU 内存管理
        # ========================================
        # TensorRT 10.x 的 execute_async_v3 需要手动管理 GPU 内存
        # 需要安装 cuda-python 或 pycuda
        # 
        # 简化实现：
        # 1. 建议用户使用 ONNX Runtime TensorRT EP（更简单）
        # 2. 或安装 cuda-python: pip install cuda-python
        
        try:
            # 尝试使用 cuda-python（如果可用）
            try:
                import cuda.cuda as cuda
                import cuda.cudart as cudart
                
                return self._infer_with_cuda_python(input_tensor)
                
            except ImportError:
                # cuda-python 不可用，提示用户
                raise RuntimeError(
                    "TensorRT 直接推理需要 GPU 内存管理。\n\n"
                    "请选择以下方案之一：\n\n"
                    "方案1（推荐）：使用 ONNX Runtime TensorRT EP\n"
                    "  backend='onnx_tensorrt'（自动管理 GPU 内存）\n\n"
                    "方案2：安装 cuda-python 进行 GPU 内存管理\n"
                    "  pip install cuda-python\n\n"
                    "方案3：安装 pycuda\n"
                    "  pip install pycuda"
                )
                
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"[TensorRTDetector] 推理失败：{e}")
            print(f"[TensorRTDetector] 错误详情:\n{error_details}")
            raise
    
    def _infer_with_cuda_python(self, input_tensor: np.ndarray) -> List[Detection]:
        """
        使用 cuda-python 执行推理（内部方法）
        
        Args:
            input_tensor: 输入张量
        
        Returns:
            检测结果列表
        """
        import cuda.cuda as cuda
        import cuda.cudart as cudart
        
        start_time = time.time()
        
        try:
            # ========================================
            # 分配 GPU 内存
            # ========================================
            input_size = input_tensor.nbytes
            output_size = int(np.prod(self._output_shape_trt) * 4)  # float32 = 4 bytes
            
            # 分配输入输出缓冲区
            d_input = cuda.Malloc(input_size)
            d_output = cuda.Malloc(output_size)
            
            # ========================================
            # 拷贝输入数据到 GPU
            # ========================================
            cuda.Memcpy(d_input, input_tensor.ctypes.data, input_size, cuda.MemcpyKind.cudaMemcpyHostToDevice)
            
            # ========================================
            # 设置输入输出绑定
            # ========================================
            self._context.set_tensor_address(self.input_name, int(d_input))
            self._context.set_tensor_address(self._output_name, int(d_output))
            
            # ========================================
            # 执行推理
            # ========================================
            self._context.execute_async_v3(stream=0)
            
            # ========================================
            # 拷贝输出数据到 CPU
            # ========================================
            output = np.empty(self._output_shape_trt, dtype=np.float32)
            cuda.Memcpy(output.ctypes.data, d_output, output_size, cuda.MemcpyKind.cudaMemcpyDeviceToHost)
            
            # ========================================
            # 释放 GPU 内存
            # ========================================
            cuda.Free(d_input)
            cuda.Free(d_output)
            
            # ========================================
            # 后处理
            # ========================================
            img_height = input_tensor.shape[2]
            img_width = input_tensor.shape[3]
            
            detections = self._postprocess_yolo(
                output=output,
                img_height=img_height,
                img_width=img_width,
                conf_threshold=self.config.conf_threshold,
                iou_threshold=self.config.iou_threshold,
                class_names=self.config.class_names,
                min_box_area=self.config.min_box_area
            )
            
            # 更新统计信息
            inference_time = time.time() - start_time
            self._update_stats(inference_time)
            
            return detections
            
        except Exception as e:
            raise RuntimeError(f"TensorRT 推理失败：{e}")
    
    def unload_model(self):
        """
        卸载模型
        
        释放 TensorRT Engine 和相关资源。
        """
        if self._context is not None:
            del self._context
            self._context = None
        
        if self._engine is not None:
            del self._engine
            self._engine = None
        
        if self._runtime is not None:
            del self._runtime
            self._runtime = None
        
        print("[TensorRTDetector] 模型已卸载")
    
    def get_info(self) -> Dict:
        """
        获取检测器信息
        
        Returns:
            检测器信息字典
        """
        info = {
            'backend': 'tensorrt',
            'engine_path': self.config.tensorrt_engine_path,
            'input_shape': self.input_shape,
            'output_shape': self._output_shape_trt,
            'class_names': self.config.class_names,
            'conf_threshold': self.config.conf_threshold,
            'iou_threshold': self.config.iou_threshold
        }
        
        # 添加推理统计信息
        stats = self.get_inference_stats()
        info.update(stats)
        
        return info