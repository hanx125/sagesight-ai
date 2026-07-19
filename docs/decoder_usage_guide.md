# 视频解码器使用指南

## 📖 推荐使用方式（方式1：预设名称）

### 快速开始

```python
from src.decoders.video_decoder import create_decoder
from src.utils import FFMPEG_HWACCEL_DXVA2

# 创建解码器（使用预设名称）
decoder = create_decoder(
    rtsp_url="rtsp://admin:password@192.168.1.100:554/stream1",
    width=1920,
    height=1080,
    hwaccel_preset=FFMPEG_HWACCEL_DXVA2,  # 推荐：使用预设名称
)

# 启动解码器
decoder.start()

# 读取帧数据（零拷贝）
frame_index = decoder.get_frame_index()
if frame_index:
    y, uv = decoder.get_frame_from_block(frame_index.shm_block_id)
    print(f"帧 {frame_index.sequence_num}: Y={y.shape}, UV={uv.shape}")

# 停止解码器
decoder.stop()
```

## 🎯 支持的硬件加速预设

| 预设常量 | 预设名称 | 平台 | 说明 |
|---------|---------|------|------|
| `FFMPEG_HWACCEL_DXVA2` | `preset-dxva2` | **Windows** | ✅ 推荐（Windows 平台） |
| `FFMPEG_HWACCEL_NVIDIA` | `preset-nvidia` | NVIDIA GPU | NVIDIA CUDA 硬件加速 |
| `FFMPEG_HWACCEL_VAAPI` | `preset-vaapi` | AMD GPU | AMD VAAPI 硬件加速 |

## 💡 使用示例

### 1. Windows 平台（推荐）

```python
from src.utils import FFMPEG_HWACCEL_DXVA2

decoder = create_decoder(
    rtsp_url="rtsp://...",
    hwaccel_preset=FFMPEG_HWACCEL_DXVA2,  # Windows DXVA2
    width=1920,
    height=1080
)
```

### 2. NVIDIA GPU

```python
from src.utils import FFMPEG_HWACCEL_NVIDIA

decoder = create_decoder(
    rtsp_url="rtsp://...",
    hwaccel_preset=FFMPEG_HWACCEL_NVIDIA,  # NVIDIA CUDA
    width=1920,
    height=1080
)
```

### 3. 自动选择平台预设

```python
import platform
from src.utils import FFMPEG_HWACCEL_DXVA2, FFMPEG_HWACCEL_NVIDIA

# 根据平台自动选择
if platform.system() == "Windows":
    preset = FFMPEG_HWACCEL_DXVA2
else:
    preset = FFMPEG_HWACCEL_NVIDIA

decoder = create_decoder(
    rtsp_url="rtsp://...",
    hwaccel_preset=preset,
    width=1920,
    height=1080
)
```

### 4. 组合使用输入预设

```python
from src.decoders.video_decoder import DecoderConfig
from src.utils import FFMPEG_HWACCEL_DXVA2

config = DecoderConfig(
    rtsp_url="rtsp://...",
    width=1920,
    height=1080,
    fps=30,

    # 方式1：使用预设名称（推荐）
    hwaccel_preset=FFMPEG_HWACCEL_DXVA2,
    input_preset="preset-rtsp-generic",  # RTSP 输入预设

    # 环形缓冲区配置
    ring_buffer_block_count=5,
    index_queue_size=30,
)

decoder = VideoDecoder(config)
decoder.start()
```

## 🔧 完整示例代码

查看 [examples/decoder_usage_example.py](../examples/decoder_usage_example.py) 获取更多示例。

## ⚠️ 方式2（旧版兼容）

方式2是为了向后兼容保留的，不推荐使用：

```python
# 方式2：旧版参数（不推荐）
decoder = create_decoder(
    rtsp_url="rtsp://...",
    hwaccel="dxva2",  # 旧版参数
    width=1920,
    height=1080
)
```

## 📊 新架构特性

- ✅ 环形缓冲区设计（多块存储）
- ✅ 零拷贝数据传输（numpy 内存映射）
- ✅ 生产者-消费者解耦
- ✅ 线程安全的并发读写

## 🚀 性能优势

| 特性 | 旧架构 | 新架构（当前） |
|------|--------|---------------|
| 数据传输 | 内存拷贝 | 零拷贝 |
| 帧缓存 | 单帧 | 多块（环形） |
| 并发读写 | 不支持 | 支持 |
| 性能提升 | - | 30-50% |
