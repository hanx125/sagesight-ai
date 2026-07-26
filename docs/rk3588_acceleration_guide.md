# RK3588 预处理和推理加速指南

## 概述

RK3588是瑞芯微的高性能ARM SoC，具有：
- **双核NPU**：6 TOPS算力（每核3 TOPS）
- **ARM Cortex-A76/A55**：多核CPU
- **Mali-G610 GPU**：支持OpenCL
- **MPP硬件解码**：支持H.264/H.265硬件解码

本项目的代码在RK3588上可以通过以下方式加速：

## 加速方案对比

| 方案 | 预处理位置 | 推理位置 | 性能提升 | CPU占用 | 适用场景 |
|------|----------|---------|---------|---------|---------|
| **纯CPU** | CPU | CPU | 基准 | 高 (80-100%) | 简单场景 |
| **NPU推理** | CPU | NPU | **10-20倍** | 中 (30-50%) | **推荐方案** |
| **NPU全流程** | NPU | NPU | **20-30倍** | 低 (10-20%) | 高性能场景 |
| **ONNX CPU** | CPU | CPU | 基准 | 高 (80-100%) | 临时测试 |

## 方案1：NPU推理加速（推荐）

### 原理
- **预处理**：在CPU上执行（I420→RGB→归一化）
- **推理**：在NPU上执行（使用RKNN模型）
- **性能**：推理速度提升10-20倍，整体FPS提升3-5倍

### 步骤

#### 1. 在PC上转换模型

```bash
# 安装rknn-toolkit2-lite（在PC上）
pip install rknn-toolkit2-lite

# 转换ONNX到RKNN（INT8量化）
python scripts/convert_onnx_to_rknn.py \
  --onnx models/yolov11n.onnx \
  --output models/yolov11n.rknn \
  --target rk3588 \
  --size 640 640 \
  --quantize
```

#### 2. 传输模型到RK3588

```bash
scp models/yolov11n.rknn root@192.168.5.15:/root/sagesight-ai/models/
```

#### 3. 配置RK3588专用配置文件

复制配置文件：
```bash
ssh root@192.168.5.15
cd /root/sagesight-ai
cp config/detector_config_rk3588.yaml config/detector_config.yaml
```

#### 4. 安装RKNN运行时（在RK3588上）

```bash
# 方法1：从源码编译安装
git clone https://github.com/airockchip/rknn-toolkit2.git
cd rknn-toolkit2/rknpu2
# 根据你的RK3588固件版本选择对应的包
pip install rknn_toolkit_lite2-*-cp310-cp310-linux_aarch64.whl

# 方法2：使用预编译包（推荐）
# 从官方或镜像站下载对应版本的wheel包
pip install rknn_toolkit_lite2-2.0.0-cp310-cp310-linux_aarch64.whl
```

#### 5. 性能测试

```bash
python3 run.py --test-performance
```

### 预期性能

| 输入尺寸 | 模型 | CPU FPS | NPU FPS | 提升倍数 |
|---------|------|---------|---------|---------|
| 640x640 | YOLOv11n | 5-8 fps | 50-80 fps | **10x** |
| 416x416 | YOLOv11n | 10-15 fps | 100-150 fps | **10x** |
| 320x320 | YOLOv11n | 15-20 fps | 150-200 fps | **10x** |

### 性能监控

```bash
# 监控NPU使用率
watch -n 1 'cat /sys/kernel/debug/rknpu/load'

# 监控CPU温度和频率
watch -n 1 'cat /sys/class/thermal/thermal_zone*/temp'
watch -n 1 'cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq'

# 监控内存使用
htop
```

## 方案2：零拷贝预处理（高级）

### 原理
使用MPP硬件解码器的输出直接作为NPU输入，避免CPU内存复制。

### 步骤

1. **使用DRM Prime格式**：
   - FFmpeg配置：`-hwaccel drm -hwaccel_output_format drm_prime`
   - 解码输出：DMA缓冲区（零拷贝）

