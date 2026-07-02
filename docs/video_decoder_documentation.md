# 视频解码器文档

## 1. 概述

视频解码器模块负责从 RTSP 流或视频文件中解码视频帧，使用 FFmpeg 硬件加速解码，输出 YUV420p/NV12 格式到共享内存，供后续检测器使用。

### 核心特性

| 特性 | 说明 |
|------|------|
| **硬件加速** | 支持 DXVA2（Windows）、CUDA（NVIDIA）、QSV（Intel） |
| **零拷贝传输** | 使用共享内存，避免数据拷贝开销 |
| **帧队列缓存** | 缓存多帧数据，防止丢帧 |
| **自动重连** | RTSP 流断线自动重连 |
| **性能监控** | 实时统计解码 FPS、丢帧数、延迟等 |

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    视频解码架构                               │
└─────────────────────────────────────────────────────────────┘

视频源（RTSP/文件）
    ↓
FFmpeg 解复用
    ↓
硬件解码（DXVA2/CUDA/QSV）
    ↓
YUV420p/NV12 格式
    ↓
帧队列缓存（FrameQueue）
    ↓
共享内存（SharedMemory）
    ↓
检测器进程（读取端）
```

### 2.2 数据流

```
┌─────────────────┐         ┌─────────────────┐
│   解码进程      │         │   推理进程      │
│  (FFmpeg)       │ ──────→ │   (GPU 推理)    │
│  CPU 核 1-4     │  Queue  │  CPU 核 5-6 + GPU│
└─────────────────┘         └─────────────────┘
      ↓                            ↓
  共享内存                    结果输出
  (零拷贝)
```

---

## 3. 核心模块

### 3.1 VideoDecoder

**位置**：`src/decoders/video_decoder.py`

**功能**：
- 使用 FFmpeg 子进程进行硬件加速解码
- 支持 RTSP 流和视频文件输入
- 输出 YUV420p/NV12 格式到共享内存
- 自动重连机制

**关键参数**：

```python
@dataclass
class DecoderConfig:
    rtsp_url: str = ""          # RTSP 流地址
    video_file: str = ""        # 视频文件路径
    width: int = 640            # 输出宽度
    height: int = 640           # 输出高度
    hwaccel: str = "dxva2"      # 硬件加速方式
    rtsp_transport: str = "tcp" # RTSP 传输协议
    reconnect: bool = True      # 自动重连
    reconnect_delay: int = 5    # 重连延迟（秒）
    buffer_size: int = 1024*1024 # 输入缓冲区大小
    frame_queue_size: int = 5   # 帧队列大小
```

### 3.2 SharedMemoryManager

**位置**：`src/utils/shared_memory.py`

**功能**：
- 基于 mmap 的共享内存管理
- 零拷贝数据传输
- 版本检查机制（避免读到半帧数据）

**共享内存布局**：

```
┌───────────────────────────────────────────────────────────┐
│  偏移量   大小     内容            说明                    │
├───────────────────────────────────────────────────────────┤
│  0        4 字节    sequence_num   帧序列号 (从 0 开始递增)   │
│  4        8 字节    timestamp_ns   时间戳 (纳秒)            │
│  12       8 字节    write_time_ns  写入完成时间 (纳秒)      │
│  20       614,400 字节 YUV 数据     YUV420p (640×640)       │
│  总计：614,420 字节                                       │
└───────────────────────────────────────────────────────────┘
```

### 3.3 FrameQueue

**位置**：`src/utils/frame_queue.py`

**功能**：
- 缓存多帧 YUV 数据
- 线程安全的 FIFO 队列
- 自动丢弃最旧的帧（队列满时）
- 性能统计监控

**队列特性**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_size` | 5 | 队列最大容量（帧数） |
| `dropped_frames` | 0 | 丢弃的帧数统计 |
| `avg_queue_delay_ms` | 0 | 平均队列延迟 |

---

## 4. 硬件加速

### 4.1 支持的硬件加速方式

| 方式 | 平台 | 适用场景 | 性能 |
|------|------|----------|------|
| **DXVA2** | Windows | RTX 2070（推荐） | ⭐⭐⭐⭐⭐ |
| **CUDA** | NVIDIA GPU | 需要特殊配置 | ⭐⭐⭐⭐ |
| **QSV** | Intel 核显 | Intel CPU | ⭐⭐⭐ |
| **None** | CPU | 无 GPU 环境 | ⭐⭐ |

### 4.2 DXVA2（推荐）

**优势**：
- Windows 通用硬件加速
- RTX 2070 已验证可用
- 输出 YUV420p 格式（兼容性好）

