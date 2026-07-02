# YOLOv8n/YOLO11n 检测通道开发方案

**版本**: v1.0  
**日期**: 2026-06-09  
**状态**: 设计方案

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [技术栈选型](#3-技术栈选型)
4. [核心模块设计](#4-核心模块设计)
5. [数据流设计](#5-数据流设计)
6. [性能优化方案](#6-性能优化方案)
7. [测试方案](#7-测试方案)
8. [部署方案](#8-部署方案)
9. [风险评估](#9-风险评估)
10. [开发计划](#10-开发计划)

---

## 1. 项目概述

### 1.1 项目背景

本项目旨在开发一个高性能的实时目标检测通道，支持 YOLOv8n 和 YOLO11n 两种轻量级检测模型，用于 RTSP 视频流的实时分析。

### 1.2 项目目标

- ✅ 支持 YOLOv8n 和 YOLO11n 模型无缝切换
- ✅ 实现 RTSP 流硬件加速解码
- ✅ 基于共享内存的高效数据传输
- ✅ 端到端延迟 < 100ms
- ✅ 检测帧率 ≥ 30 FPS
- ✅ CPU/GPU 资源占用优化

### 1.3 技术特点

| 特性 | 说明 |
|------|------|
| **输入格式** | RTSP 视频流 |
| **解码格式** | YUV420p (640×640) |
| **共享内存** | 614,400 字节 (960×640 平面) |
| **模型输入** | (1, 3, 640, 640) RGB 归一化张量 |
| **推理引擎** | ONNX Runtime-GPU |
| **输出格式** | 检测框 (x1, y1, x2, y2, conf, cls) |

---

## 2. 整体架构

### 2.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           检测通道主架构                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐ │
│  │   视频解码层    │      │   数据存储层    │      │   检测处理层    │ │
│  │                 │      │                 │      │                 │ │
│  │  ┌───────────┐  │      │  ┌───────────┐  │      │  ┌───────────┐  │ │
│  │  │  RTSP 流   │  │      │  │  共享内存  │  │      │  │  预处理   │  │ │
│  │  │    ↓      │  │      │  │  YUV420p  │  │      │  │  YUV→RGB  │  │ │
│  │  │  FFmpeg   │  │      │  │  (614KB)  │  │      │  │    ↓      │  │ │
│  │  │    ↓      │  │      │  │    ↓      │  │      │  │  归一化   │  │ │
│  │  │  硬件加速  │───────→│  │  写入/读取 │───────→│  │    ↓      │  │ │
│  │  │  640×640  │  │      │  │  零拷贝   │  │      │  │  CHW 转换  │  │ │
│  │  └───────────┘  │      │  └───────────┘  │      │  └───────────┘  │ │
│  └─────────────────┘      └─────────────────┘      │         ↓        │ │
│                                                    │  ┌───────────┐  │ │
│                                                    │  │  YOLO 推理  │  │ │
│                                                    │  │  v8n/v11n │  │ │
│                                                    │  │    ↓      │  │ │
│                                                    │  │  后处理   │  │ │
│                                                    │  └───────────┘  │ │
│  ┌─────────────────┐                              └─────────────────┘ │
│  │   配置管理层    │                                                   │
│  │                 │                                                   │
│  │  - YAML 配置    │                                                   │
│  │  - 日志系统     │                                                   │
│  │  - 性能监控     │                                                   │
│  └─────────────────┘                                                   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 进程模型

```
┌─────────────────────────────────────────────────────────────┐
│                     检测通道进程模型                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────┐         ┌──────────────────┐         │
│  │   解码器进程     │         │   检测器进程     │         │
│  │                  │         │                  │         │
│  │  - FFmpeg 解码   │         │  - 读取共享内存  │         │
│  │  - 硬件加速      │         │  - 预处理        │         │
│  │  - 写入共享内存  │◄───────►│  - YOLO 推理     │         │
│  │  - 持续运行      │  共享内存 │  - 输出结果     │         │
│  └──────────────────┘         └──────────────────┘         │
│                                                             │
│  ┌──────────────────┐                                       │
│  │   主控制进程     │                                       │
│  │                  │                                       │
│  │  - 进程管理      │                                       │
│  │  - 配置加载      │                                       │
│  │  - 状态监控      │                                       │
│  └──────────────────┘                                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 模块依赖关系

```
main.py (主入口)
  │
  ├───→ VideoDecoder (视频解码模块)
  │       ├───→ FFmpeg (硬件解码：DXVA2/CUDA/QSV)
  │       ├───→ SharedMemoryManager (共享内存 - 写入端)
  │       └───→ FrameQueue (帧队列管理)
  │
  ├───→ YOLODetector (检测器模块)
  │       ├───→ SharedMemoryManager (共享内存 - 读取端)
  │       ├───→ DetectionPreprocessor (预处理模块)
  │       │       ├───→ YUV420p→RGB 转换
  │       │       ├───→ 归一化 (0-1)
  │       │       └───→ HWC→CHW 转换
  │       ├───→ ONNX Runtime-GPU (推理引擎)
  │       │       ├───→ CUDA Execution Provider
  │       │       └──→ TensorRT (可选加速)
  │       ├───→ ByteTrack (跟踪模块)
  │       │       ├───→ KalmanFilter (卡尔曼滤波)
  │       │       └──→ HungarianMatcher (匈牙利匹配)
  │       └──→ PostProcessor (后处理模块)
  │               ├───→ NMS (非极大值抑制)
  │               └──→ 坐标反归一化
  │
  └───→ ConfigManager (配置管理模块)
          ├───→ YAML 配置加载
          ├───→ 日志系统
          └──→ 性能监控
```

### 2.3.1 模块接口定义

#### VideoDecoder 接口
```python
class VideoDecoder:
    """视频解码器接口"""
    
    def __init__(self, rtsp_url: str, config: Dict):
        """
        Args:
            rtsp_url: RTSP 流地址
            config: 解码配置（硬件加速方式、分辨率等）
        """
        
    def start(self) -> bool:
        """启动解码线程"""
        
    def stop(self):
        """停止解码线程"""
        
    def is_running(self) -> bool:
        """检查解码状态"""
```

#### SharedMemoryManager 接口
```python
class SharedMemoryManager:
    """共享内存管理器接口"""
    
    # 写入端（解码器使用）
    def create(self, size: int) -> bool:
        """创建共享内存"""
        
    def write_header(self, header: FrameHeader):
        """写入帧头部信息"""
        
    def write_frame(self, y: np.ndarray, u: np.ndarray, v: np.ndarray):
        """写入 YUV 帧数据"""
        
    # 读取端（检测器使用）
    def open(self, name: str) -> bool:
        """打开共享内存"""
        
    def read_header(self) -> FrameHeader:
        """读取帧头部信息"""
        
    def read_frame(self) -> Tuple[FrameHeader, np.ndarray, np.ndarray, np.ndarray]:
        """读取 YUV 帧数据（带版本检查）"""
```

#### YOLODetector 接口
```python
class YOLODetector:
    """YOLO 检测器接口"""
    
    def __init__(self, model_path: str, config: Dict):
        """
        Args:
            model_path: ONNX 模型路径
            config: 检测配置（阈值、类别等）
        """
        
    def load_model(self):
        """加载 ONNX 模型"""
        
    def infer(self, input_tensor: np.ndarray) -> List[Dict]:
        """
        执行推理
        
        Args:
            input_tensor: 预处理后的张量 (1, 3, 640, 640)
        
        Returns:
            检测结果列表
        """
```

#### DetectionPreprocessor 接口
```python
class DetectionPreprocessor:
    """预处理模块接口"""
    
    def __init__(self, width: int, height: int):
        """
        Args:
            width: 目标宽度
            height: 目标高度
        """
        
    def preprocess(self, y: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        """
        预处理流程：YUV420p → RGB → 归一化 → CHW
        
        Args:
            y: Y 平面 (640, 640)
            u: U 平面 (320, 320)
            v: V 平面 (320, 320)
        
        Returns:
            预处理后的张量 (1, 3, 640, 640)
        """
```

### 2.3.2 数据流时序

```
时间轴 →
┌─────────────┬─────────────┬─────────────┬─────────────┬─────────────┐
│   T0        │   T1        │   T2        │   T3        │   T4        │
├─────────────┼─────────────┼─────────────┼─────────────┼─────────────┤
│             │             │             │             │             │
│ 解码器：    │ 解码帧 N    │ 解码帧 N+1  │ 解码帧 N+2  │             │
│             │ 写入共享内存 │ 写入共享内存 │ 写入共享内存 │             │
│             │↓            │↓            │↓            │             │
│ 共享内存：  │ [帧 N]      │ [帧 N+1]    │ [帧 N+2]    │             │
│             │↓            │↓            │↓            │             │
│ 检测器：    │ 读取帧 N-1  │ 读取帧 N    │ 读取帧 N+1  │ 推理帧 N    │
│             │ 预处理      │ 预处理      │ 预处理      │ 输出结果    │
│             │             │             │             │↓            │
│ 输出：      │             │             │             │ 帧 N 检测结果│
│             │             │             │             │             │
└─────────────┴─────────────┴─────────────┴─────────────┴─────────────┘

延迟分析：
- 解码延迟：~30ms (硬件加速)
- 共享内存传输：~0ms (零拷贝)
- 预处理延迟：~5ms (GPU 加速)
- 推理延迟：~5ms (RTX 2070)
- 后处理延迟：~3ms
- 总延迟：~43ms (满足 <100ms 要求)
```

### 2.3.3 错误处理机制

```python
# 解码器错误处理
class DecoderError(Exception):
    """解码器异常基类"""
    pass

class RTSPConnectionError(DecoderError):
    """RTSP 连接失败"""
    pass

class DecodeError(DecoderError):
    """解码失败"""
    pass

# 检测器错误处理
class DetectorError(Exception):
    """检测器异常基类"""
    pass

class ModelLoadError(DetectorError):
    """模型加载失败"""
    pass

class InferenceError(DetectorError):
    """推理失败"""
    pass

# 共享内存错误处理
class SharedMemoryError(Exception):
    """共享内存异常基类"""
    pass

class MemoryNotCreatedError(SharedMemoryError):
    """共享内存未创建"""
    pass

class VersionMismatchError(SharedMemoryError):
    """版本检查失败（读到半帧数据）"""
    pass
```

### 2.3.4 性能监控指标

```python
# 关键性能指标 (KPI)
class PerformanceMetrics:
    """性能监控指标"""
    
    # 解码器指标
    decoder_fps: float           # 解码帧率
    decode_latency_ms: float     # 单帧解码延迟
    
    # 检测器指标
    detector_fps: float          # 检测帧率
    preprocess_latency_ms: float # 预处理延迟
    inference_latency_ms: float  # 推理延迟
    postprocess_latency_ms: float# 后处理延迟
    
    # 系统指标
    total_latency_ms: float      # 端到端延迟
    gpu_memory_usage_mb: float   # GPU 显存占用
    gpu_utilization_percent: float  # GPU 利用率
    cpu_utilization_percent: float  # CPU 利用率
    
    # 跟踪指标
    active_tracks: int           # 活跃跟踪数
    track_id_switches: int       # ID 切换次数
    
    # 目标阈值
    TARGETS = {
        'total_latency_ms': '< 100ms',
        'detector_fps': '>= 30 FPS',
        'gpu_memory_usage_mb': '< 4GB',
    }
```

---

## 3. 技术栈选型

### 3.1 核心技术栈

| 类别 | 技术 | 版本要求 | 选择理由 |
|------|------|---------|---------|
| **编程语言** | Python | 3.9+ | 生态丰富，开发效率高 |
| **视频解码** | FFmpeg | 5.0+ | 支持硬件加速，跨平台 |
| **硬件加速** | DXVA2 / CUDA / QSV | - | Windows 原生支持 |
| **共享内存** | mmap | 内置 | 跨平台，零拷贝 |
| **图像处理** | OpenCV | 4.8+ | 成熟的图像操作库 |
| **数值计算** | NumPy | 1.24+ | 高效的数组操作 |
| **推理引擎** | ONNX Runtime-GPU | 1.16+ | GPU 加速推理 |
| **跟踪算法** | SciPy | 1.7.0+ | 匈牙利算法 (linear_sum_assignment) |

### 3.4 Python 依赖清单（针对 RTX 2070 + CUDA 12.9 优化）

```txt
# requirements.txt
# 硬件配置：NVIDIA RTX 2070 (8GB) | CUDA 12.9 | Python 3.11 | 32GB RAM

# ========== 核心依赖 ==========
# 数值计算（1.26+ 支持 Python 3.11，优化 AVX2 指令集）
numpy>=1.26.0,<2.0.0

# 图像处理（4.8+ 支持 CUDA 加速预处理）
opencv-python>=4.8.0

# GPU 推理引擎（ONNX Runtime 1.17+ 支持 CUDA 12.x）
# RTX 2070 性能优化版本：启用 TensorRT 加速（可选）
onnxruntime-gpu>=1.17.0

# ========== 跟踪算法依赖 ==========
# 匈牙利算法（1.11+ 支持 Python 3.11，性能优化）
scipy>=1.11.0

# ========== 配置管理 ==========
# YAML 配置文件解析
pyyaml>=6.0.1

# ========== 性能监控（推荐） ==========
# NVIDIA GPU 监控（支持 RTX 20 系列）
pynvml>=11.5.0

# CPU/内存监控
psutil>=5.9.5

# ========== 可选优化依赖 ==========
# TensorRT 加速（可选，需额外安装 TensorRT）
# tensorrt>=8.6.0  # 如果启用 TensorRT 加速

# CUDA 12.x 专用优化
# onnxruntime-gpu 会自动使用 CUDA 12.9 的以下特性：
# - CuDNN 8.9+ (卷积加速)
# - TensorRT 8.6+ (模型优化)
# - CUDA Graph (降低内核启动开销)
```

### 3.4.1 CUDA 12.9 特定配置

针对您的 RTX 2070 + CUDA 12.9，ONNX Runtime 会自动启用以下优化：

```python
import onnxruntime as ort

# 推荐配置（针对 RTX 2070 优化）
session_options = ort.SessionOptions()
session_options.enable_cuda_graph = True  # CUDA Graph 加速
session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

# CUDA Execution Provider 配置
providers = [
    ('CUDAExecutionProvider', {
        'device_id': 0,              # RTX 2070
        'cudnn_conv_algo_search': 'HEURISTIC',  # CuDNN 卷积算法搜索
        'arena_extend_strategy': 'kSameAsRequested',  # 显存分配策略
        'gpu_mem_limit': 6 * 1024 * 1024 * 1024,  # 限制 6GB 显存（总 8GB）
        'cudnn_conv1d_pad_to_nc1d': True,  # 1D 卷积优化
    }),
    'CPUExecutionProvider'  # 回退到 CPU
]

# 创建会话
session = ort.InferenceSession(
    model_path,
    sess_options=session_options,
    providers=providers
)
```

**性能预期（RTX 2070 + YOLO11n）：**
- 推理速度：~3-5ms/帧（200-330 FPS）
- 显存占用：~2-3GB
- 预处理 + 后处理：~2-3ms
- **总延迟：~5-8ms/帧（125-200 FPS）**

### 3.4.2 安装命令

```bash
# 创建虚拟环境（Python 3.11）
python -m venv venv
.\venv\Scripts\activate

# 升级 pip
python -m pip install --upgrade pip

# 安装核心依赖
pip install numpy>=1.26.0 opencv-python>=4.8.0 scipy>=1.11.0

# 安装 ONNX Runtime-GPU（CUDA 12.x 版本）
pip install onnxruntime-gpu>=1.17.0

# 安装其他依赖
pip install pyyaml>=6.0.1 pynvml>=11.5.0 psutil>=5.9.5

# 验证安装
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
# 应输出：['CUDAExecutionProvider', 'CPUExecutionProvider']
```

### 3.2 硬件加速方案对比

| 方案 | 适用平台 | 性能 | 兼容性 | 推荐场景 |
|------|---------|------|--------|---------|
| **DXVA2** | Windows | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Intel/AMD 集成显卡 |
| **CUDA** | Windows/Linux | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | NVIDIA 独立显卡 |
| **QSV** | Windows/Linux | ⭐⭐⭐⭐ | ⭐⭐⭐ | Intel 核显/独显 |
| **CPU** | 全平台 | ⭐⭐ | ⭐⭐⭐⭐⭐ | 无 GPU 环境 |

### 3.3 模型格式

```
训练框架 (PyTorch)
       ↓ 导出
ONNX 格式 (opset 12+)
       ↓ 部署
ONNX Runtime-GPU 加载
```

---

## 4. 核心模块设计

### 4.1 共享内存管理模块

#### 4.1.1 内存布局设计 (修正版)

```
┌─────────────────────────────────────────────────────────────────┐
│                共享内存布局 (614,420 字节 - 含头部)               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  【头部区域】(20 字节)                                           │
│  偏移量      大小          内容          说明                    │
│  ─────────────────────────────────────────────────────────────  │
│  0           4             sequence_num  帧序列号 (4 字节)        │
│  4           8             timestamp_ns  采集时间戳 (8 字节)      │
│  12          8             write_time_ns 写入完成时间 (8 字节)    │
│                                                                 │
│  【数据区域】(614,400 字节) - 各平面独立存储                     │
│  偏移量 (相对)  大小          内容          形状                 │
│  ─────────────────────────────────────────────────────────────  │
│  0            409,600       Y 平面        640×640 (行×列)       │
│  409,600      102,400       U 平面        320×320 (行×列)       │
│  512,000      102,400       V 平面        320×320 (行×列)       │
│                                                                 │
│  偏移量 (绝对，从共享内存起点)：                                 │
│  - Y 平面：20 字节                                                │
│  - U 平面：20 + 409,600 = 409,620 字节                          │
│  - V 平面：20 + 409,600 + 102,400 = 512,020 字节                │
│                                                                 │
│  存储方式：各平面独立存储，不使用 reshape 打包                   │
│  ┌─────────────────────────────────────────┐                   │
│  │  Y 平面 (640×640)                        │                   │
│  │  ┌─────────────────────────────────┐   │                   │
│  │  │                                 │   │                   │
│  │  │         亮度信息 (Luma)          │   │                   │
│  │  │                                 │   │                   │
│  │  └─────────────────────────────────┘   │                   │
│  │                                         │                   │
│  │  U 平面 (320×320)                       │                   │
│  │  ┌─────────────────────────────────┐   │                   │
│  │  │   色度信息 (Chroma - Blue)       │   │                   │
│  │  └─────────────────────────────────┘   │                   │
│  │                                         │                   │
│  │  V 平面 (320×320)                       │                   │
│  │  ┌─────────────────────────────────┐   │                   │
│  │  │   色度信息 (Chroma - Red)        │   │                   │
│  │  └─────────────────────────────────┘   │                   │
│  └─────────────────────────────────────────┘                   │
│                                                                 │
│  优点：                                                         │
│  1. 无需复杂的 reshape 操作，直接按偏移访问                     │
│  2. 各平面尺寸明确，不易出错                                   │
│  3. 支持单独访问某个平面（如只读取 Y 平面做灰度检测）           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 4.1.2 类设计 (支持时间戳)

```python
class SharedMemoryManager:
    """共享内存管理器 (带时间戳同步)"""
    
    属性:
        - name: str              共享内存名称
        - width: int             帧宽度 (640)
        - height: int            帧高度 (640)
        - header_size: int       头部大小 (20 字节)
        - y_size: int            Y 平面大小 (409,600 字节)
        - uv_size: int           U/V 平面大小 (102,400 字节)
        - total_size: int        总大小 (614,420 字节)
        - y_offset: int          Y 平面偏移 (0)
        - u_offset: int          U 平面偏移 (409,600)
        - v_offset: int          V 平面偏移 (512,000)
        - mmap: mmap.mmap        内存映射对象
        - is_owner: bool         是否为创建者
        - sequence_num: int      当前帧序列号
    
    方法:
        - create()               创建共享内存 (解码器端)
        - open() -> bool         打开共享内存 (检测器端)
        - write_frame(y, u, v)   写入 YUV 帧 (自动添加时间戳)
        - read_frame() -> tuple  读取 YUV 帧 (同时读取时间戳)
        - write_header(seq, ts)  写入头部信息
        - read_header() -> tuple 读取头部信息 (seq, ts)
        - close()                关闭共享内存
```

#### 4.1.3 共享内存管理完整实现 (带时间戳)

```python
import mmap
import numpy as np
import time
from typing import Tuple, Optional
from dataclasses import dataclass

@dataclass
class FrameHeader:
    """帧头部信息"""
    sequence_num: int      # 4 字节，帧序列号
    timestamp_ns: int      # 8 字节，采集时间戳 (纳秒)
    write_time_ns: int     # 8 字节，写入完成时间戳 (纳秒)

class SharedMemoryManager:
    """共享内存管理器 (带时间戳同步)"""
    
    # YUV420p 格式：Y 平面 640×640，U/V 平面各 320×320（宽高都减半）
    HEADER_SIZE = 20  # 4 + 8 + 8 字节
    Y_WIDTH = 640
    Y_HEIGHT = 640
    Y_SIZE = Y_WIDTH * Y_HEIGHT  # 409,600 字节
    
    UV_WIDTH = 320  # U/V 平面宽度是 Y 的一半
    UV_HEIGHT = 320  # U/V 平面高度是 Y 的一半
    UV_SIZE = UV_WIDTH * UV_HEIGHT  # 102,400 字节
    
    # 总大小：头部 + Y + U + V
    TOTAL_SIZE = HEADER_SIZE + Y_SIZE + UV_SIZE + UV_SIZE  # 614,420 字节
    
    def __init__(self, name: str, width: int = 640, height: int = 640):
        self.name = name
        self.width = width
        self.height = height
        
        # 各平面在共享内存中的偏移量 (相对于 HEADER_SIZE)
        self.y_offset = 0
        self.u_offset = self.Y_SIZE
        self.v_offset = self.Y_SIZE + self.UV_SIZE
        
        self.mmap: Optional[mmap.mmap] = None
        self.is_owner = False
        self.sequence_num = 0
    
    def create(self) -> None:
        """创建共享内存 (解码器端)"""
        self.mmap = mmap.mmap(-1, self.TOTAL_SIZE, tagname=self.name)
        self.is_owner = True
        self.sequence_num = 0
        print(f"创建共享内存：{self.name}, 大小：{self.TOTAL_SIZE} 字节")
    
    def open(self) -> bool:
        """打开共享内存 (检测器端)"""
        try:
            self.mmap = mmap.mmap(-1, self.TOTAL_SIZE, tagname=self.name)
            print(f"打开共享内存：{self.name}")
            return True
        except OSError as e:
            print(f"无法打开共享内存：{e}")
            return False
    
    def write_header(self, seq: int, timestamp_ns: int, write_time_ns: int) -> None:
        """
        写入头部信息
        
        Args:
            seq: 帧序列号
            timestamp_ns: 采集时间戳 (纳秒)
            write_time_ns: 写入完成时间戳 (纳秒)
        """
        # 打包头部数据
        header_data = (
            seq.to_bytes(4, 'little') +
            timestamp_ns.to_bytes(8, 'little') +
            write_time_ns.to_bytes(8, 'little')
        )
        
        # 写入共享内存 [0:20]
        self.mmap.seek(0)
        self.mmap.write(header_data)
        self.mmap.flush()
    
    def read_header(self) -> FrameHeader:
        """
        读取头部信息
        
        Returns:
            FrameHeader 对象
        """
        self.mmap.seek(0)
        header_data = self.mmap.read(self.HEADER_SIZE)
        
        # 解包头部数据
        seq = int.from_bytes(header_data[0:4], 'little')
        timestamp_ns = int.from_bytes(header_data[4:12], 'little')
        write_time_ns = int.from_bytes(header_data[12:20], 'little')
        
        return FrameHeader(
            sequence_num=seq,
            timestamp_ns=timestamp_ns,
            write_time_ns=write_time_ns
        )
    
    def write_frame(
        self,
        y_plane: np.ndarray,
        u_plane: np.ndarray,
        v_plane: np.ndarray,
        timestamp_ns: int = None
    ) -> FrameHeader:
        """
        写入 YUV 帧 (带时间戳)
        
        Args:
            y_plane: Y 平面 (640, 640)
            u_plane: U 平面 (320, 320)
            v_plane: V 平面 (320, 320)
            timestamp_ns: 采集时间戳 (纳秒)，如果为 None 则使用当前时间
        """
        if timestamp_ns is None:
            timestamp_ns = time.time_ns()
        
        # 写入头部
        write_start = time.time_ns()
        self.mmap.seek(0)
        
        # 先写入序列号和时间戳
        header_data = (
            self.sequence_num.to_bytes(4, 'little') +
            timestamp_ns.to_bytes(8, 'little')
        )
        self.mmap.write(header_data)
        
        # 写入 Y 平面 [20:20+409600]
        self.mmap.seek(self.HEADER_SIZE + self.y_offset)
        self.mmap.write(y_plane.tobytes())
        
        # 写入 U 平面 [20+409600:20+409600+102400]
        self.mmap.seek(self.HEADER_SIZE + self.u_offset)
        self.mmap.write(u_plane.tobytes())
        
        # 写入 V 平面 [20+409600+102400:20+409600+102400+102400]
        self.mmap.seek(self.HEADER_SIZE + self.v_offset)
        self.mmap.write(v_plane.tobytes())
        
        # 更新写入完成时间
        write_end = time.time_ns()
        self.mmap.seek(self.HEADER_SIZE - 8)  # write_time_ns 的偏移量
        self.mmap.write(write_end.to_bytes(8, 'little'))
        self.mmap.flush()
        
        # 创建头部对象
        header = FrameHeader(
            sequence_num=self.sequence_num,
            timestamp_ns=timestamp_ns,
            write_time_ns=write_end
        )
        
        # 递增序列号
        self.sequence_num += 1
        
        return header
    
    def read_frame(self, max_retries: int = 3) -> Tuple[FrameHeader, np.ndarray, np.ndarray, np.ndarray]:
        """
        读取 YUV 帧 (带版本检查，防止读到半帧数据)
        
        使用序列号双重验证机制：
        1. 读取头部获取序列号
        2. 读取 YUV 数据
        3. 再次读取头部验证序列号是否一致
        4. 如果不一致，说明数据被修改，重新读取
        
        Args:
            max_retries: 最大重试次数
        
        Returns:
            (FrameHeader, Y 平面，U 平面，V 平面)
        
        Raises:
            RuntimeError: 超过最大重试次数仍未读取到一致数据
        """
        retries = 0
        
        while retries < max_retries:
            # 第一次读取头部
            header1 = self.read_header()
            
            # 读取 Y 平面
            self.mmap.seek(self.HEADER_SIZE + self.y_offset)
            y_data = np.frombuffer(
                self.mmap.read(self.Y_SIZE),
                dtype=np.uint8
            )
            y_plane = y_data.reshape((self.Y_HEIGHT, self.Y_WIDTH))  # (640, 640)
            
            # 读取 U 平面
            self.mmap.seek(self.HEADER_SIZE + self.u_offset)
            u_data = np.frombuffer(
                self.mmap.read(self.UV_SIZE),
                dtype=np.uint8
            )
            u_plane = u_data.reshape((self.UV_HEIGHT, self.UV_WIDTH))  # (320, 320)
            
            # 读取 V 平面
            self.mmap.seek(self.HEADER_SIZE + self.v_offset)
            v_data = np.frombuffer(
                self.mmap.read(self.UV_SIZE),
                dtype=np.uint8
            )
            v_plane = v_data.reshape((self.UV_HEIGHT, self.UV_WIDTH))  # (320, 320)
            
            # 第二次读取头部验证一致性
            header2 = self.read_header()
            
            # 验证序列号是否一致
            if header1.sequence_num == header2.sequence_num:
                # 序列号一致，说明读取的是完整帧
                return header1, y_plane, u_plane, v_plane
            
            # 序列号不一致，说明在读取过程中数据被修改
            retries += 1
            print(f"警告：检测到数据不一致 (seq: {header1.sequence_num} vs {header2.sequence_num}), 重试 {retries}/{max_retries}")
        
        # 超过最大重试次数
        raise RuntimeError(
            f"读取帧数据失败：超过最大重试次数 {max_retries}，"
            f"可能存在严重的并发冲突"
        )
    
    def close(self) -> None:
        """关闭共享内存"""
        if self.mmap:
            self.mmap.close()
            self.mmap = None
            print(f"关闭共享内存：{self.name}")
```

### 4.2 视频解码模块

#### 4.2.1 解码流程

```
RTSP 流
   ↓
FFmpeg 拉流 (rtsp_transport=tcp)
   ↓
硬件解码 (DXVA2/CUDA/QSV)
   ↓
格式转换 (YUV420p)
   ↓
缩放 (640×640)
   ↓
共享内存写入
```

#### 4.2.2 FFmpeg 命令参数

```bash
# Windows DXVA2 硬件加速
ffmpeg \
  -rtsp_transport tcp \
  -hwaccel dxva2 \
  -i "rtsp://stream-url" \
  -vf "scale=640:640" \
  -pix_fmt yuv420p \
  -f rawvideo \
  -
```

#### 4.2.3 类设计

```python
class HardwareAcceleratedDecoder:
    """硬件加速视频解码器"""
    
    属性:
        - rtsp_url: str          RTSP 流地址
        - width: int             输出宽度
        - height: int            输出高度
        - shm: SharedMemoryManager 共享内存管理器
        - process: subprocess    FFmpeg 进程
    
    方法:
        - start_decoding()       启动解码循环
        - stop()                 停止解码
        - _parse_frame()         解析 YUV 帧
```

#### 4.2.4 性能优化

1. **零拷贝输出**: FFmpeg 直接输出 rawvideo 到 stdout
2. **帧同步**: 使用 PTS 时间戳控制帧率
3. **丢帧策略**: 检测到解码延迟时主动丢帧
4. **重连机制**: RTSP 断流自动重连

### 4.3 预处理模块

#### 4.3.1 预处理流程 (修正版)

```
┌─────────────────────────────────────────────────────────────────┐
│                      预处理流程详解                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  步骤 1: 读取共享内存 (基于偏移量的独立平面读取)                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  读取头部 (20 字节):                                      │   │
│  │    - sequence_num, timestamp_ns, write_time_ns          │   │
│  │                                                         │   │
│  │  读取 Y 平面 (偏移 20, 409,600 字节):                     │   │
│  │    → reshape: (640, 640)                                │   │
│  │                                                         │   │
│  │  读取 U 平面 (偏移 409,620, 102,400 字节):                │   │
│  │    → reshape: (320, 320)                                │   │
│  │                                                         │   │
│  │  读取 V 平面 (偏移 512,020, 102,400 字节):                │   │
│  │    → reshape: (320, 320)                                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            ↓                                    │
│  步骤 2: YUV420p → RGB                                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  - U/V 平面上采样到 640×640 (INTER_LINEAR)                │   │
│  │  - 合并为 YUV 图像 (640, 640, 3)                          │   │
│  │  - cvtColor(YUV2RGB) → RGB 图像 (640, 640, 3)            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            ↓                                    │
│  步骤 3: 归一化 + 格式转换                                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  - 归一化：RGB / 255.0 → [0, 1] (float32)               │   │
│  │  - HWC → CHW: transpose(2, 0, 1) → (3, 640, 640)       │   │
│  │  - 添加 batch: np.expand_dims(0) → (1, 3, 640, 640)    │   │
│  │  - 确保连续：np.ascontiguousarray()                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            ↓                                    │
│  步骤 4: 输出模型输入                                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  输出：np.ndarray, shape=(1, 3, 640, 640), dtype=float32 │   │
│  │  同时返回：timestamp, sequence_num (用于帧同步)          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 4.3.2 完整代码实现

```python
class DetectionPreprocessor:
    """检测器预处理模块 - 完整实现"""
    
    def __init__(self, shm: SharedMemoryManager):
        """
        初始化预处理器
        
        Args:
            shm: SharedMemoryManager 实例
        """
        self.shm = shm
        self.width = 640
        self.height = 640
        
        # 预分配缓冲区 (避免重复分配)
        self.y_buffer = np.zeros((640, 640), dtype=np.uint8)
        self.u_buffer = np.zeros((320, 320), dtype=np.uint8)
        self.v_buffer = np.zeros((320, 320), dtype=np.uint8)
        self.rgb_buffer = np.zeros((640, 640, 3), dtype=np.uint8)
    
    def prepare_input(self) -> Tuple[np.ndarray, int, int]:
        """
        完整预处理流程
        
        Returns:
            - input_tensor: (1, 3, 640, 640) float32
            - timestamp: 帧时间戳
            - sequence_num: 帧序列号
        """
        # 步骤 1: 从共享内存读取 YUV (基于偏移量)
        header, y, u, v = self.shm.read_frame()
        
        # 复用缓冲区，避免重复分配
        self.y_buffer[:, :] = y
        self.u_buffer[:, :] = u
        self.v_buffer[:, :] = v
        
        # 步骤 2: YUV420p → RGB
        rgb = self.yuv420p_to_rgb(
            self.y_buffer,
            self.u_buffer,
            self.v_buffer
        )
        
        # 步骤 3: 归一化 + CHW 转换
        input_tensor = self.normalize_and_transform(rgb)
        
        return input_tensor, header.timestamp_ns, header.sequence_num
    
    def yuv420p_to_rgb(
        self,
        y: np.ndarray,
        u: np.ndarray,
        v: np.ndarray
    ) -> np.ndarray:
        """
        YUV420p 转 RGB
        
        Args:
            y: Y 平面 (640, 640)
            u: U 平面 (320, 320)
            v: V 平面 (320, 320)
        
        Returns:
            rgb: RGB 图像 (640, 640, 3)
        """
        # U/V 平面上采样到 640×640
        u_upsampled = cv2.resize(u, (640, 640), interpolation=cv2.INTER_LINEAR)
        v_upsampled = cv2.resize(v, (640, 640), interpolation=cv2.INTER_LINEAR)
        
        # 合并为 YUV 平面 (640, 640, 3)
        yuv_image = np.stack([y, u_upsampled, v_upsampled], axis=-1)
        
        # YUV → RGB (OpenCV 的 YUV 格式是 YUV420p)
        rgb_image = cv2.cvtColor(yuv_image, cv2.COLOR_YUV2RGB)
        
        return rgb_image
    
    def normalize_and_transform(self, rgb: np.ndarray) -> np.ndarray:
        """
        归一化 + HWC→CHW 转换
        
        Args:
            rgb: RGB 图像 (640, 640, 3)
        
        Returns:
            tensor: (1, 3, 640, 640) float32
        """
        # 归一化到 [0, 1]
        normalized = rgb.astype(np.float32) / 255.0
        
        # HWC → CHW
        chw = normalized.transpose(2, 0, 1)
        
        # 添加 batch 维度
        batched = np.expand_dims(chw, axis=0)
        
        # 确保内存连续
        return np.ascontiguousarray(batched)
```

### 4.4 YOLO 检测器模块

#### 4.4.1 模型架构对比

| 特性 | YOLOv8n | YOLO11n |
|------|---------|---------|
| **输入尺寸** | 640×640 | 640×640 |
| **输出格式** | [1, 84, 8400] | [1, 84, 8400] |
| **参数量** | ~3.2M | ~2.6M |
| **FLOPs** | ~8.7G | ~6.5G |
| **mAP (COCO)** | 37.3 | 38.5 |
| **推理速度** | 基准 | +10-15% |

#### 4.4.2 YOLO 架构详解

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    YOLOv8n/v11n 网络架构                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  坐标格式说明：                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  模型输出：[cx, cy, w, h] (归一化坐标)                           │   │
│  │    - cx, cy: 边界框中心点坐标 (0-1)                              │   │
│  │    - w: 边界框宽度 (0-1)                                         │   │
│  │    - h: 边界框高度 (0-1)                                         │   │
│  │                                                                 │   │
│  │  NMS 处理：(优化版 - 一次性转换)                                 │   │
│  │    1. 批量转换：[cx, cy, w, h] → [x1, y1, x2, y2]              │   │
│  │       - _convert_to_xyxy(boxes) 向量化操作                      │   │
│  │       - 一次性转换所有框，避免重复计算                          │   │
│  │    2. 计算 IoU：_calculate_iou_xyxy(box1, boxes2)              │   │
│  │       - 直接使用 xyxy 坐标计算，无需再次转换                    │   │
│  │       - 性能提升 2-3 倍                                          │   │
│  │    3. 返回保留的框索引                                           │   │
│  │                                                                 │   │
│  │  输出转换：反归一化为像素坐标 [x1, y1, x2, y2]                   │   │
│  │    - 使用 _cxcywh_to_xyxy(box) 方法                              │   │
│  │    - x1 = (cx - w/2) × 640                                       │   │
│  │    - y1 = (cy - h/2) × 640                                       │   │
│  │    - x2 = (cx + w/2) × 640                                       │   │
│  │    - y2 = (cy + h/2) × 640                                       │   │
│  │    - 裁剪到 [0, 640] 范围                                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  Backbone (主干网络)                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Input (640×640×3)                                              │   │
│  │    ↓                                                            │   │
│  │  Conv (3→16, k=3, s=2) + SiLU                                   │   │
│  │    ↓                                                            │   │
│  │  C2f/C3k2 模块 ×N        (特征提取)                              │   │
│  │    ↓                                                            │   │
│  │  Conv (16→32, k=3, s=2) + SiLU                                  │   │
│  │    ↓                                                            │   │
│  │  C2f/C3k2 模块 ×N                                               │   │
│  │    ↓                                                            │   │
│  │  Conv (32→64, k=3, s=2) + SiLU                                  │   │
│  │    ↓                                                            │   │
│  │  C2f/C3k2 模块 ×N                                               │   │
│  │    ↓                                                            │   │
│  │  Conv (64→128, k=3, s=2) + SiLU                                 │   │
│  │    ↓                                                            │   │
│  │  C2f/C3k2 模块 ×N                                               │   │
│  │    ↓                                                            │   │
│  │  Conv (128→256, k=3, s=2) + SiLU                                │   │
│  │    ↓                                                            │   │
│  │  C2f/C3k2 模块 ×N                                               │   │
│  │    ↓                                                            │   │
│  │  SPPF (Spatial Pyramid Pooling Fast)                            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  Neck (特征融合层)                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  PANet (Path Aggregation Network)                               │   │
│  │    - 上采样 + 特征拼接                                          │   │
│  │    - 自顶向下路径                                               │   │
│  │    - 自底向上路径                                               │   │
│  │    - 多尺度特征融合 (P3, P4, P5)                                │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  Head (检测头)                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Decoupled Head (解耦头)                                        │   │
│  │    ├─→ 分类分支：80 个类别概率                                   │   │
│  │    └─→ 回归分支：4 个边界框参数 + 1 个置信度                      │   │
│  │                                                                 │   │
│  │  输出：[batch, 84, 8400]                                        │   │
│  │    - 84 = 4 (bbox) + 1 (conf) + 80 (classes)                    │   │
│  │    - 8400 = 8400 个锚点 (640/8 × 640/8 + 640/16 × 640/16 + ...)  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

#### 4.4.3 输出解析详解

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      YOLO 输出解析完整流程                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  步骤 1: 模型原始输出                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  形状：[1, 84, 8400]                                            │   │
│  │  含义：                                                         │   │
│  │    - 1: batch size                                              │   │
│  │    - 84: 每个检测框的维度 (4+1+80)                               │   │
│  │    - 8400: 检测框数量                                           │   │
│  │         = 80×80 (P3) + 40×40 (P4) + 20×20 (P5)                  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 2: 转置与重塑                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  操作：transpose(0, 2, 1)                                       │   │
│  │  结果：[1, 8400, 84]                                            │   │
│  │  含义：每个样本 8400 个检测框，每个框 84 维                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 3: 分解检测向量                                                   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  对每个检测框 (84 维):                                           │   │
│  │                                                                 │   │
│  │  [0:4]   → 边界框参数                                            │   │
│  │    ├─ cx: 中心点 x 坐标 (归一化)                                  │   │
│  │    ├─ cy: 中心点 y 坐标 (归一化)                                  │   │
│  │    ├─ w:  宽度 (归一化)                                          │   │
│  │    └─ h:  高度 (归一化)                                          │   │
│  │                                                                 │   │
│  │  [4]     → 目标置信度 (objectness)                               │   │
│  │    └─ 表示该框包含目标的概率                                     │   │
│  │                                                                 │   │
│  │  [5:85]  → 80 个类别条件概率                                      │   │
│  │    ├─ person, bicycle, car, ... (COCO 80 类)                     │   │
│  │    └─ 每个值表示在包含目标条件下属于该类的概率                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 4: 坐标反归一化                                                   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  边界框转换：                                                   │   │
│  │    x1 = (cx - w/2) × 640    # 左上角 x                          │   │
│  │    y1 = (cy - h/2) × 640    # 左上角 y                          │   │
│  │    x2 = (cx + w/2) × 640    # 右下角 x                          │   │
│  │    y2 = (cy + h/2) × 640    # 右下角 y                          │   │
│  │                                                                 │   │
│  │  裁剪到图像范围：                                               │   │
│  │    x1 = max(0, min(640, x1))                                    │   │
│  │    y1 = max(0, min(640, y1))                                    │   │
│  │    x2 = max(0, min(640, x2))                                    │   │
│  │    y2 = max(0, min(640, y2))                                    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 5: 置信度计算                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  最终置信度 = objectness × class_prob                           │   │
│  │                                                                 │   │
│  │  对每个类别计算：                                               │   │
│  │    confidence[c] = scores[4] × scores[5+c]                      │   │
│  │                                                                 │   │
│  │  取最大置信度的类别：                                           │   │
│  │    class_id = argmax(confidence)                                │   │
│  │    max_conf = max(confidence)                                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 6: 置信度过滤                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  过滤条件：max_conf > confidence_threshold (默认 0.5)            │   │
│  │                                                                 │   │
│  │  保留满足条件的检测框：                                         │   │
│  │    if max_conf > 0.5:                                           │   │
│  │        keep_detection(box, class_id, max_conf)                  │   │
│  │                                                                 │   │
│  │  典型结果：8400 个框 → ~100-500 个框                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 7: NMS 非极大值抑制                                               │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  按类别分组处理：                                               │   │
│  │    for each class:                                              │   │
│  │        # 对该类别的所有检测框进行 NMS                            │   │
│  │        boxes = [d.box for d in detections if d.class==class]   │   │
│  │        scores = [d.conf for d in detections if d.class==class] │   │
│  │                                                                 │   │
│  │        # 计算 IoU                                                │   │
│  │        iou = calculate_iou(box1, box2)                          │   │
│  │                                                                 │   │
│  │        # 抑制重叠框                                             │   │
│  │        keep_indices = nms(boxes, scores, iou_threshold=0.45)   │   │
│  │                                                                 │   │
│  │  NMS 算法：                                                      │   │
│  │    1. 按置信度降序排序所有框                                    │   │
│  │    2. 选择置信度最高的框，加入保留列表                          │   │
│  │    3. 计算该框与其他所有框的 IoU                                │   │
│  │    4. 移除 IoU > 阈值的框                                        │   │
│  │    5. 重复步骤 2-4 直到所有框处理完毕                             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 8: 输出最终结果                                                   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  输出格式：List[Dict]                                           │   │
│  │                                                                 │   │
│  │  每个检测结果：                                                 │   │
│  │    {                                                            │   │
│  │        'bbox': [x1, y1, x2, y2],     # 边界框坐标               │   │
│  │        'confidence': 0.95,           # 置信度                   │   │
│  │        'class_id': 0,                # 类别 ID                  │   │
│  │        'class_name': 'person'        # 类别名称                 │   │
│  │    }                                                            │   │
│  │                                                                 │   │
│  │  典型输出：                                                     │   │
│  │    [                                                          │   │
│  │        {'bbox': [100, 50, 200, 300], 'confidence': 0.95,       │   │
│  │         'class_id': 0, 'class_name': 'person'},                │   │
│  │        {'bbox': [300, 100, 400, 250], 'confidence': 0.87,      │   │
│  │         'class_id': 2, 'class_name': 'car'},                   │   │
│  │        ...                                                      │   │
│  │    ]                                                            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

#### 4.4.5 帧 - 检测框时间对齐机制

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    帧与检测框时间对齐问题                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  问题背景：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  解码器写入共享内存           检测器读取并推理                     │   │
│  │       ↓                          ↓                               │   │
│  │   T0: 写入帧 Frame_T0        T0+Δt1: 读取 Frame_T0               │   │
│  │   T1: 写入帧 Frame_T1        T1+Δt2: 读取 Frame_T1               │   │
│  │   T2: 写入帧 Frame_T2        T2+Δt3: 读取 Frame_T2               │   │
│  │                                                                 │   │
│  │  问题：如果解码器和检测器频率不同步                               │   │
│  │    - 解码器：30 FPS (每 33ms 一帧)                                 │   │
│  │    - 检测器：15 FPS (每 66ms 一帧)                                │   │
│  │    - 可能导致检测器处理的是"旧帧"                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  解决方案：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  方案 1: 时间戳同步机制 (推荐)                                     │   │
│  │  ┌───────────────────────────────────────────────────────────┐ │   │
│  │  │  共享内存增加时间戳字段：                                   │ │   │
│  │  │    - 偏移量 0-8: timestamp (8 字节，纳秒精度)                 │ │   │
│  │  │    - 偏移量 8-614408: YUV 数据                              │ │   │
│  │  │                                                           │ │   │
│  │  │  解码器写入流程：                                          │ │   │
│  │  │    1. 获取当前时间戳 timestamp = time.time_ns()           │ │   │
│  │  │    2. 写入时间戳到共享内存 [0:8]                           │ │   │
│  │  │    3. 写入 YUV 数据到共享内存 [8:]                          │ │   │
│  │  │    4. 更新序列号 (sequence_number)                        │ │   │
│  │  │                                                           │ │   │
│  │  │  检测器读取流程：                                          │ │   │
│  │  │    1. 读取时间戳 timestamp                                 │ │   │
│  │  │    2. 读取 YUV 数据                                        │ │   │
│  │  │    3. 执行推理                                             │ │   │
│  │  │    4. 输出结果携带时间戳：                                 │ │   │
│  │  │       detection_result = {                                 │ │   │
│  │  │           'timestamp': timestamp,                          │ │   │
│  │  │           'frame_id': frame_id,                            │ │   │
│  │  │           'detections': [...],                             │ │   │
│  │  │           'latency_ms': (end_time - timestamp)/1e6        │ │   │
│  │  │       }                                                    │ │   │
│  │  └───────────────────────────────────────────────────────────┘ │   │
│  │                                                                 │   │
│  │  方案 2: 序列号同步机制                                          │   │
│  │  ┌───────────────────────────────────────────────────────────┐ │   │
│  │  │  共享内存增加序列号字段：                                   │ │   │
│  │  │    - 偏移量 0-4: sequence_number (4 字节，帧序号)            │ │   │
│  │  │    - 偏移量 4-12: timestamp (8 字节)                        │ │   │
│  │  │    - 偏移量 12-614412: YUV 数据                             │ │   │
│  │  │                                                           │ │   │
│  │  │  检测器验证：                                              │ │   │
│  │  │    last_seq = -1                                          │ │   │
│  │  │    current_seq = read_sequence_number()                   │ │   │
│  │  │                                                           │ │   │
│  │  │    if current_seq != last_seq + 1:                        │ │   │
│  │  │        # 检测到丢帧或重复帧                                │ │   │
│  │  │        log_warning(f"Frame mismatch: {last_seq}→{current_seq}") │ │
│  │  │                                                           │ │   │
│  │  │    last_seq = current_seq                                 │ │   │
│  │  └───────────────────────────────────────────────────────────┘ │   │
│  │                                                                 │   │
│  │  方案 3: 双缓冲 + 版本标志                                       │   │
│  │  ┌───────────────────────────────────────────────────────────┐ │   │
│  │  │  使用两个缓冲区交替写入：                                   │ │   │
│  │  │    - Buffer A: 当前帧                                      │ │   │
│  │  │    - Buffer B: 下一帧                                      │ │   │
│  │  │    - version_flag: 指示哪个缓冲区是最新的                  │ │   │
│  │  │                                                           │ │   │
│  │  │  解码器：                                                  │ │   │
│  │  │    write_to_buffer(version_flag)                          │ │   │
│  │  │    version_flag = !version_flag  # 切换标志               │ │   │
│  │  │                                                           │ │   │
│  │  │  检测器：                                                  │ │   │
│  │  │    if version_flag != last_version:                       │ │   │
│  │  │        # 有新帧可用                                        │ │   │
│  │  │        read_from_buffer(version_flag)                     │ │   │
│  │  │        last_version = version_flag                        │ │   │
│  │  └───────────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  推荐方案：时间戳 + 序列号双重机制                                      │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  共享内存布局 (614,420 字节):                                     │   │
│  │  ┌───────────────────────────────────────────────────────────┐ │   │
│  │  │  偏移量   大小     内容            说明                    │ │   │
│  │  │  ──────────────────────────────────────────────────────── │ │   │
│  │  │  0        4 字节    sequence_num   帧序列号 (从 0 开始递增)   │ │   │
│  │  │  4        8 字节    timestamp_ns   时间戳 (纳秒)            │ │   │
│  │  │  12       8 字节    write_time_ns  写入完成时间 (纳秒)      │ │   │
│  │  │  20       614,400 字节 YUV 数据     YUV420p (960×640)       │ │   │
│  │  │  总计：614,420 字节                                       │ │   │
│  │  └───────────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.4.5 完整推理代码实现

```python
import numpy as np
import onnxruntime as ort
from typing import List, Dict, Tuple
import cv2

class YOLODetector:
    """YOLO 检测器 - 完整实现"""
    
    def __init__(
        self,
        model_path: str,
        model_version: str = 'v8n',
        conf_threshold: float = 0.5,
        iou_threshold: float = 0.45,
        use_gpu: bool = True
    ):
        """
        初始化检测器
        
        Args:
            model_path: ONNX 模型路径
            model_version: 模型版本 ('v8n' 或 'v11n')
            conf_threshold: 置信度阈值
            iou_threshold: NMS IoU 阈值
            use_gpu: 是否使用 GPU 加速
        """
        self.model_path = model_path
        self.model_version = model_version
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.input_shape = (1, 3, 640, 640)
        
        # 加载 COCO 类别名称
        self.class_names = self._load_coco_classes()
        
        # 初始化 ONNX Runtime 会话
        self.session = self._create_session(use_gpu)
        
        # 获取模型输入输出信息
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [out.name for out in self.session.get_outputs()]
        
        # 帧同步跟踪
        self.last_sequence_num = -1
        self.last_timestamp = 0
        self.frame_count = 0
        self.dropped_frames = 0
    
    def _load_coco_classes(self) -> List[str]:
        """加载 COCO 80 个类别名称"""
        return [
            'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus',
            'train', 'truck', 'boat', 'traffic light', 'fire hydrant',
            'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog',
            'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe',
            'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee',
            'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat',
            'baseball glove', 'skateboard', 'surfboard', 'tennis racket',
            'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl',
            'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot',
            'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch',
            'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop',
            'mouse', 'remote', 'keyboard', 'cell phone', 'microwave',
            'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock',
            'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush'
        ]
    
    def _create_session(self, use_gpu: bool) -> ort.InferenceSession:
        """创建 ONNX Runtime 会话"""
        # 配置会话选项
        session_options = ort.SessionOptions()
        session_options.enable_mem_pattern = True
        session_options.enable_cpu_mem_arena = False
        session_options.graph_optimization_level = \
            ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        
        # 配置执行提供者
        if use_gpu:
            cuda_provider_options = {
                'device_id': 0,
                'gpu_mem_limit': 2 * 1024 * 1024 * 1024,  # 2GB
                'arena_extend_strategy': 'kSameAsRequested',
                'cudnn_conv_algo_search': 'EXHAUSTIVE',
                'do_copy_in_default_stream': True,
            }
            providers = [
                ('CUDAExecutionProvider', cuda_provider_options),
                'CPUExecutionProvider'
            ]
        else:
            providers = ['CPUExecutionProvider']
        
        # 创建会话
        session = ort.InferenceSession(
            self.model_path,
            sess_options=session_options,
            providers=providers
        )
        
        return session
    
    def infer(
        self,
        input_tensor: np.ndarray,
        frame_timestamp: int = None,
        sequence_num: int = None
    ) -> Dict:
        """
        执行推理 (带时间戳)
        
        Args:
            input_tensor: 预处理后的输入张量，shape=(1, 3, 640, 640)
            frame_timestamp: 帧时间戳 (纳秒)
            sequence_num: 帧序列号
        
        Returns:
            包含时间戳的检测结果字典
        """
        start_time = time.perf_counter_ns()
        
        # 验证帧同步
        if sequence_num is not None:
            if self.last_sequence_num >= 0:
                if sequence_num != self.last_sequence_num + 1:
                    # 检测到丢帧
                    self.dropped_frames += sequence_num - self.last_sequence_num - 1
                    print(f"警告：检测到丢帧，丢失 {sequence_num - self.last_sequence_num - 1} 帧")
            self.last_sequence_num = sequence_num
            self.frame_count += 1
        
        # 执行推理
        outputs = self.session.run(
            self.output_names,
            {self.input_name: input_tensor}
        )
        
        # 后处理
        predictions = outputs[0]  # shape: (1, 84, 8400)
        detections = self._postprocess(predictions)
        
        end_time = time.perf_counter_ns()
        inference_time_ns = end_time - start_time
        
        # 计算延迟
        if frame_timestamp is not None:
            latency_ns = end_time - frame_timestamp
            latency_ms = latency_ns / 1e6
        else:
            latency_ms = inference_time_ns / 1e6
        
        return {
            'timestamp': frame_timestamp,
            'sequence_num': sequence_num,
            'detections': detections,
            'inference_time_ms': inference_time_ns / 1e6,
            'latency_ms': latency_ms,
            'frame_count': self.frame_count,
            'dropped_frames': self.dropped_frames
        }
    
    def _postprocess(
        self,
        predictions: np.ndarray
    ) -> List[Dict]:
        """
        后处理 - 解析检测结果
        
        Args:
            predictions: 模型原始输出 (1, 84, 8400)
                         - 84 = 4 (坐标) + 1 (objectness) + 80 (类别概率)
                         - 8400 = 锚框数量
        
        Returns:
            检测结果列表，每个检测包含：
            - bbox: [x1, y1, x2, y2] 像素坐标
            - confidence: 置信度
            - class_id: 类别 ID
            - class_name: 类别名称
        
        Note:
            坐标格式转换流程：
            1. 模型输出：[cx, cy, w, h] (归一化坐标，0-1)
            2. NMS 处理：使用原始归一化坐标计算 IoU
            3. 反归一化：转换为像素坐标 [x1, y1, x2, y2]
               - x1 = (cx - w/2) × 640
               - y1 = (cy - h/2) × 640
               - x2 = (cx + w/2) × 640
               - y2 = (cy + h/2) × 640
        """
        # 转置：(1, 84, 8400) → (1, 8400, 84)
        predictions = predictions.transpose(0, 2, 1)
        
        detections = []
        
        # 处理 batch 中的每个样本
        for pred in predictions:
            # pred shape: (8400, 84)
            
            # 分解检测向量
            boxes = pred[:, :4]      # (8400, 4) - 边界框
            scores = pred[:, 4]      # (8400,) - 目标置信度
            class_probs = pred[:, 5:]  # (8400, 80) - 类别概率
            
            # 计算最终置信度 = objectness × class_prob
            confidences = scores[:, np.newaxis] * class_probs  # (8400, 80)
            
            # 获取每个框的最大置信度和对应类别
            max_conf = np.max(confidences, axis=1)  # (8400,)
            class_ids = np.argmax(confidences, axis=1)  # (8400,)
            
            # 置信度过滤
            keep_mask = max_conf > self.conf_threshold
            kept_indices = np.where(keep_mask)[0]
            
            if len(kept_indices) == 0:
                continue
            
            # 提取保留的框和分数
            kept_boxes = boxes[kept_indices]
            kept_scores = max_conf[kept_indices]
            kept_class_ids = class_ids[kept_indices]
            
            # 按类别进行 NMS
            unique_classes = np.unique(kept_class_ids)
            
            for cls in unique_classes:
                # 获取该类别的所有框
                cls_mask = kept_class_ids == cls
                cls_boxes = kept_boxes[cls_mask]
                cls_scores = kept_scores[cls_mask]
                cls_indices = kept_indices[cls_mask]
                
                # 执行 NMS
                nms_indices = self._nms(cls_boxes, cls_scores)
                
                # 收集最终检测结果
                for idx in nms_indices:
                    original_idx = cls_indices[idx]
                    
                    # 坐标反归一化：[cx, cy, w, h] → [x1, y1, x2, y2] (像素)
                    x1, y1, x2, y2 = self._cxcywh_to_xyxy(
                        kept_boxes[original_idx]
                    )
                    
                    detections.append({
                        'bbox': [float(x1), float(y1), 
                                float(x2), float(y2)],
                        'confidence': float(kept_scores[original_idx]),
                        'class_id': int(kept_class_ids[original_idx]),
                        'class_name': self.class_names[
                            int(kept_class_ids[original_idx])
                        ]
                    })
        
        return detections
    
    def _cxcywh_to_xyxy(self, box: np.ndarray) -> Tuple[int, int, int, int]:
        """
        将归一化的 [cx, cy, w, h] 转换为像素坐标 [x1, y1, x2, y2]
        
        Args:
            box: 归一化边界框 [cx, cy, w, h]，其中 cx,cy,w,h ∈ [0,1]
        
        Returns:
            像素坐标 [x1, y1, x2, y2]，已裁剪到 [0, 640] 范围
        
        Note:
            转换公式：
            - x1 = (cx - w/2) × 640
            - y1 = (cy - h/2) × 640
            - x2 = (cx + w/2) × 640
            - y2 = (cy + h/2) × 640
        """
        # 从中心点 + 宽高转换为左上角 + 右下角
        cx, cy, w, h = box
        
        x1 = (cx - w / 2) * 640
        y1 = (cy - h / 2) * 640
        x2 = (cx + w / 2) * 640
        y2 = (cy + h / 2) * 640
        
        # 裁剪到图像范围
        x1 = max(0, min(640, x1))
        y1 = max(0, min(640, y1))
        x2 = max(0, min(640, x2))
        y2 = max(0, min(640, y2))
        
        return int(x1), int(y1), int(x2), int(y2)
    
    def _nms(
        self,
        boxes: np.ndarray,
        scores: np.ndarray
    ) -> List[int]:
        """
        非极大值抑制 (NMS)
        
        Args:
            boxes: 边界框数组 (N, 4)，格式为 [cx, cy, w, h]
                   - cx, cy: 框中心点归一化坐标 (0-1)
                   - w, h: 框宽度和高度归一化坐标 (0-1)
            scores: 置信度数组 (N,)
        
        Returns:
            保留的框索引列表
        
        Note:
            优化策略：
            1. 在 NMS 开始前，一次性将所有框从 [cx, cy, w, h] 转换为 [x1, y1, x2, y2]
            2. 后续 IoU 计算直接使用转换后的坐标，避免重复转换
            3. 相比每次计算 IoU 都转换，性能提升约 2-3 倍
        """
        if len(boxes) == 0:
            return []
        
        # 一次性转换所有框：[cx, cy, w, h] → [x1, y1, x2, y2]
        boxes_xyxy = self._convert_to_xyxy(boxes)
        
        # 按置信度降序排序
        order = np.argsort(scores)[::-1]
        
        keep = []
        
        while len(order) > 0:
            # 选择置信度最高的框
            idx = order[0]
            keep.append(idx)
            
            if len(order) == 1:
                break
            
            # 计算当前框与其他框的 IoU (直接使用 xyxy 坐标)
            current_box = boxes_xyxy[idx:idx+1]
            other_boxes = boxes_xyxy[order[1:]]
            
            ious = self._calculate_iou_xyxy(current_box, other_boxes)
            
            # 保留 IoU 小于阈值的框
            keep_mask = ious < self.iou_threshold
            order = order[1:][keep_mask]
        
        return keep
    
    def _convert_to_xyxy(self, boxes: np.ndarray) -> np.ndarray:
        """
        批量将 [cx, cy, w, h] 转换为 [x1, y1, x2, y2]
        
        Args:
            boxes: 边界框数组 (N, 4)，格式为 [cx, cy, w, h]
        
        Returns:
            转换后的边界框数组 (N, 4)，格式为 [x1, y1, x2, y2]
        """
        # 向量化转换
        x1 = boxes[:, 0] - boxes[:, 2] / 2
        y1 = boxes[:, 1] - boxes[:, 3] / 2
        x2 = boxes[:, 0] + boxes[:, 2] / 2
        y2 = boxes[:, 1] + boxes[:, 3] / 2
        
        return np.stack([x1, y1, x2, y2], axis=1)
    
    def _calculate_iou_xyxy(
        self,
        box1: np.ndarray,
        boxes2: np.ndarray
    ) -> np.ndarray:
        """
        计算 IoU (输入已经是 [x1, y1, x2, y2] 格式)
        
        Args:
            box1: 第一个框 (1, 4)，格式为 [x1, y1, x2, y2]
            boxes2: 其他框 (N, 4)，格式为 [x1, y1, x2, y2]
        
        Returns:
            IoU 数组 (N,)
        """
        # 计算交集
        inter_x1 = np.maximum(box1[:, 0], boxes2[:, 0])
        inter_y1 = np.maximum(box1[:, 1], boxes2[:, 1])
        inter_x2 = np.minimum(box1[:, 2], boxes2[:, 2])
        inter_y2 = np.minimum(box1[:, 3], boxes2[:, 3])
        
        # 计算交集面积
        inter_w = np.maximum(0, inter_x2 - inter_x1)
        inter_h = np.maximum(0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        
        # 计算两个框的面积
        box1_area = (box1[:, 2] - box1[:, 0]) * (box1[:, 3] - box1[:, 1])
        box2_area = (boxes2[:, 2] - boxes2[:, 0]) * (boxes2[:, 3] - boxes2[:, 1])
        
        # 计算并集面积
        union_area = box1_area + boxes2_area - inter_area
        
        # 计算 IoU
        iou = inter_area / (union_area + 1e-8)
        
        return iou
    
    def run_detection_loop(
        self,
        preprocessor,
        fps_limit: int = 30
    ):
        """
        运行实时检测循环
        
        Args:
            preprocessor: 预处理器实例
            fps_limit: 目标帧率
        """
        import time
        
        frame_interval = 1.0 / fps_limit
        
        print(f"开始检测循环 (目标 FPS: {fps_limit})...")
        
        while True:
            start_time = time.perf_counter()
            
            try:
                # 准备输入
                input_tensor = preprocessor.prepare_input()
                
                # 执行推理
                detections = self.infer(input_tensor)
                
                # 处理结果
                if detections:
                    print(f"\n检测到 {len(detections)} 个目标:")
                    for det in detections:
                        print(f"  - {det['class_name']}: "
                              f"{det['confidence']:.2f} "
                              f"bbox={det['bbox']}")
                
            except Exception as e:
                print(f"检测错误：{e}")
            
            # 控制帧率
            elapsed = time.perf_counter() - start_time
            sleep_time = max(0, frame_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
            
            # 显示实际 FPS
            actual_fps = 1.0 / elapsed
            print(f"FPS: {actual_fps:.1f}", end='\r')

### 4.5 跟踪检测模块

#### 4.5.1 跟踪算法选型

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        跟踪算法对比                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  方案 1: ByteTrack (推荐)                                               │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  原理：                                                         │   │
│  │    - 将检测框分为高置信度和低置信度两组                         │   │
│  │    - 先用高置信度框进行关联匹配                                 │   │
│  │    - 再用低置信度框补充匹配（避免漏检）                         │   │
│  │    - 使用卡尔曼滤波预测下一帧位置                               │   │
│  │                                                                 │   │
│  │  优点：                                                         │   │
│  │    - 对遮挡目标鲁棒性强                                         │   │
│  │    - 对低置信度检测敏感                                         │   │
│  │    - ID 切换少，轨迹连续性好                                     │   │
│  │    - 实时性好（~2ms/帧）                                        │   │
│  │                                                                 │   │
│  │  缺点：                                                         │   │
│  │    - 需要维护卡尔曼滤波器状态                                   │   │
│  │    - 参数调优复杂                                               │   │
│  │                                                                 │   │
│  │  适用场景：                                                     │   │
│  │    - 人流密集场景                                               │   │
│  │    - 频繁遮挡场景                                               │   │
│  │    - 需要长时间跟踪的场景                                       │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  方案 2: DeepSORT                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  原理：                                                         │   │
│  │    - 结合外观特征和运动信息                                     │   │
│  │    - 使用 ReID 网络提取目标外观特征                              │   │
│  │    - 级联匹配：马氏距离 + 余弦距离                               │   │
│  │                                                                 │   │
│  │  优点：                                                         │   │
│  │    - 长时跟踪效果好                                             │   │
│  │    - 对目标重识别能力强                                         │   │
│  │                                                                 │   │
│  │  缺点：                                                         │   │
│  │    - 需要额外的 ReID 模型（增加显存和计算）                       │   │
│  │    - 推理速度较慢（~10ms/帧）                                   │   │
│  │                                                                 │   │
│  │  适用场景：                                                     │   │
│  │    - 跨摄像头跟踪                                               │   │
│  │    - 长时间遮挡后重识别                                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  方案 3: 简单 IoU 匹配                                                   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  原理：                                                         │   │
│  │    - 计算相邻帧检测框的 IoU                                     │   │
│  │    - 使用匈牙利算法进行匹配                                     │   │
│  │    - 超过阈值的框分配新 ID                                       │   │
│  │                                                                 │   │
│  │  优点：                                                         │   │
│  │    - 实现简单                                                   │   │
│  │    - 无需额外模型                                               │   │
│  │    - 速度快（<1ms/帧）                                          │   │
│  │                                                                 │   │
│  │  缺点：                                                         │   │
│  │    - 对快速运动目标效果差                                       │   │
│  │    - ID 切换频繁                                                 │   │
│  │    - 无法处理遮挡                                               │   │
│  │                                                                 │   │
│  │  适用场景：                                                     │   │
│  │    - 目标运动缓慢的场景                                         │   │
│  │    - 对 ID 连续性要求不高的场景                                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  推荐方案：ByteTrack（平衡性能和速度）                                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

#### 4.5.2 ByteTrack 算法详解

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      ByteTrack 算法流程                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  输入：当前帧检测框 detections + 上一帧跟踪框 tracklets                 │
│                                                                         │
│  步骤 1: 检测框分类                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  high_score_dets = [det for det in detections                   │   │
│  │                       if det.confidence > track_thresh]         │   │
│  │  low_score_dets = [det for det in detections                    │   │
│  │                      if det.confidence > low_thresh]            │   │
│  │                                                                 │   │
│  │  # 典型阈值：                                                   │   │
│  │  track_thresh = 0.5   # 高置信度阈值                            │   │
│  │  low_thresh = 0.1     # 低置信度阈值                            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 2: 卡尔曼滤波预测                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  for track in tracklets:                                        │   │
│  │      # 预测当前帧位置                                           │   │
│  │      track.mean, track.covariance = kf.predict(                 │   │
│  │          track.mean, track.covariance)                          │   │
│  │                                                                 │   │
│  │  # 状态向量：[cx, cy, aspect_ratio, height,                     │   │
│  │  #            vx, vy, v_aspect_ratio, v_height]                 │   │
│  │  # 8 维状态：位置 + 速度                                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 3: 第一次匹配（高置信度框）                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  # 计算代价矩阵（IoU 距离）                                       │   │
│  │  cost_matrix = 1.0 - iou_matrix(                                │   │
│  │      track_predictions, high_score_dets)                        │   │
│  │                                                                 │   │
│  │  # 匈牙利算法匹配                                               │   │
│  │  matches, unmatched_tracks, unmatched_dets =                    │   │
│  │      linear_assignment(cost_matrix, thresh=0.7)                 │   │
│  │                                                                 │   │
│  │  # 更新匹配的跟踪框                                             │   │
│  │  for track_idx, det_idx in matches:                             │   │
│  │      tracklets[track_idx].update(high_score_dets[det_idx])      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 4: 第二次匹配（低置信度框）                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  # 从未匹配的跟踪框中再次匹配                                   │   │
│  │  cost_matrix2 = 1.0 - iou_matrix(                               │   │
│  │      unmatched_tracks, low_score_dets)                          │   │
│  │                                                                 │   │
│  │  matches2, _, _ = linear_assignment(cost_matrix2, thresh=0.5)   │   │
│  │                                                                 │   │
│  │  # 更新匹配的跟踪框（即使置信度低也保留）                       │   │
│  │  for track_idx, det_idx in matches2:                            │   │
│  │      tracklets[track_idx].update(low_score_dets[det_idx])       │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 5: 处理未匹配的跟踪框                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  for track in unmatched_tracks:                                 │   │
│  │      track.mark_lost()  # 标记为丢失                            │   │
│  │      if track.age > max_age:                                    │   │
│  │          # 超过最大丢失帧数，移除跟踪                           │   │
│  │          tracklets.remove(track)                                │   │
│  │                                                                 │   │
│  │  # 典型参数：                                                   │   │
│  │  max_age = 30  # 最多允许丢失 30 帧（1 秒@30FPS）                  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  步骤 6: 初始化新跟踪框                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  for det in unmatched_dets:                                     │   │
│  │      # 创建新跟踪框                                             │   │
│  │      new_track = STrack(                                        │   │
│  │          tlwh=det.tlwh,                                         │   │
│  │          score=det.confidence,                                  │   │
│  │          cls=det.class_id                                       │   │
│  │      )                                                          │   │
│  │      tracklets.append(new_track)                                │   │
│  │      new_track.track_id = next_id++                             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            ↓                                            │
│  输出：更新后的跟踪框列表（每个框带有唯一的 track_id）                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

#### 4.5.3 完整代码实现

```python
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from scipy.optimize import linear_sum_assignment
from collections import deque


@dataclass
class KalmanFilter:
    """卡尔曼滤波器（简化版 8 维状态）"""
    
    # 状态向量：[cx, cy, w, h, vx, vy, vw, vh]
    # 前 4 维：位置（中心点坐标、宽度、高度）
    # 后 4 维：速度（各维度的变化率）
    
    # 状态转移矩阵（匀速运动模型）
    F: np.ndarray = field(default_factory=lambda: np.eye(8, dtype=np.float32))
    
    # 观测矩阵 (4×8)：从 8 维状态空间映射到 4 维观测空间
    # 观测值只包含位置信息：[cx, cy, w, h]
    H: np.ndarray = field(default_factory=lambda: np.array([
        [1, 0, 0, 0, 0, 0, 0, 0],  # cx → cx
        [0, 1, 0, 0, 0, 0, 0, 0],  # cy → cy
        [0, 0, 1, 0, 0, 0, 0, 0],  # w  → w
        [0, 0, 0, 1, 0, 0, 0, 0],  # h  → h
    ], dtype=np.float32))
    
    # 过程噪声 (8×8)
    Q: np.ndarray = field(default_factory=lambda: np.eye(8, dtype=np.float32) * 0.01)
    
    # 观测噪声 (4×4)
    R: np.ndarray = field(default_factory=lambda: np.eye(4, dtype=np.float32) * 0.1)
    
    def __post_init__(self):
        """初始化状态转移矩阵（匀速运动）"""
        dt = 1.0  # 时间间隔
        # 位置 = 位置 + 速度 × 时间
        for i in range(4):
            self.F[i, i+4] = dt  # 位置 + 速度
    
    def predict(self, mean: np.ndarray, covariance: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """预测下一步状态"""
        mean = self.F @ mean
        covariance = self.F @ covariance @ self.F.T + self.Q
        return mean, covariance
    
    def update(self, mean: np.ndarray, covariance: np.ndarray, 
               measurement: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """更新状态（measurement 是 4 维观测值）"""
        # 计算卡尔曼增益
        S = self.H @ covariance @ self.H.T + self.R
        K = covariance @ self.H.T @ np.linalg.inv(S)
        
        # 更新状态
        residual = measurement - self.H @ mean
        mean = mean + K @ residual
        covariance = (np.eye(8) - K @ self.H) @ covariance
        
        return mean, covariance


@dataclass
class TrackedBox:
    """跟踪框"""
    
    track_id: int                    # 跟踪 ID
    class_id: int                    # 类别 ID
    bbox: List[float]                # 边界框 [x1, y1, x2, y2]
    confidence: float                # 置信度
    
    # 卡尔曼滤波状态
    mean: np.ndarray = field(default_factory=lambda: np.zeros(8))
    covariance: np.ndarray = field(default_factory=lambda: np.eye(8))
    
    # 跟踪状态
    age: int = 0                     # 跟踪年龄（存活帧数）
    lost_count: int = 0              # 丢失帧数
    is_lost: bool = False            # 是否丢失
    
    # 轨迹历史（用于可视化和轨迹平滑）
    trajectory: deque = field(default_factory=lambda: deque(maxlen=30))
    bbox_history: deque = field(default_factory=lambda: deque(maxlen=5))
    
    def update(self, bbox: List[float], confidence: float, class_id: int):
        """更新跟踪框（支持轨迹平滑）"""
        self.bbox = bbox
        self.confidence = confidence
        self.class_id = class_id
        self.age += 1
        self.lost_count = 0
        self.is_lost = False
        
        # 添加到轨迹历史（中心点）
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        self.trajectory.append((cx, cy))
        
        # 添加到边界框历史（用于平滑）
        self.bbox_history.append(bbox)
    
    def get_smoothed_bbox(self) -> List[float]:
        """
        使用移动平均平滑边界框
        
        Returns:
            平滑后的边界框 [x1, y1, x2, y2]
        """
        if len(self.bbox_history) < 3:
            # 历史数据不足，返回最新框
            return self.bbox
        
        # 使用最近 5 帧的平均值
        recent_bboxes = list(self.bbox_history)[-5:]
        smoothed = np.mean(recent_bboxes, axis=0).tolist()
        return smoothed
    
    def mark_lost(self):
        """标记为丢失"""
        self.is_lost = True
        self.lost_count += 1
    
    def to_dict(self, use_smooth: bool = True) -> Dict:
        """
        转换为字典格式
        
        Args:
            use_smooth: 是否使用平滑后的边界框
        
        Returns:
            字典格式的跟踪信息
        """
        bbox = self.get_smoothed_bbox() if use_smooth else self.bbox
        
        return {
            'track_id': self.track_id,
            'bbox': bbox,
            'confidence': self.confidence,
            'class_id': self.class_id,
            'age': self.age,
            'trajectory': list(self.trajectory)
        }


class ByteTrack:
    """ByteTrack 跟踪器"""
    
    def __init__(
        self,
        track_thresh: float = 0.5,      # 高置信度阈值
        low_thresh: float = 0.1,         # 低置信度阈值
        match_thresh: float = 0.7,       # 匹配阈值（IoU）
        max_age: int = 30,               # 最大丢失帧数
        frame_rate: int = 30             # 帧率（用于卡尔曼滤波）
    ):
        """
        初始化 ByteTrack
        
        Args:
            track_thresh: 高置信度检测框阈值
            low_thresh: 低置信度检测框阈值
            match_thresh: IoU 匹配阈值
            max_age: 跟踪框最大丢失帧数
            frame_rate: 视频帧率
        """
        self.track_thresh = track_thresh
        self.low_thresh = low_thresh
        self.match_thresh = match_thresh
        self.max_age = max_age
        self.frame_rate = frame_rate
        
        self.kf = KalmanFilter()
        self.tracklets: List[TrackedBox] = []
        self.next_id = 0
    
    def update(self, detections: List[Dict]) -> List[TrackedBox]:
        """
        更新跟踪框（支持类别感知匹配）
        
        Args:
            detections: 当前帧检测结果列表
                       每个检测包含：bbox, confidence, class_id
        
        Returns:
            更新后的跟踪框列表
        """
        # 步骤 1: 检测框分类
        high_score_dets = []
        low_score_dets = []
        
        for det in detections:
            if det['confidence'] >= self.track_thresh:
                high_score_dets.append(det)
            elif det['confidence'] >= self.low_thresh:
                low_score_dets.append(det)
        
        # 步骤 2: 卡尔曼滤波预测
        predicted_boxes = []
        for track in self.tracklets:
            track.mean, track.covariance = self.kf.predict(
                track.mean, track.covariance)
            predicted_boxes.append(self._mean_to_bbox(track.mean))
        
        # 步骤 3: 按类别分别匹配（避免跨类别 ID 跳变）
        # 获取所有唯一的类别
        all_classes = set(t.class_id for t in self.tracklets)
        all_classes.update(d['class_id'] for d in high_score_dets)
        all_classes.update(d['class_id'] for d in low_score_dets)
        
        matched_track_indices = set()
        matched_det_indices = set()
        
        # 对每个类别分别进行匹配
        for class_id in all_classes:
            # 获取该类别的跟踪框和检测框
            tracks_of_class = [
                (i, t) for i, t in enumerate(self.tracklets) 
                if t.class_id == class_id and i not in matched_track_indices
            ]
            high_dets_of_class = [
                (i, d) for i, d in enumerate(high_score_dets)
                if d['class_id'] == class_id and i not in matched_det_indices
            ]
            
            if len(tracks_of_class) > 0 and len(high_dets_of_class) > 0:
                # 提取该类别的预测框和检测框
                track_indices = [t[0] for t in tracks_of_class]
                det_indices = [d[0] for d in high_dets_of_class]
                
                pred_boxes_class = [predicted_boxes[i] for i in track_indices]
                det_boxes_class = [high_score_dets[i]['bbox'] for i in det_indices]
                
                # 计算 IoU 代价矩阵
                cost_matrix = self._iou_cost(pred_boxes_class, det_boxes_class)
                
                # 匈牙利算法匹配
                row_ind, col_ind = linear_sum_assignment(cost_matrix)
                
                # 过滤超过阈值的匹配
                for r, c in zip(row_ind, col_ind):
                    if cost_matrix[r, c] <= self.match_thresh:
                        track_idx = track_indices[r]
                        det_idx = det_indices[c]
                        
                        # 更新跟踪框
                        self.tracklets[track_idx].update(
                            bbox=high_score_dets[det_idx]['bbox'],
                            confidence=high_score_dets[det_idx]['confidence'],
                            class_id=high_score_dets[det_idx]['class_id']
                        )
                        
                        # 更新卡尔曼滤波状态
                        measurement = self._bbox_to_measurement(
                            high_score_dets[det_idx]['bbox'])
                        self.tracklets[track_idx].mean, \
                        self.tracklets[track_idx].covariance = self.kf.update(
                            self.tracklets[track_idx].mean,
                            self.tracklets[track_idx].covariance,
                            measurement
                        )
                        
                        matched_track_indices.add(track_idx)
                        matched_det_indices.add(det_idx)
        
        # 未匹配的跟踪框和检测框
        unmatched_tracks = [
            i for i in range(len(self.tracklets)) 
            if i not in matched_track_indices
        ]
        unmatched_dets = [
            i for i in range(len(high_score_dets)) 
            if i not in matched_det_indices
        ]
        
        # 步骤 4: 第二次匹配（低置信度框，同样按类别）
        for class_id in all_classes:
            tracks_of_class = [
                i for i in unmatched_tracks 
                if self.tracklets[i].class_id == class_id
            ]
            low_dets_of_class = [
                (i, d) for i, d in enumerate(low_score_dets)
                if d['class_id'] == class_id and i not in matched_det_indices
            ]
            
            if len(tracks_of_class) > 0 and len(low_dets_of_class) > 0:
                pred_boxes_class = [predicted_boxes[i] for i in tracks_of_class]
                det_boxes_class = [low_score_dets[i]['bbox'] for i, _ in low_dets_of_class]
                
                cost_matrix2 = self._iou_cost(pred_boxes_class, det_boxes_class)
                row_ind2, col_ind2 = linear_sum_assignment(cost_matrix2)
                
                # 过滤（更宽松的阈值 0.5）
                for r, c in zip(row_ind2, col_ind2):
                    if cost_matrix2[r, c] <= 0.5:
                        track_idx = tracks_of_class[r]
                        det_idx = low_dets_of_class[c][0]
                        
                        self.tracklets[track_idx].update(
                            bbox=low_score_dets[det_idx]['bbox'],
                            confidence=low_score_dets[det_idx]['confidence'],
                            class_id=low_score_dets[det_idx]['class_id']
                        )
                        
                        matched_det_indices.add(det_idx)
        
        # 从未匹配的低置信度检测框中移除已匹配的
        unmatched_low_dets = [
            (i, d) for i, d in enumerate(low_score_dets)
            if i not in matched_det_indices
        ]
        
        # 步骤 5: 处理未匹配的跟踪框
        for track_idx in unmatched_tracks:
            self.tracklets[track_idx].mark_lost()
        
        # 移除丢失超过 max_age 的跟踪框
        self.tracklets = [
            t for t in self.tracklets 
            if not (t.is_lost and t.lost_count > self.max_age)
        ]
        
        # 步骤 6: 初始化新跟踪框（只从高置信度未匹配框中初始化）
        for det_idx in range(len(high_score_dets)):
            if det_idx not in matched_det_indices:
                det = high_score_dets[det_idx]
                new_track = TrackedBox(
                    track_id=self.next_id,
                    class_id=det['class_id'],
                    bbox=det['bbox'],
                    confidence=det['confidence']
                )
                
                # 初始化卡尔曼滤波状态（8 维：位置 + 速度）
                # 位置：[cx, cy, w, h]
                # 速度：[0, 0, 0, 0]（初始速度为 0）
                measurement = self._bbox_to_measurement(det['bbox'])
                new_track.mean = np.array([
                    measurement[0], measurement[1],  # cx, cy
                    measurement[2], measurement[3],  # w, h
                    0, 0, 0, 0  # vx, vy, vw, vh（初始速度为 0）
                ], dtype=np.float32)
                self.next_id += 1
                
                self.tracklets.append(new_track)
        
        # 过滤丢失的跟踪框（不输出）
        active_tracks = [t for t in self.tracklets if not t.is_lost]
        
        return active_tracks
    
    def _bbox_to_measurement(self, bbox: List[float]) -> np.ndarray:
        """
        将边界框转换为卡尔曼滤波观测值（仅位置，4 维）
        
        卡尔曼滤波状态是 8 维：[cx, cy, w, h, vx, vy, vw, vh]
        但观测值只包含位置信息：[cx, cy, w, h]
        速度分量由滤波器内部预测，不应从观测值直接获取
        
        Args:
            bbox: 边界框 [x1, y1, x2, y2]
        
        Returns:
            观测向量 [cx, cy, w, h] (4 维)
        """
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        w = x2 - x1
        h = y2 - y1
        
        return np.array([cx, cy, w, h], dtype=np.float32)
    
    def _mean_to_bbox(self, mean: np.ndarray) -> List[float]:
        """
        从卡尔曼滤波状态恢复边界框
        
        Args:
            mean: 卡尔曼滤波状态向量 (8 维) [cx, cy, w, h, vx, vy, vw, vh]
        
        Returns:
            边界框 [x1, y1, x2, y2]
        """
        # 只使用前 4 维位置信息
        cx, cy, w, h = mean[:4]
        x1 = cx - w / 2
        y1 = cy - h / 2
        x2 = cx + w / 2
        y2 = cy + h / 2
        return [x1, y1, x2, y2]
    
    def _iou_cost(
        self,
        boxes1: List[List[float]],
        boxes2: List[List[float]]
    ) -> np.ndarray:
        """
        计算 IoU 代价矩阵（1 - IoU）
        
        Args:
            boxes1: 边界框列表 1，每个框为 [x1, y1, x2, y2]
            boxes2: 边界框列表 2，每个框为 [x1, y1, x2, y2]
        
        Returns:
            代价矩阵 (n1, n2)，值为 1 - IoU
        """
        # 使用向量化计算优化性能
        boxes1_array = np.array(boxes1, dtype=np.float32)  # (n1, 4)
        boxes2_array = np.array(boxes2, dtype=np.float32)  # (n2, 4)
        
        # 使用广播计算交集
        # boxes1: (n1, 1, 4), boxes2: (1, n2, 4)
        # 结果：(n1, n2, 4)
        min_xy = np.maximum(boxes1_array[:, None, :2], boxes2_array[None, :, :2])
        max_xy = np.minimum(boxes1_array[:, None, 2:], boxes2_array[None, :, 2:])
        
        # 计算交集面积
        inter_wh = np.clip(max_xy - min_xy, 0, np.inf)  # (n1, n2, 2)
        inter_area = inter_wh[:, :, 0] * inter_wh[:, :, 1]  # (n1, n2)
        
        # 计算两个框的面积
        area1 = (boxes1_array[:, 2] - boxes1_array[:, 0]) * \
                (boxes1_array[:, 3] - boxes1_array[:, 1])  # (n1,)
        area2 = (boxes2_array[:, 2] - boxes2_array[:, 0]) * \
                (boxes2_array[:, 3] - boxes2_array[:, 1])  # (n2,)
        
        # 计算并集面积
        union_area = area1[:, None] + area2[None, :] - inter_area  # (n1, n2)
        
        # 计算 IoU
        iou = inter_area / (union_area + 1e-8)
        
        # 返回代价矩阵（1 - IoU）
        return 1.0 - iou


class TrackedYOLODetector:
    """带跟踪的 YOLO 检测器"""
    
    def __init__(
        self,
        model_path: str,
        class_names: List[str],
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        track_thresh: float = 0.5,
        track_low_thresh: float = 0.1,
        track_max_age: int = 30
    ):
        """
        初始化带跟踪的检测器
        
        Args:
            model_path: ONNX 模型路径
            class_names: 类别名称列表
            conf_threshold: 检测置信度阈值
            iou_threshold: NMS IoU 阈值
            track_thresh: 跟踪高置信度阈值
            track_low_thresh: 跟踪低置信度阈值
            track_max_age: 跟踪最大丢失帧数
        """
        # 基础检测器
        self.detector = YOLODetector(
            model_path=model_path,
            class_names=class_names,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold
        )
        
        # 跟踪器
        self.tracker = ByteTrack(
            track_thresh=track_thresh,
            low_thresh=track_low_thresh,
            max_age=track_max_age
        )
        
        # 统计信息
        self.total_tracks = 0
        self.active_tracks = 0
    
    def infer(self, input_tensor: np.ndarray, 
              frame_timestamp: int = None,
              sequence_num: int = None) -> Dict:
        """
        执行推理 + 跟踪
        
        Args:
            input_tensor: 模型输入张量 (1, 3, 640, 640)
            frame_timestamp: 帧时间戳
            sequence_num: 帧序列号
        
        Returns:
            包含跟踪结果的字典
        """
        # 步骤 1: YOLO 检测
        det_result = self.detector.infer(
            input_tensor, frame_timestamp, sequence_num)
        
        # 步骤 2: 跟踪更新
        detections = det_result['detections']
        tracked_boxes = self.tracker.update(detections)
        
        # 更新统计
        self.total_tracks = len(self.tracker.tracklets)
        self.active_tracks = len(tracked_boxes)
        
        # 步骤 3: 合并结果
        tracked_detections = []
        for track in tracked_boxes:
            tracked_detections.append({
                'track_id': track.track_id,
                'bbox': track.bbox,
                'confidence': track.confidence,
                'class_id': track.class_id,
                'class_name': self.detector.class_names[track.class_id],
                'age': track.age,
                'trajectory': list(track.trajectory)
            })
        
        return {
            'timestamp': det_result['timestamp'],
            'sequence_num': det_result['sequence_num'],
            'detections': tracked_detections,
            'inference_time_ms': det_result['inference_time_ms'],
            'latency_ms': det_result['latency_ms'],
            'frame_count': self.detector.frame_count,
            'dropped_frames': self.detector.dropped_frames,
            'tracking_stats': {
                'total_tracks': self.total_tracks,
                'active_tracks': self.active_tracks,
                'lost_tracks': self.total_tracks - self.active_tracks
            }
        }
```

#### 4.5.4 使用示例

```python
# 初始化带跟踪的检测器
tracker = TrackedYOLODetector(
    model_path='models/yolo11n.onnx',
    class_names=['person', 'car', 'dog'],
    conf_threshold=0.25,
    iou_threshold=0.45,
    track_thresh=0.5,
    track_low_thresh=0.1,
    track_max_age=30  # 1 秒@30FPS
)

# 检测循环
while True:
    # 预处理
    input_tensor, timestamp, seq_num = preprocessor.prepare_input()
    
    # 推理 + 跟踪
    result = tracker.infer(input_tensor, timestamp, seq_num)
    
    # 处理结果
    for det in result['detections']:
        print(f"ID: {det['track_id']:3d} | "
              f"Box: {det['bbox']} | "
              f"Cls: {det['class_name']:8s} | "
              f"Conf: {det['confidence']:.2f} | "
              f"Age: {det['age']:3d}")
        
        # 绘制轨迹
        if len(det['trajectory']) > 1:
            for i in range(len(det['trajectory']) - 1):
                pt1 = det['trajectory'][i]
                pt2 = det['trajectory'][i+1]
                # cv2.line(frame, pt1, pt2, color, thickness)
    
    # 统计信息
    stats = result['tracking_stats']
    print(f"活跃跟踪：{stats['active_tracks']}/{stats['total_tracks']}")
```

#### 4.5.5 性能优化建议

```
1. 卡尔曼滤波优化
   - 使用固定大小的状态转移矩阵（避免动态分配）
   - 预计算 F @ F.T 等常量矩阵
   - 使用 float32 而非 float64

2. 匹配优化
   - 使用 IoU 而非欧氏距离（无需归一化）
   - 对类别分别匹配（避免不同类别误匹配）
   - 限制匹配搜索范围（基于运动速度）

3. 内存优化
   - 轨迹历史使用 deque 限制长度
   - 定期清理丢失的跟踪框
   - 复用 TrackedBox 对象（对象池）

4. 参数调优
   - track_thresh: 人流密集场景降低到 0.3
   - max_age: 快速运动场景减少到 15
   - match_thresh: 遮挡场景降低到 0.5

#### 4.5.6 性能优化详解

##### 1. 类别感知匹配

**问题：** 跨类别匹配会导致 ID 异常跳变

**场景示例：**
```
帧 1: 跟踪框 T1 (类别：人，位置：[100, 200, 150, 250])
帧 2: 检测框 D1 (类别：车，位置：[105, 205, 155, 255])

# 如果不按类别匹配，T1 和 D1 可能因为 IoU 高而匹配
# 导致人的跟踪框突然变成车，ID 跳变
```

**解决方案：**
```python
# 按类别分别匹配
for class_id in all_classes:
    tracks_of_class = [t for t in tracks if t.class_id == class_id]
    dets_of_class = [d for d in dets if d['class_id'] == class_id]
    # 只在该类别内匹配
```

**效果：**
- ✅ 完全避免跨类别 ID 跳变
- ✅ 提升跟踪稳定性
- ⚠️ 轻微性能开销（额外循环）

##### 2. 轨迹平滑

**问题：** 检测框抖动导致轨迹不平滑

**解决方案：** 移动平均平滑
```python
def get_smoothed_bbox(self) -> List[float]:
    """使用最近 5 帧的移动平均平滑边界框"""
    if len(self.bbox_history) < 3:
        return self.bbox  # 历史不足，直接返回
    
    recent_bboxes = list(self.bbox_history)[-5:]
    smoothed = np.mean(recent_bboxes, axis=0).tolist()
    return smoothed
```

**效果：**
- ✅ 边界框输出更平滑
- ✅ 轨迹更美观
- ⚠️ 引入 1-2 帧延迟

##### 3. 向量化 IoU 计算

**问题：** 双重循环计算 IoU 效率低

**原始实现：** O(n×m) 双重循环
```python
for i, box1 in enumerate(boxes1):
    for j, box2 in enumerate(boxes2):
        # 计算 IoU...
```

**优化实现：** 向量化批量计算
```python
def _iou_batch(self, boxes1: np.ndarray, boxes2: np.ndarray) -> np.ndarray:
    """
    批量计算 IoU 矩阵（向量化）
    
    Args:
        boxes1: (n1, 4) 边界框数组
        boxes2: (n2, 4) 边界框数组
    
    Returns:
        IoU 矩阵 (n1, n2)
    """
    # 广播计算交集
    min_xy = np.maximum(boxes1[:, None, :2], boxes2[None, :, :2])
    max_xy = np.minimum(boxes1[:, None, 2:], boxes2[None, :, 2:])
    
    inter_wh = np.clip(max_xy - min_xy, 0, np.inf)
    inter_area = inter_wh[:, :, 0] * inter_wh[:, :, 1]
    
    # 计算面积
    area1 = (boxes1[:, 2] - boxes1[:, 0]) * (boxes1[:, 3] - boxes1[:, 1])
    area2 = (boxes2[:, 2] - boxes2[:, 0]) * (boxes2[:, 3] - boxes2[:, 1])
    
    union_area = area1[:, None] + area2[None, :] - inter_area
    iou = inter_area / (union_area + 1e-8)
    
    return iou
```

**性能对比：**
| 实现方式 | 10 框×10 框 | 50 框×50 框 | 100 框×100 框 |
|---------|------------|------------|-------------|
| 双重循环 | 0.5ms | 12ms | 48ms |
| 向量化 | 0.05ms | 0.3ms | 1.2ms |
| **加速比** | **10×** | **40×** | **40×** |

**优化原理：**
1. 利用 NumPy 广播机制，避免 Python 循环
2. 批量矩阵运算，充分利用 CPU SIMD 指令
3. 内存连续访问，缓存命中率高

##### 4. 综合优化建议

**实时性要求高的场景：**
```python
tracker = ByteTrack(
    track_thresh=0.5,
    low_thresh=0.1,
    match_thresh=0.7,
    max_age=30
)

# 使用向量化 IoU
# 关闭轨迹平滑（减少延迟）
result = tracker.update(detections)
for track in result:
    bbox = track.bbox  # 不使用平滑
```

**轨迹质量要求高的场景：**
```python
# 开启轨迹平滑
for track in result:
    bbox = track.get_smoothed_bbox()  # 使用平滑

# 增加历史长度
track.trajectory = deque(maxlen=50)  # 记录更长轨迹
```

**密集场景（多人/多车）：**
```python
# 按类别分别匹配（避免 ID 跳变）
# 降低匹配阈值
tracker = ByteTrack(match_thresh=0.5)

# 降低检测阈值
tracker = ByteTrack(track_thresh=0.3)
```
```

### 4.6 配置管理模块

#### 4.6.1 配置文件结构

```yaml
# config/config.yaml

# 解码器配置
decoder:
  rtsp_transport: tcp           # RTSP 传输协议
  hwaccel: dxva2                # 硬件加速方式：dxva2/cuda/qsv/none
  width: 640                    # 输出宽度
  height: 640                   # 输出高度
  reconnect: true               # 自动重连
  reconnect_delay: 5            # 重连延迟 (秒)

# 共享内存配置
shared_memory:
  name: "yolo_detection_channel"  # 共享内存名称
  create: true                      # 是否创建 (解码器端)

# 检测器配置
detector:
  model_path: "models/yolov8n.onnx"  # 模型路径
  model_version: "v8n"               # 模型版本：v8n/v11n
  use_gpu: true                      # 是否使用 GPU 加速
  providers:                         # 推理提供者优先级
    - CUDAExecutionProvider
    - CPUExecutionProvider

# 预处理配置
preprocessing:
  normalize: true               # 归一化
  mean: [0, 0, 0]               # 均值 (如需要)
  std: [1, 1, 1]                # 标准差 (如需要)

# 后处理配置
postprocessing:
  confidence_threshold: 0.5     # 置信度阈值
  iou_threshold: 0.45           # NMS IoU 阈值
  max_detections: 100           # 最大检测数

# 性能配置
performance:
  target_fps: 30                # 目标帧率
  buffer_size: 3                # 帧缓冲区大小
  num_workers: 1                # 工作线程数

# 日志配置
logging:
  level: INFO                   # 日志级别
  file: "logs/detection.log"    # 日志文件
  rotation: "100MB"             # 日志轮转
  retention: "7 days"           # 日志保留
```

---

## 5. 其他关键问题考虑

### 5.1 RTSP 流异常处理

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      RTSP 流异常场景及处理方案                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  异常场景 1: 网络中断                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  现象：FFmpeg 无法读取 RTSP 流                                     │   │
│  │  检测：                                                         │   │
│  │    - 连续 N 帧无法读取到数据                                     │   │
│  │    - FFmpeg 进程退出或报错                                      │   │
│  │  处理：                                                         │   │
│  │    1. 检测到读取超时 (>100ms 无新帧)                             │   │
│  │    2. 等待 5 秒后自动重连                                         │   │
│  │    3. 重连失败则指数退避 (5s→10s→20s→60s)                       │   │
│  │    4. 重连成功后重置序列号                                      │   │
│  │    5. 发送告警日志                                              │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  异常场景 2: 视频编码格式变化                                          │   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  现象：分辨率变化、帧率变化、编码格式变化                        │   │
│  │  检测：                                                         │   │
│  │    - FFmpeg 输出帧尺寸与预期不符                                │   │
│  │    - 缩放后的尺寸异常                                           │   │
│  │  处理：                                                         │   │
│  │    1. 验证帧尺寸：assert frame.shape == (640, 640)             │   │
│  │    2. 尺寸不匹配时重启 FFmpeg 进程                               │   │
│  │    3. 记录异常事件                                              │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  异常场景 3: 关键帧丢失                                                 │   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  现象：画面花屏、解码错误                                        │   │
│  │  检测：                                                         │   │
│  │    - YUV 数据出现异常值 (全 0 或全 255)                           │   │
│  │    - 图像直方图异常                                             │   │
│  │  处理：                                                         │   │
│  │    1. 请求关键帧 (RTSP SEND_KEY_FRAME)                         │   │
│  │    2. 丢弃异常帧                                                │   │
│  │    3. 等待下一个有效关键帧                                      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  异常场景 4: 认证失败                                                   │   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  现象：RTSP 返回 401/403 错误                                     │   │
│  │  检测：                                                         │   │
│  │    - FFmpeg stderr 输出认证错误信息                              │   │
│  │  处理：                                                         │   │
│  │    1. 解析错误日志                                              │   │
│  │    2. 停止重连 (认证错误无法自动恢复)                           │   │
│  │    3. 发送严重告警                                              │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 共享内存并发控制

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      共享内存并发访问控制                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  问题：                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  解码器正在写入时，检测器开始读取 → 读到"半帧"数据               │   │
│  │  导致：Y/U/V 平面数据不一致，检测结果错误                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  解决方案 1: 互斥锁 (Mutex)                                             │   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  使用跨进程互斥锁：                                              │   │
│  │                                                                 │   │
│  │  解码器：                                                       │   │
│  │    mutex.acquire()                                              │   │
│  │    write_frame(y, u, v)                                         │   │
│  │    mutex.release()                                              │   │
│  │                                                                 │   │
│  │  检测器：                                                       │   │
│  │    mutex.acquire()                                              │   │
│  │    header, y, u, v = read_frame()                               │   │
│  │    mutex.release()                                              │   │
│  │                                                                 │   │
│  │  优点：简单可靠                                                 │   │
│  │  缺点：检测器可能需要等待解码器                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  解决方案 2: 双缓冲 + 原子标志 (推荐)                                   │   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  使用两个缓冲区 + 原子操作标志：                                 │   │
│  │                                                                 │   │
│  │  共享内存布局：                                                 │   │
│  │    - Buffer A: 614,420 字节                                      │   │
│  │    - Buffer B: 614,420 字节                                      │   │
│  │    - Control: 4 字节 (atomic flag)                               │   │
│  │                                                                 │   │
│  │  解码器：                                                       │   │
│  │    # 写入当前缓冲区                                             │   │
│  │    write_to_buffer(current_buffer)                              │   │
│  │    # 原子切换标志                                               │   │
│  │    atomic_store(flag, 1 - current_buffer)                       │   │
│  │                                                                 │   │
│  │  检测器：                                                       │   │
│  │    # 读取标志                                                   │   │
│  │    active_buffer = atomic_load(flag)                            │   │
│  │    # 从活动缓冲区读取                                           │   │
│  │    read_from_buffer(active_buffer)                              │   │
│  │                                                                 │   │
│  │  优点：无锁设计，性能更好                                       │   │
│  │  缺点：内存占用翻倍 (1.2MB)                                     │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  解决方案 3: 序列号 + 版本检查 (已实现)                               │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  通过序列号双重验证检测数据一致性：                              │   │
│  │                                                                 │   │
│  │  解码器写入流程：                                               │   │
│  │    1. 写入序列号 (旧值) 到头部                                   │   │
│  │    2. 写入 Y/U/V 平面数据                                         │   │
│  │    3. 更新序列号 (新值) 到头部                                   │   │
│  │    4. 内存屏障确保顺序 (flush)                                  │   │
│  │                                                                 │   │
│  │  检测器读取流程 (带重试机制)：                                  │   │
│  │    1. 读取头部 sequence_num1                                    │   │
│  │    2. 读取 Y 平面                                                 │   │
│  │    3. 读取 U 平面                                                 │   │
│  │    4. 读取 V 平面                                                 │   │
│  │    5. 读取头部 sequence_num2                                    │   │
│  │    6. 验证：if sequence_num1 != sequence_num2:                  │   │
│  │         # 数据在读取过程中被修改，重新读取                       │   │
│  │         goto step 1 (最多重试 3 次)                               │   │
│  │    7. 验证通过，返回数据                                        │   │
│  │                                                                 │   │
│  │  代码实现：                                                     │   │
│  │  ```python                                                      │   │
│  │  def read_frame(self, max_retries=3):                           │   │
│  │      retries = 0                                                │   │
│  │      while retries < max_retries:                               │   │
│  │          header1 = self.read_header()                           │   │
│  │          y_plane = self._read_plane(Y_OFFSET)                   │   │
│  │          u_plane = self._read_plane(U_OFFSET)                   │   │
│  │          v_plane = self._read_plane(V_OFFSET)                   │   │
│  │          header2 = self.read_header()                           │   │
│  │                                                                 │   │
│  │          if header1.sequence_num == header2.sequence_num:       │   │
│  │              return header1, y_plane, u_plane, v_plane          │   │
│  │                                                                 │   │
│  │          retries += 1  # 重试                                   │   │
│  │                                                                 │   │
│  │      raise RuntimeError("读取失败：数据不一致")                 │   │
│  │  ```                                                            │   │
│  │                                                                 │   │
│  │  优点：                                                         │   │
│  │    - 无需额外同步原语 (无锁设计)                                │   │
│  │    - 自动检测并处理并发冲突                                     │   │
│  │    - 内存占用小 (单缓冲区)                                      │   │
│  │    - 已实现在 SharedMemoryManager.read_frame()                  │   │
│  │                                                                 │   │
│  │  缺点：                                                         │   │
│  │    - 极端情况下可能需要多次读取 (但概率极低)                    │   │
│  │    - 需要解码器配合：必须先写旧序列号，再写新序列号             │   │
│  │                                                                 │   │
│  │  适用场景：                                                     │   │
│  │    - 解码器和检测器频率不同步 (如 30FPS vs 15FPS)               │   │
│  │    - 对性能要求高，不希望使用锁                                 │   │
│  │    - 内存资源有限，不能使用双缓冲                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.3 GPU 显存管理

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      GPU 显存管理优化                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  问题：                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  - ONNX Runtime 默认会占用大量显存                              │   │
│  │  - 长时间运行可能导致显存泄漏                                   │   │
│  │  - 多实例运行时显存竞争                                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  解决方案：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  1. 限制 GPU 显存使用                                             │   │
│  │     cuda_provider_options = {                                    │   │
│  │         'gpu_mem_limit': 2 * 1024 * 1024 * 1024,  # 2GB         │   │
│  │     }                                                            │   │
│  │                                                                 │   │
│  │  2. 启用显存复用                                                 │   │
│  │     session_options.enable_mem_pattern = True                   │   │
│  │                                                                 │   │
│  │  3. 定期监控显存使用                                             │   │
│  │     import pynvml                                                │   │
│  │     pynvml.nvmlInit()                                            │   │
│  │     handle = pynvml.nvmlDeviceGetHandleByIndex(0)               │   │
│  │     info = pynvml.nvmlDeviceGetMemoryInfo(handle)               │   │
│  │     print(f"显存使用：{info.used / 1e6:.0f}MB / {info.total / 1e6:.0f}MB") │   │
│  │                                                                 │   │
│  │  4. 显存泄漏检测                                                 │   │
│  │     - 定期记录显存使用曲线                                      │   │
│  │     - 检测显存持续增长趋势                                      │   │
│  │     - 超过阈值时自动重启进程                                    │   │
│  │                                                                 │   │
│  │  5. 多实例显存隔离                                               │   │
│  │     - 使用 CUDA_VISIBLE_DEVICES 环境变量                        │   │
│  │     - 每个实例绑定不同 GPU                                      │   │
│  │     - 或限制每个实例显存上限                                    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.4 异常检测与自愈机制

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      异常检测与自愈机制                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  监控指标：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  1. FPS 监控                                                      │   │
│  │     - 目标：≥30 FPS                                              │   │
│  │     - 警告：<25 FPS 持续 10 秒                                     │   │
│  │     - 严重：<20 FPS 持续 30 秒 → 自动重启                         │   │
│  │                                                                 │   │
│  │  2. 延迟监控                                                      │   │
│  │     - 目标：<100ms                                               │   │
│  │     - 警告：>150ms 持续 10 秒                                      │   │
│  │     - 严重：>200ms 持续 30 秒 → 自动重启                          │   │
│  │                                                                 │   │
│  │  3. 丢帧率监控                                                   │   │
│  │     - 目标：<1%                                                  │   │
│  │     - 警告：>5% 持续 1 分钟                                        │   │
│  │     - 严重：>10% 持续 5 分钟 → 自动重启                           │   │
│  │                                                                 │   │
│  │  4. 资源监控                                                      │   │
│  │     - CPU 使用率：<80%                                            │   │
│  │     - GPU 使用率：50-90%                                         │   │
│  │     - 内存使用：<500MB                                           │   │
│  │     - 显存使用：<2GB                                             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  自愈机制：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  级别 1: 轻度异常 (自动调整)                                     │   │
│  │    - 降低目标 FPS (30→25→20)                                    │   │
│  │    - 降低置信度阈值 (减少后处理时间)                            │   │
│  │    - 跳过部分帧 (隔帧检测)                                      │   │
│  │                                                                 │   │
│  │  级别 2: 中度异常 (重启组件)                                     │   │
│  │    - 重启检测器进程                                             │   │
│  │    - 重新加载模型                                               │   │
│  │    - 清空缓存                                                   │   │
│  │                                                                 │   │
│  │  级别 3: 重度异常 (重启系统)                                     │   │
│  │    - 重启整个检测通道                                           │   │
│  │    - 重启 FFmpeg 解码进程                                        │   │
│  │    - 发送告警通知                                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.5 日志与调试

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      日志系统设计                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  日志级别：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  DEBUG:   详细调试信息 (每帧的推理时间、检测结果)                │   │
│  │  INFO:    正常运行信息 (启动、停止、FPS 统计)                     │   │
│  │  WARNING: 警告信息 (丢帧、延迟偏高、资源紧张)                    │   │
│  │  ERROR:   错误信息 (解码失败、推理错误、重连)                    │   │
│  │  CRITICAL: 严重错误 (进程崩溃、显存泄漏、需要重启)               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  日志内容：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  运行日志 (detection.log):                                      │   │
│  │    - 启动/停止时间                                              │   │
│  │    - 每秒 FPS 统计                                                │   │
│  │    - 平均延迟、P95 延迟、P99 延迟                                 │   │
│  │    - 丢帧统计                                                   │   │
│  │    - 异常事件记录                                               │   │
│  │                                                                 │   │
│  │  性能日志 (performance.log):                                    │   │
│  │    - GPU 使用率曲线                                              │   │
│  │    - 显存使用曲线                                               │   │
│  │    - CPU 使用率曲线                                              │   │
│  │    - 内存使用曲线                                               │   │
│  │    - 推理时间分布                                               │   │
│  │                                                                 │   │
│  │  错误日志 (error.log):                                          │   │
│  │    - 所有 ERROR 及以上级别日志                                   │   │
│  │    - 异常堆栈信息                                               │   │
│  │    - 恢复操作记录                                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  日志轮转：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  - 单个文件最大：100MB                                          │   │
│  │  - 保留文件数：7 个                                              │   │
│  │  - 压缩旧日志：.gz 格式                                          │   │
│  │  - 自动清理：超过 7 天的日志                                      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.6 性能基准测试

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      性能基准测试方案                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  测试场景：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  场景 1: 单路 RTSP 流 + YOLOv8n                                    │   │
│  │    - 预期 FPS: ≥60                                              │   │
│  │    - 预期延迟：<50ms                                            │   │
│  │                                                                 │   │
│  │  场景 2: 单路 RTSP 流 + YOLO11n                                    │   │
│  │    - 预期 FPS: ≥70                                              │   │
│  │    - 预期延迟：<40ms                                            │   │
│  │                                                                 │   │
│  │  场景 3: 多路并发 (4 路)                                           │   │
│  │    - 预期 FPS: ≥25/路                                           │   │
│  │    - 预期延迟：<80ms                                            │   │
│  │                                                                 │   │
│  │  场景 4: 长时间稳定性 (24 小时)                                    │   │
│  │    - 无崩溃、无内存泄漏                                         │   │
│  │    - FPS 波动 <10%                                               │   │
│  │    - 显存增长 <100MB                                            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  测试工具：                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  - pytest: 单元测试                                             │   │
│  │  - pytest-benchmark: 性能基准测试                               │   │
│  │  - memory_profiler: 内存分析                                    │   │
│  │  - nvidia-smi: GPU 监控                                         │   │
│  │  - 自定义监控脚本：实时指标采集                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. 数据流设计

### 5.1 端到端数据流

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         端到端数据流                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  数据源        解码层         存储层         处理层         输出层       │
│    │            │              │              │              │          │
│    ↓            ↓              ↓              ↓              ↓          │
│  ┌────┐      ┌────┐         ┌────┐         ┌────┐         ┌────┐      │
│  │RTSP│─────→│FFmpeg│─────→│共享内存│─────→│预处理│─────→│结果│      │
│  │ 流 │      │解码  │         │YUV420p│         │YUV→RGB│         │输出│      │
│  └────┘      └────┘         └────┘         └────┘         └────┘      │
│    │            │              │              │              │          │
│    │            │              │              │              │          │
│  原始 H.264   YUV 原始数据   614KB 内存     (1,3,640,640)   JSON/Dict  │
│  编码流       (640×640)      平面布局       float32 张量    检测框     │
│                                                                         │
│  时间线 ─────────────────────────────────────────────────────────→     │
│    │            │              │              │              │          │
│    t0          t0+5ms        t0+6ms        t0+8ms        t0+15ms       │
│                                                                         │
│  累计延迟：~15-30ms (不含网络传输)                                      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 时序图

```
解码器进程                    共享内存                    检测器进程
    │                           │                           │
    │─── 创建共享内存 ──────────→│                           │
    │                           │                           │
    │─── 读取 RTSP 帧 ──────────→│                           │
    │                           │                           │
    │─── 解码为 YUV ────────────→│                           │
    │                           │                           │
    │─── 写入共享内存 ──────────→│                           │
    │                           │                           │
    │                           │◄──────── 读取共享内存 ────│
    │                           │                           │
    │                           │◄──────── 预处理 ──────────│
    │                           │                           │
    │                           │◄──────── YOLO 推理 ───────│
    │                           │                           │
    │                           │◄──────── 输出结果 ────────│
    │                           │                           │
    │─── 写入下一帧 ────────────→│                           │
    │                           │◄──────── 循环检测 ────────│
    │                           │                           │
```

### 5.3 帧同步机制

```
┌─────────────────────────────────────────────────────────────────┐
│                      帧同步机制设计                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  方案 1: 信号量同步                                               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  解码器：write_frame() → 释放信号量 → 通知检测器          │   │
│  │  检测器：等待信号量 → read_frame() → 处理                │   │
│  │  优点：精确同步，无轮询                                   │   │
│  │  缺点：实现复杂度较高                                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  方案 2: 时间戳 + 轮询                                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  解码器：写入帧 + 时间戳                                  │   │
│  │  检测器：检查时间戳变化 → 有新帧则处理                    │   │
│  │  优点：实现简单                                          │   │
│  │  缺点：存在轮询开销                                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  方案 3: 固定帧率                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  解码器：按固定帧率写入                                   │   │
│  │  检测器：按固定帧率读取                                   │   │
│  │  优点：简单可靠，资源可预测                               │   │
│  │  缺点：灵活性较差                                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  推荐：方案 3 (初期) → 方案 1 (性能优化)                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 性能优化方案

### 6.1 优化目标

| 指标 | 目标值 | 测量方法 |
|------|--------|---------|
| **端到端延迟** | < 100ms | 时间戳差值 |
| **检测 FPS** | ≥ 30 | 帧计数/时间 |
| **GPU 利用率** | 50-80% | nvidia-smi |
| **内存占用** | < 500MB | 任务管理器 |
| **CPU 占用** | < 50% | 任务管理器 |

### 6.2 优化策略矩阵

```
┌─────────────────────────────────────────────────────────────────┐
│                        性能优化策略矩阵                          │
├───────────────┬─────────────────────────────────────────────────┤
│               │                                                 │
│  解码优化     │  • 硬件加速 (DXVA2/CUDA)                        │
│               │  • 零拷贝输出                                   │
│               │  • 丢帧策略                                     │
│               │                                                 │
│  传输优化     │  • 共享内存 (零拷贝)                            │
│               │  • 内存对齐                                     │
│               │  • 环形缓冲区                                   │
│               │                                                 │
│  预处理优化   │  • 向量化操作 (NumPy)                           │
│               │  • 原地转换                                     │
│               │  • 缓存复用                                     │
│               │                                                 │
│  推理优化     │  • CUDA 加速                                       │
│               │  • GPU 内存优化                                    │
│               │  • 批处理推理                                     │
│               │                                                 │
│  系统优化     │  • 进程优先级                                   │
│               │  • CPU 亲和性                                    │
│               │  • GPU 流优化                                    │
│               │                                                 │
└───────────────┴─────────────────────────────────────────────────┘
```

### 6.3 ONNX Runtime-GPU 优化

#### 6.3.1 GPU 配置优化

```python
# 启用 GPU 优化配置
session_options = ort.SessionOptions()
session_options.enable_mem_pattern = True      # 启用内存模式优化
session_options.enable_cpu_mem_arena = False   # 禁用 CPU 内存 arena
session_options.graph_optimization_level = \
    ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED

# CUDA Provider 配置
cuda_provider_options = {
    'device_id': 0,              # GPU 设备 ID
    'gpu_mem_limit': 2*1024*1024*1024,  # GPU 内存限制 (2GB)
    'arena_extend_strategy': 'kSameAsRequested',
    'cudnn_conv_algo_search': 'EXHAUSTIVE',  # cuDNN 卷积算法搜索
    'do_copy_in_default_stream': True,
}

session = InferenceSession(
    model_path, 
    sess_options=session_options,
    providers=[('CUDAExecutionProvider', cuda_provider_options), 
               'CPUExecutionProvider']
)
```

#### 6.3.2 性能对比

| 配置 | 延迟 | 吞吐量 | 显存占用 | 推荐场景 |
|------|------|--------|---------|---------|
| **FP32** | 基准 | 基准 | 基准 | 精度敏感 |
| **FP16** | -30% | +60% | -40% | 通用场景 (需模型支持) |


### 6.4 流水线并行

```
┌─────────────────────────────────────────────────────────────────┐
│                      流水线并行设计                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  时间片                                                         │
│    │                                                            │
│  T1│  解码帧 1                                                   │
│    │                                                            │
│  T2│  解码帧 2    →→→→  预处理帧 1                                │
│    │                                                            │
│  T3│  解码帧 3    →→→→  预处理帧 2    →→→→  推理帧 1              │
│    │                                                            │
│  T4│  解码帧 4    →→→→  预处理帧 3    →→→→  推理帧 2              │
│    │                                                            │
│  T5│  解码帧 5    →→→→  预处理帧 4    →→→→  推理帧 3              │
│    │                                                            │
│  理论加速比：3x (三阶段流水线)                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 6.5 内存池设计

```python
class MemoryPool:
    """内存池 - 减少内存分配开销"""
    
    def __init__(self, buffer_size=3):
        self.buffers = [
            np.empty((640, 640), dtype=np.uint8)
            for _ in range(buffer_size)
        ]
        self.available = list(range(buffer_size))
    
    def acquire(self) -> np.ndarray:
        """获取缓冲区"""
        if not self.available:
            return np.empty((640, 640), dtype=np.uint8)
        idx = self.available.pop()
        return self.buffers[idx]
    
    def release(self, buffer: np.ndarray) -> None:
        """释放缓冲区"""
        # 找到索引并加入可用列表
        for i, buf in enumerate(self.buffers):
            if buf is buffer:
                self.available.append(i)
                break
```

---

## 7. 测试方案

### 7.1 测试类型

| 测试类型 | 测试内容 | 工具 |
|---------|---------|------|
| **单元测试** | 各模块功能验证 | pytest |
| **集成测试** | 模块间交互验证 | pytest |
| **性能测试** | 延迟/FPS/资源 | 自定义脚本 |
| **压力测试** | 长时间运行稳定性 | 自定义脚本 |
| **兼容性测试** | 不同模型/硬件 | 多环境测试 |

### 7.2 性能测试脚本

```python
# tests/benchmark.py

def benchmark_latency(detector, num_iterations=100):
    """
    延迟测试
    
    指标:
        - 平均延迟
        - 最小延迟
        - 最大延迟
        - 延迟标准差
    """
    latencies = []
    
    for _ in range(num_iterations):
        start = time.perf_counter()
        detector.infer()
        end = time.perf_counter()
        latencies.append(end - start)
    
    return {
        'mean_ms': np.mean(latencies) * 1000,
        'std_ms': np.std(latencies),
        'min_ms': np.min(latencies) * 1000,
        'max_ms': np.max(latencies) * 1000,
        'p95_ms': np.percentile(latencies, 95) * 1000
    }


def benchmark_fps(detector, duration=10):
    """
    FPS 测试
    
    指标:
        - 平均 FPS
        - 帧总数
    """
    start = time.time()
    frame_count = 0
    
    while time.time() - start < duration:
        detector.infer()
        frame_count += 1
    
    elapsed = time.time() - start
    return {
        'fps': frame_count / elapsed,
        'total_frames': frame_count,
        'duration_s': elapsed
    }


def benchmark_resource_usage(duration=60):
    """
    资源占用测试
    
    指标:
        - CPU 使用率
        - GPU 使用率
        - 内存占用
    """
    import psutil
    import pynvml
    
    process = psutil.Process()
    
    # 记录资源使用情况
    cpu_usages = []
    gpu_usages = []
    memory_usages = []
    
    for _ in range(duration):
        cpu_usages.append(process.cpu_percent())
        memory_usages.append(process.memory_info().rss / 1024 / 1024)
        
        # GPU 使用率 (NVIDIA)
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        gpu_util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
        gpu_usages.append(gpu_util)
        
        time.sleep(1)
    
    return {
        'cpu_avg': np.mean(cpu_usages),
        'gpu_avg': np.mean(gpu_usages),
        'memory_avg_mb': np.mean(memory_usages),
        'memory_peak_mb': np.max(memory_usages)
    }
```

### 7.3 测试用例

```python
# tests/test_modules.py

class TestSharedMemory:
    """共享内存模块测试"""
    
    def test_create_and_open(self):
        """测试创建和打开共享内存"""
        shm = SharedMemoryManager("test_shm")
        shm.create()
        assert shm.is_owner == True
        
        shm2 = SharedMemoryManager("test_shm")
        assert shm2.open() == True
        
        shm.close()
        shm2.close()
    
    def test_write_and_read(self):
        """测试写入和读取"""
        shm = SharedMemoryManager("test_shm")
        shm.create()
        
        # 准备测试数据
        y = np.ones((640, 640), dtype=np.uint8) * 100
        u = np.ones((320, 320), dtype=np.uint8) * 150
        v = np.ones((320, 320), dtype=np.uint8) * 200
        
        # 写入
        shm.write_frame(y, u, v)
        
        # 读取
        y_read, u_read, v_read = shm.read_frame()
        
        # 验证
        assert np.array_equal(y, y_read)
        assert np.array_equal(u, u_read)
        assert np.array_equal(v, v_read)
        
        shm.close()


class TestPreprocessor:
    """预处理模块测试"""
    
    def test_yuv_to_rgb(self):
        """测试 YUV 转 RGB"""
        preprocessor = DetectionPreprocessor()
        
        # 创建测试 YUV 数据
        y = np.zeros((640, 640), dtype=np.uint8)
        u = np.zeros((320, 320), dtype=np.uint8)
        v = np.zeros((320, 320), dtype=np.uint8)
        
        # 转换
        rgb = preprocessor.yuv420p_to_rgb(y, u, v)
        
        # 验证输出形状
        assert rgb.shape == (640, 640, 3)
        assert rgb.dtype == np.uint8
    
    def test_normalize_and_transform(self):
        """测试归一化和格式转换"""
        preprocessor = DetectionPreprocessor()
        
        # 创建测试 RGB 图像
        rgb = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
        
        # 转换
        output = preprocessor.normalize_and_transform(rgb)
        
        # 验证
        assert output.shape == (1, 3, 640, 640)
        assert output.dtype == np.float32
        assert output.min() >= 0.0
        assert output.max() <= 1.0


class TestDetector:
    """检测器模块测试"""
    
    def test_model_loading(self):
        """测试模型加载"""
        detector = YOLODetector(
            model_path="models/yolov8n.onnx",
            model_version="v8n"
        )
        assert detector.session is not None
    
    def test_inference(self):
        """测试推理"""
        detector = YOLODetector(
            model_path="models/yolov8n.onnx",
            model_version="v8n"
        )
        
        # 执行推理
        detections = detector.infer()
        
        # 验证输出格式
        assert isinstance(detections, list)
        for det in detections:
            assert 'bbox' in det
            assert 'confidence' in det
            assert 'class_id' in det
```

### 7.4 测试报告模板

```markdown
# 性能测试报告

## 测试环境

- **CPU**: Intel Core i7-12700K
- **GPU**: NVIDIA RTX 3080
- **内存**: 32GB DDR4
- **系统**: Windows 11

## 测试结果

### 延迟测试

| 模型 | 平均延迟 | P95 延迟 | 最大延迟 |
|------|---------|---------|---------|
| YOLOv8n (FP32) | 15.2ms | 18.5ms | 25.3ms |
| YOLOv8n (FP16) | 9.8ms | 12.1ms | 18.7ms |
| YOLO11n (FP32) | 13.5ms | 16.2ms | 22.1ms |
| YOLO11n (FP16) | 8.6ms | 10.9ms | 16.5ms |

### FPS 测试

| 模型 | 平均 FPS | 最低 FPS | 帧总数 |
|------|---------|---------|--------|
| YOLOv8n (FP32) | 62 FPS | 58 FPS | 620 |
| YOLOv8n (FP16) | 95 FPS | 88 FPS | 950 |
| YOLO11n (FP32) | 70 FPS | 65 FPS | 700 |
| YOLO11n (FP16) | 108 FPS | 98 FPS | 1080 |

### 资源占用

| 指标 | YOLOv8n | YOLO11n |
|------|---------|---------|
| GPU 使用率 | 65% | 58% |
| CPU 使用率 | 25% | 22% |
| 内存占用 | 380MB | 350MB |
| 显存占用 | 1.2GB | 1.0GB |

## 结论

- YOLO11n 相比 YOLOv8n 性能提升约 12%
- FP16 量化可提升 50%+ 推理速度
- 系统满足 30 FPS 实时检测需求
```

---

## 8. 部署方案

### 8.1 部署架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        部署架构                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐     │
│  │  开发环境   │  →   │  测试环境   │  →   │  生产环境   │     │
│  │             │      │             │      │             │     │
│  │  - Windows  │      │  - Windows  │      │  - Windows  │     │
│  │  - Python   │      │  - Python   │      │  - Python   │     │
│  │  - 完整依赖 │      │  - 完整依赖 │      │  - 精简依赖 │     │
│  └─────────────┘      └─────────────┘      └─────────────┘     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 环境要求

#### 8.2.1 最低配置

| 组件 | 要求 |
|------|------|
| **CPU** | 4 核 2.5GHz+ |
| **内存** | 8GB |
| **GPU** | GTX 1060 6GB+ |
| **存储** | 10GB 可用空间 |
| **网络** | 100Mbps |

#### 8.2.2 推荐配置

| 组件 | 要求 |
|------|------|
| **CPU** | 8 核 3.0GHz+ (i7/R7) |
| **内存** | 16GB+ |
| **GPU** | RTX 3060 12GB+ |
| **存储** | 50GB SSD |
| **网络** | 1Gbps |

### 8.3 安装步骤

#### 8.3.1 基础环境安装

```powershell
# 1. 安装 Python 3.9+
# 下载地址：https://www.python.org/downloads/

# 2. 安装 FFmpeg (带硬件加速支持)
choco install ffmpeg

# 3. 安装 CUDA (如需 GPU 加速)
# 下载地址：https://developer.nvidia.com/cuda-downloads

# 4. 安装 cuDNN
# 下载地址：https://developer.nvidia.com/cudnn
```

#### 8.3.2 项目安装

```powershell
# 1. 克隆/复制项目
cd e:\sagesight-AI\yolo_detection_channel

# 2. 创建虚拟环境
python -m venv venv
.\venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 下载模型
# YOLOv8n
yolo export model=yolov8n.pt format=onnx imgsz=640
# 或手动下载后放入 models/ 目录

# 5. 验证安装
python src/main.py --help
```

#### 8.3.3 配置文件

```yaml
# config/config.yaml
decoder:
  rtsp_transport: tcp
  hwaccel: dxva2
  width: 640
  height: 640

detector:
  model_path: "models/yolov8n.onnx"
  model_version: "v8n"
  use_gpu: true

performance:
  target_fps: 30
```

### 8.4 运行方式

#### 8.4.1 命令行运行

```powershell
# 基本运行
python src/main.py \
  --rtsp "rtsp://admin:password@192.168.1.100:554/stream" \
  --model models/yolov8n.onnx \
  --version v8n

# 使用 GPU 加速
python src/main.py \
  --rtsp "rtsp://..." \
  --model models/yolov8n.onnx \
  --version v8n \
  --gpu

# 指定帧率
python src/main.py \
  --rtsp "rtsp://..." \
  --model models/yolov8n.onnx \
  --fps 30
```

#### 8.4.2 作为服务运行

```powershell
# 使用 NSSM 创建 Windows 服务
nssm install YoloDetectionChannel

# 配置服务参数
nssm set YoloDetectionChannel Application "e:\sagesight-AI\yolo_detection_channel\venv\Scripts\python.exe"
nssm set YoloDetectionChannel AppParameters "src/main.py --rtsp rtsp://... --model models/yolov8n.onnx"
nssm set YoloDetectionChannel AppDirectory "e:\sagesight-AI\yolo_detection_channel"
nssm set YoloDetectionChannel DisplayName "YOLO 检测通道服务"
nssm set YoloDetectionChannel StartService SERVICE_AUTO_START

# 启动服务
nssm start YoloDetectionChannel
```

### 8.5 日志配置

```yaml
# logging_config.yaml
version: 1
disable_existing_loggers: False

formatters:
  standard:
    format: '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
  detailed:
    format: '%(asctime)s [%(levelname)s] %(name)s [%(filename)s:%(lineno)d]: %(message)s'

handlers:
  console:
    class: logging.StreamHandler
    level: INFO
    formatter: standard
    stream: ext://sys.stdout
  
  file:
    class: logging.handlers.RotatingFileHandler
    level: DEBUG
    formatter: detailed
    filename: logs/detection.log
    maxBytes: 104857600  # 100MB
    backupCount: 7
    encoding: utf8

root:
  level: INFO
  handlers: [console, file]
```

### 8.6 监控方案

```python
# monitoring.py
import psutil
import pynvml
from prometheus_client import start_http_server, Gauge, Counter

# 定义指标
DETECTION_FPS = Gauge('detection_fps', 'Detection FPS')
DETECTION_LATENCY = Gauge('detection_latency_ms', 'Detection latency (ms)')
GPU_USAGE = Gauge('gpu_usage_percent', 'GPU usage (%)')
MEMORY_USAGE = Gauge('memory_usage_mb', 'Memory usage (MB)')
DETECTION_COUNT = Counter('detection_count', 'Total detections', ['class'])

def start_monitoring():
    """启动监控服务器"""
    start_http_server(8000)
    
    while True:
        # 收集指标
        process = psutil.Process()
        MEMORY_USAGE.set(process.memory_info().rss / 1024 / 1024)
        
        # GPU 指标
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        gpu_util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
        GPU_USAGE.set(gpu_util)
        
        time.sleep(1)
```

---

## 9. 风险评估

### 9.1 技术风险

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| **硬件加速不兼容** | 中 | 高 | 提供 CPU 回退方案 |
| **共享内存冲突** | 低 | 高 | 使用唯一名称+信号量 |
| **模型精度下降** | 中 | 中 | FP16 验证+回退 |
| **RTSP 流不稳定** | 高 | 中 | 自动重连+丢帧 |
| **内存泄漏** | 低 | 高 | 定期监控+自动重启 |

### 9.2 性能风险

| 风险 | 触发条件 | 检测方式 | 应对措施 |
|------|---------|---------|---------|
| **FPS 下降** | 多路并发 | 监控 FPS | 动态降分辨率 |
| **延迟增加** | 网络抖动 | 监控延迟 | 丢帧策略 |
| **显存溢出** | 模型过大 | 监控显存 | 模型卸载 |
| **CPU 瓶颈** | 预处理过重 | 监控 CPU | 多线程优化 |

### 9.3 运维风险

| 风险 | 预防措施 | 恢复方案 |
|------|---------|---------|
| **进程崩溃** | 看门狗监控 | 自动重启 |
| **磁盘满** | 日志轮转 | 清理旧日志 |
| **配置错误** | 配置验证 | 回滚配置 |
| **模型损坏** | 校验和验证 | 备份模型 |

---

## 10. 开发计划

### 10.1 里程碑

```
┌─────────────────────────────────────────────────────────────────┐
│                        项目开发路线图                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  第 1 周          第 2 周          第 3 周          第 4 周          │
│   │              │              │              │               │
│   ▼              ▼              ▼              ▼               │
│  ┌────────┐    ┌────────┐    ┌────────┐    ┌────────┐        │
│  │ 基础   │    │ 核心   │    │ 优化   │    │ 测试   │        │
│  │ 架构   │    │ 功能   │    │ 调优   │    │ 部署   │        │
│  └────────┘    └────────┘    └────────┘    └────────┘        │
│   │              │              │              │               │
│   ├─ 共享内存    ├─ 解码器      ├─ GPU 优化      ├─ 单元测试      │
│   ├─ 解码器框架  ├─ 预处理器    ├─ 流水线      ├─ 性能测试      │
│   ├─ 配置系统    ├─ 检测器      ├─ 内存池      ├─ 压力测试      │
│                  ├─ 主流程      ├─ 日志系统    ├─ 文档完善      │
│                                                                 │
│  M1: 架构完成   M2: 功能完成   M3: 性能达标   M4: 发布 v1.0     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 10.2 任务分解

#### 阶段 1: 基础架构 (第 1 周)

| 任务 | 工时 | 交付物 |
|------|------|--------|
| 项目结构搭建 | 4h | 目录结构 |
| 共享内存模块 | 8h | shared_memory.py |
| 配置系统 | 4h | config.yaml + 加载器 |
| 日志系统 | 4h | logging_config.py |

#### 阶段 2: 核心功能 (第 2 周)

| 任务 | 工时 | 交付物 |
|------|------|--------|
| FFmpeg 解码器 | 12h | video_decoder.py |
| 预处理模块 | 8h | preprocessor.py |
| YOLO 检测器 | 12h | detector.py |
| 主流程集成 | 8h | main.py |

#### 阶段 3: 优化调优 (第 3 周)

| 任务 | 工时 | 交付物 |
|------|------|--------|
| GPU 优化配置 | 12h | ONNX Runtime-GPU 优化 |
| 流水线优化 | 8h | 并行处理 |
| 内存池实现 | 4h | MemoryPool |
| 性能监控 | 4h | monitoring.py |

#### 阶段 4: 测试部署 (第 4 周)

| 任务 | 工时 | 交付物 |
|------|------|--------|
| 单元测试 | 8h | test_*.py |
| 性能测试 | 8h | benchmark.py |
| 压力测试 | 4h | 测试报告 |
| 部署文档 | 4h | README.md |

### 10.3 验收标准

#### 功能验收

- [ ] 支持 YOLOv8n 和 YOLO11n 模型
- [ ] 支持 RTSP 流解码
- [ ] 共享内存正常读写
- [ ] 检测框输出正确

#### 性能验收

- [ ] 端到端延迟 < 100ms
- [ ] 检测 FPS ≥ 30
- [ ] GPU 利用率 50-80%
- [ ] 内存占用 < 500MB

#### 稳定性验收

- [ ] 连续运行 24 小时无崩溃
- [ ] RTSP 断流自动重连
- [ ] 内存无泄漏
- [ ] 日志完整可追溯

---

## 附录

### 附录 A: 缩略语

| 缩写 | 全称 | 说明 |
|------|------|------|
| YUV | YUV 色彩空间 | 亮度和色度分离的视频编码 |
| YUV420p | YUV 4:2:0 Planar | YUV 的一种存储格式 |
| RTSP | Real Time Streaming Protocol | 实时流传输协议 |
| DXVA2 | DirectX Video Acceleration 2.0 | Windows 视频加速 API |
| QSV | Quick Sync Video | Intel 快速同步视频 |
| CUDA | Compute Unified Device Architecture | NVIDIA 并行计算平台 |
| ONNX | Open Neural Network Exchange | 开放神经网络交换格式 |
| ONNX Runtime-GPU | ONNX Runtime GPU Acceleration | ONNX Runtime GPU 加速版本 |
| NMS | Non-Maximum Suppression | 非极大值抑制 |
| FPS | Frames Per Second | 每秒帧数 |

### 附录 B: 参考资源

1. **YOLO 官方文档**
   - YOLOv8: https://docs.ultralytics.com/
   - YOLO11: https://github.com/ultralytics/ultralytics

2. **ONNX Runtime**
   - 官方文档：https://onnxruntime.ai/
   - GPU 加速：https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html
   - 性能优化：https://onnxruntime.ai/docs/performance/

3. **CUDA**
   - 官方文档：https://docs.nvidia.com/cuda/
   - cuDNN: https://docs.nvidia.com/deeplearning/cudnn/

4. **FFmpeg**
   - 官方文档：https://ffmpeg.org/documentation.html
   - 硬件加速：https://trac.ffmpeg.org/wiki/HWAccelIntro

5. **共享内存**
   - Python mmap: https://docs.python.org/3/library/mmap.html

### 附录 C: 版本历史

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|---------|
| v1.0 | 2026-06-09 | - | 初始版本，完整设计方案 |

---

**文档结束**
