"""
YOLO 检测器模块

基于 ONNX Runtime-GPU / TensorRT 的 YOLOv8n/YOLO11n 推理引擎。

功能：
1. ONNX 模型加载（支持 CUDA / TensorRT Execution Provider）
2. TensorRT Engine 加载（支持 FP16 加速）
3. GPU 加速推理
4. 后处理（NMS、坐标反归一化）
5. 支持 YOLOv8n 和 YOLO11n 模型无缝切换

输入：
- 预处理张量：(1, 3, 640, 640) float32, 范围 [0, 1]

输出：
- 检测结果：List[Dict]，每个检测包含：
  - bbox: (x1, y1, x2, y2) 归一化坐标
  - confidence: 置信度 (0-1)
  - class_id: 类别 ID
  - class_name: 类别名称
"""

import os
import sys
import numpy as np
import cv2
import onnxruntime as ort
from typing import List, Dict, Optional, Tuple, Literal, Union
from dataclasses import dataclass, field
import time

# RKNN Executor 导入（仅RK3588等平台可用）
try:
    from src.utils.rknn_executor import RKNNExecutor, is_rknn_available
    RKNN_EXECUTOR_AVAILABLE = True
except ImportError:
    RKNN_EXECUTOR_AVAILABLE = False
    print("[YOLODetector] RKNN Executor 未安装（仅RK3588平台可用）")

# TensorRT 支持
# 必须在导入 tensorrt 之前设置 PATH
TRT_AVAILABLE = False
TRT_CONFIG = {}

def _load_hardware_config():
    """
    从system_config.yaml加载硬件配置
    
    Returns:
        Dict: 硬件配置字典
    """
    import yaml
    from pathlib import Path
    
    try:
        # 获取项目根目录
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / 'config' / 'system_config.yaml'
        
        if not config_path.exists():
            print("[YOLODetector] system_config.yaml 不存在，使用默认配置")
            return {}
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        return config.get('hardware', {})
    except Exception as e:
        print(f"[YOLODetector] 加载硬件配置失败: {e}")
        return {}

def _setup_tensorrt_lib_path():
    """
    设置TensorRT lib路径（模块初始化时）
    
    优先级：
    1. 环境变量 TENSORRT_LIB_PATH
    2. system_config.yaml中的hardware.tensorrt.lib_path
    3. 自动检测常见路径
    
    注意：DetectorConfig中的tensorrt_lib_path会在YOLODetector初始化时覆盖
    
    Returns:
        str: TensorRT lib路径，失败返回空字符串
    """
    tensorrt_lib = ""
    
    # 1. 使用环境变量（最高优先级，便于临时测试）
    tensorrt_lib = os.environ.get("TENSORRT_LIB_PATH", "")
    
    # 2. 从配置文件读取
    if not tensorrt_lib:
        hw_config = _load_hardware_config()
        trt_config = hw_config.get('tensorrt', {})
        tensorrt_lib = trt_config.get('lib_path', '')
    
    # 3. 自动检测常见路径（Windows）
    if not tensorrt_lib:
        hw_config = _load_hardware_config()
        trt_config = hw_config.get('tensorrt', {})
        
        # 根据配置文件中的版本号构建路径
        trt_version = trt_config.get('version', '10.5.0.18')
        cuda_version = hw_config.get('cuda', {}).get('version', '12.6')
        
        default_paths = [
            f"D:\\TensorRT-{trt_version}.Windows.win10.cuda-{cuda_version}\\TensorRT-{trt_version}\\lib",
            f"D:\\TensorRT-{trt_version}.Windows10.x86_64.cuda-{cuda_version}\\lib",
            "C:\\Program Files\\TensorRT\\lib",
            "/usr/local/TensorRT/lib",  # Linux
        ]
        
        for path in default_paths:
            if os.path.exists(path):
                tensorrt_lib = path
                print(f"[YOLODetector] 自动检测到TensorRT路径: {path}")
                break
    
    # 添加到系统PATH
    if tensorrt_lib and tensorrt_lib not in os.environ.get("PATH", ""):
        os.environ["PATH"] = tensorrt_lib + ";" + os.environ.get("PATH", "")
        print(f"[YOLODetector] TensorRT lib路径已添加到PATH: {tensorrt_lib}")
    
    return tensorrt_lib

# 初始化TensorRT
try:
    tensorrt_lib = _setup_tensorrt_lib_path()
    
    import tensorrt as trt
    TRT_AVAILABLE = True
    
    # 保存配置供后续使用
    TRT_CONFIG = _load_hardware_config().get('tensorrt', {})
    print(f"[YOLODetector] TensorRT 已加载")
    
except ImportError as e:
    print(f"[YOLODetector] TensorRT 未安装或加载失败: {e}")
    print("[YOLODetector] 将使用 ONNX Runtime + CUDA 后端")
    pass