**FFmpeg 参数**：

```bash
ffmpeg -hwaccel dxva2 -i rtsp://... -pix_fmt yuv420p -s 640x640 -f rawvideo pipe:1
```

### 4.3 CUDA（需要特殊配置）

**注意事项**：
- CUDA 解码 2K 视频会失败（表面数量超限）
- 需要使用 NV12 格式而非 YUV420p
- 需要限制线程数和硬件帧数量

**FFmpeg 参数**：

```bash
ffmpeg -hwaccel cuda -hwaccel_output_format nv12 -extra_hw_frames 16 -threads 16 \
       -i rtsp://... -pix_fmt nv12 -s 640x640 -f rawvideo pipe:1
```

### 4.4 性能对比

| 硬件加速 | 解码 FPS | CPU 占用 | GPU 占用 | 延迟 |
|----------|----------|----------|----------|------|
| **DXVA2** | 25-30 | 5-10% | 10-20% | 40ms |
| **CUDA** | 30-35 | 3-5% | 15-25% | 35ms |
| **QSV** | 20-25 | 8-12% | 5-10% | 50ms |
| **CPU** | 10-15 | 80-90% | 0% | 100ms |

---

## 5. 配置文件

### 5.1 配置文件位置

**路径**：`config/decoder_config.yaml`

### 5.2 配置示例

```yaml
# 视频解码器配置文件
decoder:
  # 视频源配置
  # 选项 1: RTSP 流
  #rtsp_url: "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/1"
  
  # 选项 2: 视频文件
  video_file: "C:/Users/Administrator/Desktop/test_1.mp4"
  
  # 输出分辨率
  width: 640
  height: 640
  
  # 硬件加速方式
  # 可选值：dxva2, cuda, qsv, none
  hwaccel: "dxva2"
  
  # RTSP 传输协议
  rtsp_transport: "tcp"
  
  # 自动重连配置
  reconnect: true
  reconnect_delay: 5
  
  # 缓冲区配置
  buffer_size: 1048576  # 1MB
  frame_queue_size: 30

# 共享内存配置
shared_memory:
  name: "yolo_detection_channel"
  create: true

# 日志配置
logging:
  level: "INFO"
  file: "logs/decoder.log"
  console: true

# 性能监控配置
monitoring:
  enabled: true
  stats_interval: 5
  gpu_monitoring: true
```

---

## 6. 使用方法

### 6.1 基本使用

```python
from src.decoders import VideoDecoder, DecoderConfig

# 创建解码器配置
config = DecoderConfig(
    rtsp_url="rtsp://admin:password@192.168.1.100:554/Streaming/Channels/1",
    width=640,
    height=640,
    hwaccel="dxva2",
    reconnect=True
)

# 创建解码器
decoder = VideoDecoder(config)

# 启动解码
decoder.start()

# 获取统计信息
stats = decoder.get_stats()
print(f"解码帧数: {stats['frames_decoded']}")
print(f"平均 FPS: {stats['fps']}")

# 停止解码
decoder.stop()
```

### 6.2 视频文件解码

```python
from src.decoders import VideoDecoder, DecoderConfig

# 创建解码器配置（视频文件）
config = DecoderConfig(
    video_file="C:/Users/Administrator/Desktop/test.mp4",
    width=640,
    height=640,
    hwaccel="dxva2",
    frame_queue_size=30  # 视频文件需要更大的队列
)

# 创建解码器
decoder = VideoDecoder(config)
decoder.start()
```

### 6.3 多进程架构使用

```python
import multiprocessing as mp

# 在主进程中创建解码器
decoder_proc = mp.Process(
    target=decoder_worker,
    args=(config, frame_queue, running),
    daemon=True
)
decoder_proc.start()

# 在推理进程中读取共享内存
shm = open_shared_memory(config['shm_name'])
header, y, u, v = shm.read_frame()
```

---

## 7. 性能监控

### 7.1 统计信息

```python
stats = decoder.get_stats()

# 基本统计
print(f"解码帧数: {stats['frames_decoded']}")
print(f"解码错误: {stats['decode_errors']}")
print(f"重连次数: {stats['reconnect_count']}")
print(f"平均 FPS: {stats['fps']}")

# 帧队列统计
queue_stats = stats['frame_queue']
print(f"队列大小: {queue_stats['size']}/{queue_stats['max_size']}")
print(f"丢帧数: {queue_stats['dropped_frames']}")
print(f"平均延迟: {queue_stats['avg_delay_ms']}ms")
```

### 7.2 性能指标