2. **NPU直接访问DMA缓冲区**：
   - RKNN API支持DRM Prime格式输入
   - 避免`memcpy`操作

3. **性能提升**：
   - 减少30-40%的预处理时间
   - 降低CPU占用

### 配置示例

```yaml
decoder:
  output_format: "nv12"
  hwaccel: "drm"
  hwaccel_output_format: "drm_prime"
```

### 注意事项

- 需要修改解码器和RKNN后端代码
- 需要深入理解DRM/KMS子系统
- 适合专业用户

## 方案3：多核NPU并行（高级）

RK3588有双核NPU，可以并行运行2个模型。

### 配置

```yaml
detector:
  backend: "rknn"
  rknn_core_mask: 3  # 使用双核

detection_pool:
  num_detectors: 2  # 2个检测器进程
```

### 性能提升

- 单核NPU：50-80 fps
- 双核NPU：**100-150 fps**

### 限制

- 需要模型支持多实例
- 内存占用翻倍
- 需要测试稳定性

## 性能优化建议

### 1. 模型优化

- **输入尺寸**：320x320或416x416（性能优先）
- **INT8量化**：性能最佳，精度损失约1-2%
- **模型选择**：YOLOv11n（nano）或YOLOv11s（small）

### 2. 系统优化

```bash
# 设置CPU为性能模式
echo performance > /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# 设置NPU为性能模式
echo performance > /sys/devices/platform/fdab0000.npu/devfreq/fdab0000.npu/governor

# 增加内存缓存
echo 1024 > /proc/sys/vm/min_free_kbytes
```

### 3. 多路视频处理

RK3588适合处理多路视频流：

```yaml
detection_pool:
  use_detection_pool: true
  num_detectors: 2  # 双核NPU
  detection_queue_size: 1000
```

**推荐配置：**
- 4路720p RTSP流：FPS 15-20/路
- 2路1080p RTSP流：FPS 10-15/路
- 1路4K RTSP流：FPS 5-10/路

### 4. 内存优化

```bash
# 减少解码器缓冲区
decoder:
  buffer_size: 2  # 从默认的5减少到2

# 使用共享内存
use_shared_memory: true
```

## 故障排查

### NPU不可用

```bash
# 检查NPU驱动
ls /dev/rknpu*
# 应该显示：/dev/rknpu0 /dev/rknpu1

# 检查内核模块
lsmod | grep rknpu
```

### RKNN模型加载失败

```bash
# 检查RKNN运行时
python3 -c "from rknnlite.api import RKNNLite; print('OK')"

# 检查模型格式
file models/yolov11n.rknn
```

### 性能不达预期

1. **检查NPU使用率**：
   ```bash
   cat /sys/kernel/debug/rknpu/load
   # 应该接近100%
   ```

2. **检查CPU瓶颈**：
   ```bash
   top -H
   # 查看预处理线程CPU占用
   ```

3. **降低输入尺寸**：
   ```yaml
   preprocessor:
     model_input_size: 320  # 从640降到320
   ```

## 性能对比总结

| 配置 | FPS | CPU占用 | NPU占用 | 内存 |
|------|-----|---------|---------|------|
| CPU推理 | 5-8 | 90% | 0% | 2GB |
| NPU推理 | 50-80 | 40% | 80% | 2.5GB |
| NPU+零拷贝 | 70-100 | 25% | 90% | 2GB |
| 双核NPU | 100-150 | 60% | 90% | 4GB |

## 总结

**推荐方案：NPU推理（方案1）**

- ✅ 性能提升10-20倍
- ✅ 配置简单
- ✅ 稳定可靠
- ✅ 适合大多数场景

**高级方案：零拷贝+双核NPU**

- ✅ 性能提升20-30倍
- ⚠️ 需要深度定制
- ⚠️ 需要专业知识
- ✅ 适合高性能场景