@dataclass
class DetectorConfig:
    """检测器配置"""
    # 模型配置
    model_path: str = ""  # ONNX 模型路径
    model_type: str = "yolov8n"  # 模型类型：yolov8n 或 yolo11n

    # 推理后端配置
    backend: Literal["onnx_cpu", "onnx_cuda", "onnx_tensorrt", "tensorrt", "rknn"] = "onnx_cuda"
    # onnx_cpu: ONNX Runtime CPU（纯CPU推理，无需GPU）
    # onnx_cuda: ONNX Runtime + CUDA Execution Provider
    # onnx_tensorrt: ONNX Runtime + TensorRT Execution Provider（自动转换）
    # tensorrt: 直接加载 TensorRT Engine（需要 .engine 文件）
    # rknn: RKNN模型（RK3588/RK3566等瑞芯微平台，需要.rknn文件）

    # TensorRT 配置（扁平结构，与配置文件一致）
    tensorrt_engine_path: str = ""  # TensorRT Engine 路径（backend="tensorrt" 时使用）
    tensorrt_engine_cache_path: str = "./trt_cache"  # Engine 缓存路径
    tensorrt_lib_path: str = ""  # TensorRT lib 目录路径（Windows 需要配置）
    use_fp16: bool = True  # 使用 FP16 精度（仅 TensorRT）
    
    # RKNN 配置（RK3588等瑞芯微平台）
    rknn_target: str = "rk3588"  # 目标平台：rk3588 | rk3566 | rk3568 | rv1103 | rv1106
    rknn_device_id: str = ""  # RKNN设备ID（多设备时使用）
    rknn_core_mask: int = 0  # RKNN核心掩码（0=自动，1=NPU0，2=NPU1，3=双核）

    # 推理配置
    conf_threshold: float = 0.25  # 置信度阈值
    iou_threshold: float = 0.45   # NMS IoU 阈值
    min_box_area: float = 10.0    # 最小检测框面积（像素）

    # GPU 配置（针对 RTX 2070 优化）
    device_id: int = 0
    gpu_mem_limit: int = 6 * 1024 * 1024 * 1024  # 6GB 显存限制

    # 类别配置
    class_names: Tuple[str, ...] = (
        "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
        "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
        "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
        "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
        "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
        "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake",
        "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
        "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
        "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
    )  # COCO 80 类

    # 输入配置
    input_width: int = 640
    input_height: int = 640


@dataclass
class Detection:
    """单个检测结果"""
    bbox: Tuple[float, float, float, float]  # (x1, y1, x2, y2)
    confidence: float
    class_id: int
    class_name: str