| 指标 | 说明 | 目标值 |
|------|------|--------|
| **解码 FPS** | 每秒解码帧数 | ≥ 25 |
| **解码延迟** | 单帧解码耗时 | ≤ 40ms |
| **队列延迟** | 帧在队列中的等待时间 | ≤ 20ms |
| **丢帧率** | 队列满时丢弃的帧比例 | ≤ 1% |
| **CPU 占用** | 解码进程 CPU 使用率 | ≤ 10% |
| **GPU 占用** | 硬件解码 GPU 使用率 | ≤ 20% |

---

## 8. 故障处理

### 8.1 RTSP 流断线重连

**机制**：
- FFmpeg 进程退出后自动重启
- 重连延迟可配置（默认 5 秒）
- 重连次数统计

**日志示例**：

```
[VideoDecoder] FFmpeg 进程退出，返回码=-1
[VideoDecoder] 5秒后重连...
[VideoDecoder] FFmpeg 进程已重启
```

### 8.2 解码错误处理

**常见错误**：

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `FFmpeg 未找到` | FFmpeg 未安装 | 安装 FFmpeg 并添加到 PATH |
| `RTSP 连接失败` | 网络问题或认证失败 | 检查网络和认证信息 |
| `CUDA 表面超限` | CUDA 解码 2K 视频 | 使用 DXVA2 或降低分辨率 |
| `解码格式错误` | 硬件加速不支持 | 更换硬件加速方式 |

### 8.3 性能优化建议

**视频文件全速解码问题**：
- 视频文件会按原始帧率解码（可能 30 FPS）
- 检测器处理速度跟不上会导致丢帧
- **解决方案**：增大帧队列大小（`frame_queue_size: 30`）

**RTSP 流延迟问题**：
- RTSP 流可能有网络延迟
- **解决方案**：使用 TCP 传输协议（`rtsp_transport: "tcp"`）

---

## 9. 最佳实践

### 9.1 硬件加速选择

| 环境 | 推荐 | 原因 |
|------|------|------|
| **Windows + NVIDIA GPU** | DXVA2 | 稳定可靠，兼容性好 |
| **Linux + NVIDIA GPU** | CUDA | 性能最优 |
| **Intel CPU** | QSV | 利用核显加速 |
| **无 GPU** | None | CPU 解码 |

### 9.2 队列大小配置

| 场景 | 推荐值 | 原因 |
|------|--------|------|
| **RTSP 流** | 5-10 | 实时性要求高 |
| **视频文件** | 20-30 | 防止全速解码丢帧 |
| **低延迟场景** | 3-5 | 减少队列延迟 |

### 9.3 分辨率配置

| 分辨率 | 解码性能 | 推理性能 | 推荐场景 |
|--------|----------|----------|----------|
| **640×640** | 25-30 FPS | 20-25 FPS | 实时检测（推荐） |
| **960×640** | 20-25 FPS | 15-20 FPS | 高精度检测 |
| **1920×1080** | 10-15 FPS | 5-10 FPS | 高清视频（不推荐） |

---

## 10. 相关文件

### 10.1 核心文件

| 文件 | 说明 |
|------|------|
| `src/decoders/video_decoder.py` | 视频解码器实现 |
| `src/decoders/__init__.py` | 解码器模块导出 |
| `src/utils/shared_memory.py` | 共享内存管理 |
| `src/utils/frame_queue.py` | 帧队列管理 |
| `config/decoder_config.yaml` | 解码器配置文件 |

### 10.2 使用示例

| 文件 | 说明 |
|------|------|
| `run.py` | 统一主程序（Web管理平台 + 任务进程管理） |
| `src/tasks/task_executor.py` | 多任务执行器（一个任务检测多个摄像机） |
| `src/tasks/task_manager.py` | 任务管理器（数据库管理） |

---

## 11. 总结

视频解码器模块是 YOLO 检测系统的核心组件，负责从视频源解码帧数据并传输到检测器。

**核心优势**：
- ✅ 硬件加速解码（DXVA2/CUDA/QSV）
- ✅ 零拷贝传输（共享内存）
- ✅ 帧队列缓存（防止丢帧）
- ✅ 自动重连机制（RTSP 流）
- ✅ 性能监控（实时统计）

**推荐配置**：
- 硬件加速：DXVA2（Windows）
- 分辨率：640×640
- 队列大小：5-10（RTSP） / 20-30（视频文件）
- 传输协议：TCP（RTSP）

**预期性能**：
- 解码 FPS：25-30
- 解码延迟：40ms
- CPU 占用：5-10%
- GPU 占用：10-20%