class YOLODetector:
    """
    YOLO 检测器（支持 YOLOv8n 和 YOLO11n）
    
    特性：
    - ONNX Runtime-GPU 加速推理（CUDA / TensorRT Execution Provider）
    - TensorRT Engine 直接加载（FP16 加速）
    - CUDA Execution Provider 优化配置
    - 后处理：NMS、坐标反归一化
    """
    
    def __init__(self, config: Union[DetectorConfig, Dict] = None):
        """
        初始化 YOLO 检测器

        Args:
            config: 检测器配置（支持 DetectorConfig 对象或字典）
        """
        # ========================================
        # 配置对象转换逻辑
        # ========================================
        # 支持两种输入方式：
        # 1. DetectorConfig 对象（dataclass）
        # 2. 普通字典（自动转换为 DetectorConfig）
        
        if isinstance(config, dict):
            # 字典转 DetectorConfig：提取关键字段
            
            # ========================================
            # 类别配置类型转换
            # ========================================
            # DetectorConfig.class_names 是 Tuple[str, ...]
            # 但输入可能是 List[str]，需要转换为 Tuple
            
            target_classes = config.get('target_classes', DetectorConfig.class_names)
            if isinstance(target_classes, list):
                target_classes = tuple(target_classes)
            
            # 从标签文件读取类别列表
            labels_file = config.get('labels_file', '')
            if labels_file and os.path.exists(labels_file):
                # 标签文件存在，读取类别列表
                try:
                    with open(labels_file, 'r', encoding='utf-8') as f:
                        class_names = tuple(line.strip() for line in f if line.strip())
                    print(f"[YOLODetector] 从标签文件读取类别: {labels_file}")
                except Exception as e:
                    print(f"[YOLODetector] [警告] 无法读取标签文件: {e}，使用COCO默认类别")
                    class_names = DetectorConfig.class_names
            else:
                # 标签文件不存在或未配置，使用COCO默认类别
                class_names = DetectorConfig.class_names
                if labels_file:
                    print(f"[YOLODetector] [警告] 标签文件不存在: {labels_file}，使用COCO默认类别")
            
            # 字典转 DetectorConfig：提取关键字段
            # 重要：class_names 必须使用完整的类别列表（保持类别ID映射正确）
            # target_classes 只用于过滤检测结果，不改变 class_names 的索引
            # YOLO 模型的 class_id 是基于类别列表的固定索引（如 class_id=2 代表第3个类别）
            self.config = DetectorConfig(
                # 模型配置
                model_path=config.get('model_path', 'models/yolov11n.onnx'),

                # 推理后端配置
                backend=config.get('backend', 'onnx_cuda'),

                # TensorRT 配置
                tensorrt_engine_cache_path=config.get('engine_cache_path', './trt_cache'),
                tensorrt_lib_path=config.get('tensorrt_lib_path', ''),
                tensorrt_engine_path=config.get('tensorrt_engine_path', ''),
                use_fp16=config.get('use_fp16', True),

                # RKNN 配置
                rknn_target=config.get('rknn_target', 'rk3588'),
                rknn_device_id=config.get('rknn_device_id', ''),
                rknn_core_mask=config.get('rknn_core_mask', 0),

                # 推理配置
                conf_threshold=config.get('conf_threshold', 0.25),
                iou_threshold=config.get('iou_threshold', 0.45),
                min_box_area=config.get('min_box_area', 10.0),

                # GPU 配置
                device_id=config.get('device_id', 0),
                gpu_mem_limit=config.get('gpu_mem_limit', 6 * 1024 * 1024 * 1024),

                # 类别配置：从标签文件读取或使用COCO默认类别
                class_names=class_names
            )
            # 保存用户配置的过滤列表（在 _postprocess 中用于过滤检测结果）
            self.config.target_classes = target_classes
            print(f"[YOLODetector] 字典配置已转换为 DetectorConfig:")
            print(f"[YOLODetector]   backend={self.config.backend}")
            print(f"[YOLODetector]   model_path={self.config.model_path}")
            print(f"[YOLODetector]   class_names={len(self.config.class_names)}类（从标签文件）")
            print(f"[YOLODetector]   target_classes={len(target_classes)}类: {target_classes}")
        else:
            # 直接使用 DetectorConfig 对象
            self.config = config or DetectorConfig()
            print(f"[YOLODetector] 使用 DetectorConfig 对象: backend={self.config.backend}")

        # TensorRT lib 路径配置
        # DetectorConfig中的tensorrt_lib_path优先级最高（来自detector_config.yaml）
        if self.config.backend in ["onnx_tensorrt", "tensorrt"]:
            # 如果DetectorConfig指定了路径，使用它覆盖
            if self.config.tensorrt_lib_path:
                tensorrt_lib = self.config.tensorrt_lib_path
                if tensorrt_lib not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = tensorrt_lib + ";" + os.environ.get("PATH", "")
                    print(f"[YOLODetector] 使用DetectorConfig中的TensorRT路径: {tensorrt_lib}")
            
            if TRT_AVAILABLE:
                print(f"[YOLODetector] TensorRT 已加载")
            else:
                print(f"[YOLODetector] [警告] TensorRT 未加载，将回退到 CUDA 后端")
        
        # ONNX Runtime 会话
        self.session: Optional[ort.InferenceSession] = None
        self.input_name: Optional[str] = None
        self.input_shape: Optional[Tuple[int, int, int, int]] = None

        # TensorRT Engine
        self.trt_engine = None
        self.trt_context = None
        self.trt_stream = None
        self.trt_bindings = {}
        self.trt_input_shape = None
        self.trt_output_shape = None

        # 性能统计
        self._inference_count = 0
        self._total_inference_time = 0.0
    
    def initialize(self):
        """
        初始化检测器（加载模型）
        
        此方法在 __init__ 之后调用，用于延迟加载模型，
        避免在创建对象时立即加载模型导致启动缓慢。
        """
        try:
            print(f"[YOLODetector] 正在初始化检测器...")
            print(f"[YOLODetector]   模型路径: {self.config.model_path}")
            print(f"[YOLODetector]   推理后端: {self.config.backend}")
            print(f"[YOLODetector]   FP16模式: {self.config.use_fp16}")
            
            # 强制刷新输出缓冲区，确保日志立即显示
            import sys
            sys.stdout.flush()
            
            self.load_model()
            
            print(f"[YOLODetector] 检测器初始化完成")
            sys.stdout.flush()
        except Exception as e:
            print(f"[YOLODetector] 检测器初始化失败: {e}")
            import sys
            sys.stdout.flush()
            raise
    
    def load_model(self, model_path: str = None):
        """
        加载模型（支持 ONNX 和 TensorRT Engine）
        
        Args:
            model_path: 模型路径（可选，覆盖配置中的路径）
        
        Raises:
            FileNotFoundError: 模型文件不存在
            RuntimeError: 模型加载失败
        """
        model_path = model_path or self.config.model_path
        
        if not model_path:
            raise FileNotFoundError("模型路径未指定")
        
        # 根据后端类型加载模型
        if self.config.backend == "tensorrt":
            self._load_tensorrt_engine()
        elif self.config.backend == "onnx_tensorrt":
            self._load_onnx_with_tensorrt(model_path)
        elif self.config.backend == "rknn":
            self._load_rknn(model_path)
        elif self.config.backend == "onnx_cpu":
            self._load_onnx_with_cpu(model_path)
        else:  # onnx_cuda
            self._load_onnx_with_cuda(model_path)
    
    def _load_onnx_with_cuda(self, model_path: str):
        """使用 ONNX Runtime + CUDA 加载模型"""
        if not model_path.endswith('.onnx'):
            raise ValueError(f"模型文件必须是 ONNX 格式：{model_path}")
        
        try:
            # 创建会话选项（针对 RTX 2070 优化）
            session_options = ort.SessionOptions()
            
            # CUDA Graph 加速（需要 ONNX Runtime 1.17+）
            try:
                session_options.enable_cuda_graph = True
            except AttributeError:
                # 旧版本不支持 CUDA Graph，跳过
                print("[YOLODetector] 当前版本不支持 CUDA Graph，使用默认优化")
            
            session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            
            # CUDA Execution Provider 配置
            providers = [
                ('CUDAExecutionProvider', {
                    'device_id': self.config.device_id,
                    'cudnn_conv_algo_search': 'HEURISTIC',
                    'arena_extend_strategy': 'kSameAsRequested',
                    'gpu_mem_limit': self.config.gpu_mem_limit,
                    'cudnn_conv1d_pad_to_nc1d': True,
                }),
                'CPUExecutionProvider'  # 回退到 CPU
            ]
            
            # 创建推理会话
            self.session = ort.InferenceSession(
                model_path,
                sess_options=session_options,
                providers=providers
            )
            
            # 获取输入信息
            inputs = self.session.get_inputs()
            self.input_name = inputs[0].name
            self.input_shape = inputs[0].shape
            
            print(f"[YOLODetector] ONNX 模型加载成功（CUDA 后端）：{model_path}")
            print(f"  输入名称：{self.input_name}")
            print(f"  输入形状：{self.input_shape}")
            print(f"  可用 Provider: {self.session.get_providers()}")
            
        except Exception as e:
            raise RuntimeError(f"模型加载失败：{e}")
    
    def _load_onnx_with_tensorrt(self, model_path: str):
        """使用 ONNX Runtime + TensorRT Execution Provider 加载模型"""
        if not model_path.endswith('.onnx'):
            raise ValueError(f"模型文件必须是 ONNX 格式：{model_path}")
        
        try:
            # 创建会话选项
            session_options = ort.SessionOptions()
            session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            
            # 【关键】启用 TensorRT 详细日志，查看编译进度
            import os
            os.environ['ORT_TENSORRT_LOGGER_LEVEL'] = '1'  # 0=OFF, 1=INFO, 2=VERBOSE
            
            # TensorRT Execution Provider 配置
            trt_ep_options = {
                'device_id': self.config.device_id,
                'trt_fp16_enable': self.config.use_fp16,  # 启用 FP16
                'trt_engine_cache_enable': True,  # 启用 Engine 缓存
                'trt_engine_cache_path': self.config.tensorrt_engine_cache_path,  # 缓存路径
            }
            
            providers = [
                ('TensorrtExecutionProvider', trt_ep_options),
                ('CUDAExecutionProvider', {
                    'device_id': self.config.device_id,
                }),
                'CPUExecutionProvider'
            ]
            
            # 创建推理会话（TensorRT编译Engine会在此步骤执行）
            print(f"[YOLODetector] 正在加载模型并创建推理会话...")
            print(f"[YOLODetector]   检查Engine缓存: {self.config.tensorrt_engine_cache_path}")
            print(f"[YOLODetector]   如果缓存不存在，TensorRT将编译新Engine（可能耗时2-5分钟）")
            print(f"[YOLODetector]   请观察控制台是否有TensorRT编译日志输出...")
            sys.stdout.flush()
            
            self.session = ort.InferenceSession(
                model_path,
                sess_options=session_options,
                providers=providers
            )
            
            print(f"[YOLODetector] 推理会话创建完成！")
            sys.stdout.flush()
            
            # 获取输入信息
            inputs = self.session.get_inputs()
            self.input_name = inputs[0].name
            self.input_shape = inputs[0].shape
            
            # 获取实际使用的 Provider（验证 TensorRT 是否真正生效）
            active_providers = self.session.get_providers()
            print(f"[YOLODetector] ONNX 模型加载成功（TensorRT 后端）：{model_path}")
            print(f"  输入名称：{self.input_name}")
            print(f"  输入形状：{self.input_shape}")
            print(f"  可用 Provider: {active_providers}")
            print(f"  FP16 加速: {self.config.use_fp16}")
            print(f"  Engine 缓存: {self.config.tensorrt_engine_cache_path}")
            sys.stdout.flush()
            
            # 关键验证：确保 TensorRT 实际在运行，而非回退到 CUDA
            if 'TensorrtExecutionProvider' not in active_providers:
                print(f"[YOLODetector] [警告] TensorRT EP 未激活！实际使用：{active_providers}")
                print(f"[YOLODetector] 可能原因：")
                print(f"    1. 首次运行正在编译 Engine（可能需要 2-5 分钟）")
                print(f"    2. TensorRT 版本与 CUDA 版本不匹配")
                print(f"    3. ONNX 模型包含 TensorRT 不支持的算子")
                print(f"    4. 缺少 TensorRT 动态库（libnvinfer.so/dll）")
            else:
                print(f"[YOLODetector] [OK] TensorRT EP 已确认激活")
            
        except Exception as e:
            print(f"[YOLODetector] TensorRT 加载失败，回退到 CUDA：{e}")
            self._load_onnx_with_cuda(model_path)
    
    def _load_tensorrt_engine(self):
        """直接加载 TensorRT Engine"""
        if not TRT_AVAILABLE:
            raise RuntimeError("TensorRT 未安装，无法使用 TensorRT 后端")
        
        engine_path = self.config.tensorrt_engine_path
        if not engine_path:
            raise FileNotFoundError("TensorRT Engine 路径未指定")
        
        if not os.path.exists(engine_path):
            raise FileNotFoundError(f"TensorRT Engine 文件不存在：{engine_path}")
        
        try:
            # 创建 Logger
            logger = trt.Logger(trt.Logger.INFO)
            
            # 创建 Runtime
            runtime = trt.Runtime(logger)
            
            # 加载 Engine
            with open(engine_path, 'rb') as f:
                self.trt_engine = runtime.deserialize_cuda_engine(f.read())
            
            # 创建 Execution Context
            self.trt_context = self.trt_engine.create_execution_context()
            
            # 获取输入输出信息
            for i in range(self.trt_engine.num_io_tensors):
                name = self.trt_engine.get_tensor_name(i)
                mode = self.trt_engine.get_tensor_mode(name)
                shape = self.trt_engine.get_tensor_shape(name)
                dtype = self.trt_engine.get_tensor_dtype(name)
                
                if mode == trt.TensorIOMode.INPUT:
                    self.input_name = name
                    self.trt_input_shape = shape
                    print(f"  [INPUT] {name}: 形状={shape}, 类型={dtype}")
                else:
                    self.trt_output_shape = shape
                    print(f"  [OUTPUT] {name}: 形状={shape}, 类型={dtype}")
            
            self.input_shape = tuple(self.trt_input_shape)
            
            print(f"[YOLODetector] TensorRT Engine 加载成功：{engine_path}")
            print(f"  输入名称：{self.input_name}")
            print(f"  输入形状：{self.input_shape}")
            print(f"  Engine 大小：{os.path.getsize(engine_path) / (1 << 20):.1f} MB")
            
        except Exception as e:
            raise RuntimeError(f"TensorRT Engine 加载失败：{e}")
    
    def _load_onnx_with_cpu(self, model_path: str):
        """使用 ONNX Runtime CPU 加载模型（纯CPU推理，无需GPU）"""
        if not model_path.endswith('.onnx'):
            raise ValueError(f"模型文件必须是 ONNX 格式：{model_path}")
        
        try:
            # 创建会话选项（CPU优化）
            session_options = ort.SessionOptions()
            session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            
            # CPU线程数优化（使用所有可用核心）
            import multiprocessing
            num_cores = multiprocessing.cpu_count()
            session_options.intra_op_num_threads = num_cores
            session_options.inter_op_num_threads = num_cores
            
            # CPU Execution Provider
            providers = ['CPUExecutionProvider']
            
            # 创建推理会话
            self.session = ort.InferenceSession(
                model_path,
                sess_options=session_options,
                providers=providers
            )
            
            # 获取输入信息
            inputs = self.session.get_inputs()
            self.input_name = inputs[0].name
            self.input_shape = inputs[0].shape
            
            print(f"[YOLODetector] ONNX 模型加载成功（CPU 后端）：{model_path}")
            print(f"  输入名称：{self.input_name}")
            print(f"  输入形状：{self.input_shape}")
            print(f"  CPU 核心数：{num_cores}")
            print(f"  提示：CPU推理速度较慢，建议使用GPU后端（onnx_cuda/onnx_tensorrt）")
            
        except Exception as e:
            raise RuntimeError(f"模型加载失败：{e}")
    
    def _load_rknn(self, model_path: str):
        """加载 RKNN 模型（RK3588/RK3566等瑞芯微平台NPU推理）"""
        if not RKNN_EXECUTOR_AVAILABLE:
            raise ImportError(
                "RKNN Executor 未安装，无法使用 RKNN 后端。\n"
                "请在RK3588平台上安装 rknn-toolkit2:\n"
                "pip install rknn-toolkit2"
            )
        
        if not model_path.endswith('.rknn'):
            raise ValueError(f"模型文件必须是 RKNN 格式：{model_path}")
        
        try:
            # 创建 RKNN Executor
            self.rknn_executor = RKNNExecutor(
                model_path=model_path,
                target=self.config.rknn_target,
                device_id=self.config.rknn_device_id
            )
            
            # RKNN模型输入通常为NHWC格式（与ONNX的NCHW不同）
            # 输入形状需要根据实际模型调整，这里假设为(1, 640, 640, 3)
            self.input_shape = (1, 640, 640, 3)  # NHWC格式
            
            print(f"[YOLODetector] RKNN 模型加载成功：{model_path}")
            print(f"  目标平台：{self.config.rknn_target}")
            print(f"  输入形状：{self.input_shape} (NHWC格式)")
            print(f"  提示：RKNN推理使用NPU加速，性能优于CPU")
            
        except Exception as e:
            raise RuntimeError(f"RKNN 模型加载失败：{e}")
    
    def infer(self, input_tensor: np.ndarray) -> List[Detection]:
        """
        执行推理
        
        Args:
            input_tensor: 预处理后的张量 (1, 3, 640, 640) float32
        
        Returns:
            检测结果列表
        
        Raises:
            RuntimeError: 模型未加载或推理失败
        """
        # 验证输入形状
        expected_shape = (1, 3, self.config.input_height, self.config.input_width)
        if input_tensor.shape != expected_shape:
            raise ValueError(f"输入形状错误：期望 {expected_shape}, 得到 {input_tensor.shape}")
        
        try:
            start_time = time.time()
            
            # 根据后端执行推理
            infer_start = time.time()
            if self.config.backend == "tensorrt" and self.trt_context is not None:
                output = self._infer_tensorrt(input_tensor)
            elif self.config.backend == "rknn" and hasattr(self, 'rknn_executor'):
                output = self._infer_rknn(input_tensor)
            else:
                output = self._infer_onnx(input_tensor)
            infer_time = time.time() - infer_start
            
            # 详细日志（只在首次推理时打印）
            if not hasattr(self, '_inference_first_printed'):
                print(f"[YOLODetector] 推理完成，输出类型: {type(output)}")
                print(f"[YOLODetector] 推理完成，输出形状: {output.shape if hasattr(output, 'shape') else 'N/A'}")
                self._inference_first_printed = True
            
            # 后处理
            postprocess_start = time.time()
            detections = self._postprocess(output, input_tensor.shape[2], input_tensor.shape[3])
            postprocess_time = time.time() - postprocess_start
            
            inference_time = time.time() - start_time
            
            # 更新统计
            self._inference_count += 1
            self._total_inference_time += inference_time
            
            # 性能日志（每100帧打印一次）
            if not hasattr(self, '_perf_counter'):
                self._perf_counter = 0
            self._perf_counter += 1
            if self._perf_counter % 100 == 1 or self._perf_counter == 1:
                print(f"[YOLODetector] 性能分析（第{self._perf_counter}帧）：")
                print(f"  推理耗时: {infer_time*1000:.2f}ms")
                print(f"  后处理耗时: {postprocess_time*1000:.2f}ms")
                print(f"  总耗时: {inference_time*1000:.2f}ms")
                print(f"  FPS: {1000/(inference_time*1000):.1f}")
                print(f"  检测数量: {len(detections)}")
            
            return detections
        
        except Exception as e:
            # 打印详细错误堆栈
            import traceback
            error_details = traceback.format_exc()
            print(f"[YOLODetector] 推理失败：{e}")
            print(f"[YOLODetector] 错误详情:\n{error_details}")
            raise RuntimeError(f"推理失败：{e}")
    
    def _infer_onnx(self, input_tensor: np.ndarray) -> np.ndarray:
        """使用 ONNX Runtime 执行推理"""
        if self.session is None:
            raise RuntimeError("ONNX 模型未加载")
        
        outputs = self.session.run(None, {self.input_name: input_tensor})
        
        # 健壮性检查：确保outputs不为空
        if not outputs or len(outputs) == 0:
            raise RuntimeError("推理返回空输出")
        
        return outputs[0]
    
    def _infer_tensorrt(self, input_tensor: np.ndarray) -> np.ndarray:
        """使用 TensorRT 执行推理"""
        if self.trt_context is None:
            raise RuntimeError("TensorRT Engine 未加载")
        
        # TensorRT 10.x 使用 execute_async_v3 或 execute_v3
        # 需要手动管理 GPU 内存，这里使用 ONNX Runtime TensorRT EP 替代
        # 直接加载 Engine 的方式需要 cuda-python 或 pycuda
        
        # 由于 TensorRT 直接推理需要 GPU 内存管理，这里回退到 ONNX Runtime
        raise RuntimeError(
            "TensorRT 直接推理需要 cuda-python 或 pycuda 进行 GPU 内存管理。\n"
            "请使用 backend='onnx_tensorrt' 或安装 cuda-python:\n"
            "pip install cuda-python"
        )
    
    def _infer_rknn(self, input_tensor: np.ndarray) -> np.ndarray:
        """使用 RKNN 执行推理（RK3588 NPU加速）"""
        if not hasattr(self, 'rknn_executor') or self.rknn_executor is None:
            raise RuntimeError("RKNN 模型未加载")
        
        # RKNN模型输入格式转换（NCHW→NHWC）
        # ONNX输入： (1, 3, 640, 640) NCHW格式
        # RKNN输入： (1, 640, 640, 3) NHWC格式
        if input_tensor.shape == (1, 3, 640, 640):
            # 转换NCHW到NHWC
            input_tensor = np.transpose(input_tensor, (0, 2, 3, 1))
        elif input_tensor.shape != (1, 640, 640, 3):
            raise ValueError(f"RKNN输入形状错误：期望 (1, 640, 640, 3) 或 (1, 3, 640, 640)，得到 {input_tensor.shape}")
        
        # 执行RKNN推理
        outputs = self.rknn_executor.run(input_tensor)
        
        # RKNN输出格式处理
        # YOLO模型RKNN输出通常是多个分支（boxes, classes, scores）
        # 需要根据实际模型输出结构调整
        
        # 如果输出是列表，取第一个元素
        if isinstance(outputs, list) and len(outputs) > 0:
            output = outputs[0]
        else:
            output = outputs
        
        # 输出格式转换（根据RKNN模型实际情况）
        # RKNN输出可能是NHWC格式，需要转换为NCHW以便后处理
        # 这里假设输出已经是正确格式，后续可根据实际情况调整
        
        return output
    
    def _postprocess(self, output: np.ndarray, img_height: int, img_width: int) -> List[Detection]:
        """
        后处理：解码 + NMS + 坐标反归一化
        
        自动检测模型输出的类别数量：
        - 如果 output.shape = (num_boxes, 84)，则 num_classes = 80 (COCO)
        - 如果 output.shape = (num_boxes, 6)，则 num_classes = 2 (自定义)
        - 以此类推...
        
        Args:
            output: 模型输出
            img_height: 输入图像高度
            img_width: 输入图像宽度
        
        Returns:
            检测结果列表
        """
        # 调试：打印输出形状（只在首次时打印）
        if not hasattr(self, '_postprocess_debug_printed'):
            print(f"[YOLODetector] 模型输出形状: {output.shape if hasattr(output, 'shape') else type(output)}")
            # 自动检测类别数量
            if output.ndim == 2:
                num_boxes, total_cols = output.shape
                num_classes = total_cols - 4  # 4是边界框坐标
                print(f"[YOLODetector] 检测到 {num_classes} 个类别（从输出列数推断）")
                print(f"[YOLODetector] class_names长度: {len(self.config.class_names)}")
            self._postprocess_debug_printed = True
        
        # 解析模型输出
        # ONNX 导出的 YOLOv8n/YOLO11n 输出格式可能是：
        # - (1, 84, 8400): 需要转置为 (8400, 84)
        # - (1, 8400, 84): 直接取 output[0]
        # 格式取决于 ONNX 导出时的设置，而非模型版本
        
        # 根据形状自动处理
        if output.ndim == 3:
            if output.shape[1] == 84 and output.shape[2] == 8400:
                # 格式 (1, 84, 8400) → 转置为 (8400, 84)
                output = np.transpose(output[0], (1, 0))
            else:
                # 格式 (1, 8400, 84) → 直接取 output[0]
                output = output[0]
        elif output.ndim != 2:
            # 不支持的输出维度
            print(f"[YOLODetector] [错误] 不支持的输出维度: {output.ndim}D，期望 2D 或 3D")
            return []
        
        # 健壮性检查：确保output有足够的列
        if output.shape[1] < 5:
            print(f"[YOLODetector] [错误] 输出列数不足: {output.shape[1]}，期望 >= 5")
            return []
        
        # 自动检测类别数量
        num_classes = output.shape[1] - 4  # 4是边界框坐标 (x1, y1, x2, y2)
        
        # 检查 class_names 是否与模型输出一致
        if len(self.config.class_names) != num_classes:
            print(f"[YOLODetector] [警告] class_names 长度 ({len(self.config.class_names)}) 与模型输出类别数 ({num_classes}) 不匹配")
            print(f"[YOLODetector] [提示] 正在使用前 {min(len(self.config.class_names), num_classes)} 个类别名称")
        
        # 提取检测信息
        predictions = output[:, :4]  # 边界框 (x1, y1, x2, y2)
        class_scores = output[:, 4:]  # 类别置信度（直接是每个类别的概率）
        # 
        # YOLOv5/YOLOv7 或某些导出设置（有 objectness）：
        # - 第 0-3：边界框 [cx, cy, w, h]
        # - 第 4：目标置信度（objectness）
        # - 第 5-84：80 个类别条件概率
        # 
        # 检测输出维度判断格式
        if output.shape[1] == 85:
            # 有 objectness score 的格式（YOLOv5/YOLOv7）
            boxes = output[:, :4]  # (N, 4)
            objectness = output[:, 4]  # (N,) 目标置信度
            class_confs = output[:, 5:]  # (N, 80) 类别条件概率
            
            # 最终置信度 = objectness * class_conf
            scores = objectness * class_confs
        else:
            # 无 objectness score 的格式（YOLOv8/YOLO11 默认）
            boxes = output[:, :4]  # (N, 4)
            scores = output[:, 4:]  # (N, 80) 类别置信度
        
        # 计算每个框的最大置信度和对应类别
        max_class_confs = np.max(scores, axis=1)  # (N,)
        class_ids = np.argmax(scores, axis=1)  # (N,)
        
        # 应用置信度阈值
        valid_mask = max_class_confs > self.config.conf_threshold
        
        if not np.any(valid_mask):
            return []
        
        boxes = boxes[valid_mask]
        max_class_confs = max_class_confs[valid_mask]
        class_ids = class_ids[valid_mask]
        
        # 将 [x, y, w, h] 转换为 [x1, y1, x2, y2]
        # YOLO 输出是中心点坐标 + 宽高
        x_center = boxes[:, 0]
        y_center = boxes[:, 1]
        w = boxes[:, 2]
        h = boxes[:, 3]
        
        x1 = x_center - w / 2
        y1 = y_center - h / 2
        x2 = x_center + w / 2
        y2 = y_center + h / 2
        
        # YOLO ONNX 输出的坐标是相对于输入图像尺寸的像素坐标
        # img_width 和 img_height 是输入张量尺寸（通常为 640x640）
        # 将坐标限制在输入图像范围内
        x1 = np.clip(x1, 0, img_width)
        y1 = np.clip(y1, 0, img_height)
        x2 = np.clip(x2, 0, img_width)
        y2 = np.clip(y2, 0, img_height)
        
        # 过滤过小的检测框（面积过滤）
        areas = (x2 - x1) * (y2 - y1)
        min_box_area = getattr(self.config, 'min_box_area', 10.0)
        valid_area_mask = areas >= min_box_area
        
        if not np.any(valid_area_mask):
            return []
        
        x1 = x1[valid_area_mask]
        y1 = y1[valid_area_mask]
        x2 = x2[valid_area_mask]
        y2 = y2[valid_area_mask]
        max_class_confs = max_class_confs[valid_area_mask]
        class_ids = class_ids[valid_area_mask]
        
        # 应用 NMS（使用 OpenCV 加速）
        indices = self._nms(x1, y1, x2, y2, max_class_confs, class_ids)
        
        # 构建检测结果
        detections = []
        for idx in indices:
            class_id = int(class_ids[idx])
            
            # 获取类别名称（支持自定义模型）
            # 对于自定义模型，class_id可能超出class_names范围
            if class_id < len(self.config.class_names):
                # class_id在有效范围内，使用class_names
                class_name = self.config.class_names[class_id]
            else:
                # class_id超出class_names范围，使用默认名称
                print(f"[YOLODetector] [警告] class_id={class_id} 超出 class_names 范围({len(self.config.class_names)})，使用默认名称")
                class_name = f"class_{class_id}"
            
            # 使用 target_classes 过滤检测结果（如果有配置）
            # target_classes 是用户配置的类别列表（如 ['person', 'car']）
            if hasattr(self.config, 'target_classes') and self.config.target_classes:
                if class_name not in self.config.target_classes:
                    # 此类别不在用户配置的列表中，跳过
                    continue
            
            detection = Detection(
                bbox=(float(x1[idx]), float(y1[idx]), float(x2[idx]), float(y2[idx])),
                confidence=float(max_class_confs[idx]),
                class_id=class_id,
                class_name=class_name
            )
            detections.append(detection)
        
        return detections
    
    def _nms(self, x1: np.ndarray, y1: np.ndarray, x2: np.ndarray, y2: np.ndarray,
             scores: np.ndarray, class_ids: np.ndarray) -> np.ndarray:
        """
        非极大值抑制（NMS）- 使用 OpenCV 加速
        
        Args:
            x1, y1, x2, y2: 边界框坐标
            scores: 置信度
            class_ids: 类别 ID
        
        Returns:
            保留的框的索引
        """
        # 使用 cv2.dnn.NMSBoxes 加速（按类别分别 NMS）
        unique_classes = np.unique(class_ids)
        keep_indices = []
        
        for cls in unique_classes:
            cls_mask = class_ids == cls
            cls_indices = np.where(cls_mask)[0]
            
            # 构建边界框列表 [[x, y, w, h], ...]
            boxes = []
            for idx in cls_indices:
                boxes.append([float(x1[idx]), float(y1[idx]), 
                             float(x2[idx] - x1[idx]), float(y2[idx] - y1[idx])])
            
            cls_scores = scores[cls_mask].astype(np.float32)
            
            # 使用 OpenCV NMS
            nms_indices = cv2.dnn.NMSBoxes(
                boxes, 
                cls_scores.tolist(),
                score_threshold=0.0,  # 已经过滤过阈值，这里设为 0
                nms_threshold=self.config.iou_threshold
            )
            
            # 转换为原始索引
            if len(nms_indices) > 0:
                if isinstance(nms_indices, np.ndarray):
                    nms_indices = nms_indices.flatten()
                for nms_idx in nms_indices:
                    keep_indices.append(cls_indices[nms_idx])
        
        return np.array(keep_indices)
    
    def detect(self, input_tensor: np.ndarray) -> Dict:
        """
        执行检测（detect方法的字典格式兼容接口）
        
        Args:
            input_tensor: 预处理后的张量 (1, 3, 640, 640) float32
        
        Returns:
            字典格式的检测结果
            {
                'detections': [
                    {
                        'class_id': int,
                        'class_name': str,
                        'confidence': float,
                        'bbox': [x1, y1, x2, y2],
                        'track_id': int (可选)
                    },
                    ...
                ],
                'inference_time': float,
                'total_objects': int
            }
        
        Raises:
            RuntimeError: 模型未加载或检测失败
        """
        # 执行推理
        detections = self.infer(input_tensor)
        
        # 转换为字典格式
        detection_list = []
        for det in detections:
            detection_dict = {
                'class_id': det.class_id,
                'class_name': det.class_name,
                'confidence': det.confidence,
                'bbox': det.bbox
            }
            if hasattr(det, 'track_id') and det.track_id is not None:
                detection_dict['track_id'] = det.track_id
            detection_list.append(detection_dict)
        
        # 返回字典格式
        return {
            'detections': detection_list,
            'inference_time': self.get_inference_stats().get('last_inference_time', 0),
            'total_objects': len(detection_list)
        }
    
    def get_inference_stats(self) -> Dict:
        """
        获取推理统计信息
        
        Returns:
            统计信息字典
        """
        avg_time = (
            self._total_inference_time / self._inference_count
            if self._inference_count > 0 else 0
        )
        
        return {
            'inference_count': self._inference_count,
            'total_time': self._total_inference_time,
            'avg_inference_time_ms': avg_time * 1000,
            'fps': 1.0 / avg_time if avg_time > 0 else 0
        }
    
    def reset_stats(self):
        """重置统计信息"""
        self._inference_count = 0
        self._total_inference_time = 0.0
    
    def unload_model(self):
        """卸载模型"""
        # 卸载 ONNX Runtime 会话
        if self.session is not None:
            del self.session
            self.session = None
            self.input_name = None
            self.input_shape = None
        
        # 卸载 TensorRT Engine
        if self.trt_context is not None:
            del self.trt_context
            self.trt_context = None
        
        if self.trt_engine is not None:
            del self.trt_engine
            self.trt_engine = None
        
        print("[YOLODetector] 模型已卸载")


def create_detector(
    model_path: str,
    model_type: str = "yolov8n",
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    backend: str = "onnx_cuda",
    tensorrt_engine_path: str = "",
    tensorrt_engine_cache_path: str = "./trt_cache",
    use_fp16: bool = True
) -> YOLODetector:
    """
    创建 YOLO 检测器
    
    Args:
        model_path: ONNX 模型路径
        model_type: 模型类型（yolov8n 或 yolo11n）
        conf_threshold: 置信度阈值
        iou_threshold: NMS IoU 阈值
        backend: 推理后端（onnx_cuda, onnx_tensorrt, tensorrt）
        tensorrt_engine_path: TensorRT Engine 路径（backend="tensorrt" 时使用）
        tensorrt_engine_cache_path: TensorRT Engine 缓存路径
        use_fp16: 使用 FP16 精度（仅 TensorRT）
    
    Returns:
        YOLODetector 实例
    """
    config = DetectorConfig(
        model_path=model_path,
        model_type=model_type,
        conf_threshold=conf_threshold,
        iou_threshold=iou_threshold,
        backend=backend,
        tensorrt_engine_path=tensorrt_engine_path,
        tensorrt_engine_cache_path=tensorrt_engine_cache_path,
        use_fp16=use_fp16
    )
    detector = YOLODetector(config)
    detector.load_model()
    return detector
