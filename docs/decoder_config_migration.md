# 解码器配置参数迁移指南

## 📋 变更摘要

**版本：** 新架构（环形缓冲区 + 索引队列）

**变更内容：** 移除旧版参数 `hwaccel`，统一使用新版参数 `hwaccel_preset`

## 🔄 参数对比

### 旧版参数（已删除）

```python
# ❌ 不再支持
hwaccel: str = "auto"  # 旧版硬件加速参数

# 支持的值：
# - "dxva2": Windows DXVA2
# - "cuda": NVIDIA CUDA
# - "qsv": Intel QSV
# - "rk3588": Rockchip RKMPP
# - "none": CPU 解码
```

### 新版参数（必须使用）

```python
# ✅ 必须使用
hwaccel_preset: str = "preset-dxva2"  # 硬件加速预设

# 支持的预设：
# - "preset-dxva2": Windows DXVA2
# - "preset-nvidia": NVIDIA CUDA
# - "preset-intel-qsv-h264": Intel QSV H.264
# - "preset-intel-qsv-h265": Intel QSV H.265
# - "preset-vaapi": AMD VAAPI
# - "preset-rk-h264": Rockchip RKMPP H.264
# - "preset-rk-h265": Rockchip RKMPP H.265
```

## 📊 迁移对照表

| 旧版参数 | 新版参数 | 说明 |
|---------|---------|------|
| `hwaccel: "dxva2"` | `hwaccel_preset: "preset-dxva2"` | Windows DXVA2 |
| `hwaccel: "cuda"` | `hwaccel_preset: "preset-nvidia"` | NVIDIA CUDA |
| `hwaccel: "qsv"` | `hwaccel_preset: "preset-intel-qsv-h264"` | Intel QSV |
| `hwaccel: "rk3588"` | `hwaccel_preset: "preset-rk-h264"` | Rockchip RKMPP |
| `hwaccel: "none"` | `hwaccel_preset: ""`（空字符串） | CPU 解码 |
| `hwaccel: "auto"` | `hwaccel_preset: "preset-dxva2"` | Windows 默认 |

## 💡 迁移示例

### 代码迁移

#### 旧版代码（不再支持）

```python
from src.decoders.video_decoder import DecoderConfig

# ❌ 旧版参数（不再支持）
config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel="dxva2",  # ❌ 旧版参数
    width=640,
    height=640,
)
```

#### 新版代码（必须使用）

```python
from src.decoders.video_decoder import DecoderConfig
from src.utils import FFMPEG_HWACCEL_DXVA2

# ✅ 新版参数（必须使用）
config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel_preset=FFMPEG_HWACCEL_DXVA2,  # ✅ 新版参数
    width=640,
    height=640,
)
```

### 配置文件迁移

#### YAML 配置文件

**旧版配置（不再支持）：**

```yaml
decoder:
  hwaccel: "dxva2"  # ❌ 旧版参数
```

**新版配置（必须使用）：**

```yaml
decoder:
  hwaccel_preset: "preset-dxva2"  # ✅ 新版参数
```

### 任务表迁移

#### 数据库任务表配置

**旧版配置（不再支持）：**

```json
{
  "hwaccel": "cuda"  // ❌ 旧版参数
}
```

**新版配置（必须使用）：**

```json
{
  "hwaccel_preset": "preset-nvidia"  // ✅ 新版参数
}
```

## 🚀 完整迁移示例

### 场景1：Windows DXVA2

**旧版代码：**

```python
config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel="dxva2",
)
```

**新版代码：**

```python
from src.utils import FFMPEG_HWACCEL_DXVA2

config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel_preset=FFMPEG_HWACCEL_DXVA2,
)
```

### 场景2：NVIDIA CUDA

**旧版代码：**

```python
config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel="cuda",
)
```

**新版代码：**

```python
from src.utils import FFMPEG_HWACCEL_NVIDIA

config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel_preset=FFMPEG_HWACCEL_NVIDIA,
)
```

### 场景3：Intel QSV

**旧版代码：**

```python
config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel="qsv",
)
```

**新版代码：**

```python
config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel_preset="preset-intel-qsv-h264",
)
```

### 场景4：CPU 解码

**旧版代码：**

```python
config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel="none",
)
```

**新版代码：**

```python
config = DecoderConfig(
    rtsp_url="rtsp://...",
    hwaccel_preset="",  # 空字符串表示 CPU 解码
)
```

## ✅ 迁移检查清单

### 代码迁移

- [ ] 检查所有使用 `DecoderConfig` 的地方
- [ ] 替换 `hwaccel` 参数为 `hwaccel_preset`
- [ ] 导入对应的预设常量：
  ```python
  from src.utils import (
      FFMPEG_HWACCEL_DXVA2,
      FFMPEG_HWACCEL_NVIDIA,
      FFMPEG_HWACCEL_VAAPI,
  )
  ```

### 配置文件迁移

- [ ] 更新 YAML 配置文件：`config/decoder_config.yaml`
- [ ] 更新任务表中的 `decoder_config` 字段
- [ ] 检查所有 JSON 配置文件

### 测试验证

- [ ] 运行解码器测试，确保硬件加速正常工作
- [ ] 检查 FFmpeg 命令是否正确
- [ ] 验证 CPU/GPU 占用率符合预期

## 📚 相关文档

- [解码器配置指南](./decoder_config_guide.md)
- [解码器数据流程](./decoder_data_flow.md)
- [硬件加速对比](./hardware_acceleration_comparison.md)

## ⚠️ 注意事项

1. **必须使用新版参数**
   - 旧版参数 `hwaccel` 已完全移除
   - 如果未指定 `hwaccel_preset`，将使用 CPU 解码

2. **预设名称必须准确**
   - 预设名称必须完全匹配（区分大小写）
   - 使用导入的常量，避免拼写错误

3. **CPU 解码性能**
   - 如果未指定硬件加速预设，将使用 CPU 解码
   - CPU 解码占用较高（80-100%），不推荐用于生产环境

4. **兼容性**
   - 新架构不支持旧版参数
   - 必须迁移所有旧配置

## 🆘 常见问题

### Q1: 为什么要移除旧版参数？

**A:** 新版参数 `hwaccel_preset` 更灵活，支持更多硬件加速方案，并且与 FFmpeg 预设系统集成更紧密。

### Q2: 如果忘记指定 `hwaccel_preset` 会怎样？

**A:** 解码器将使用 CPU 解码，性能较低。建议在配置文件中设置默认值。

### Q3: 旧版代码会报错吗？

**A:** 不会直接报错，但 `hwaccel` 参数将被忽略，可能使用 CPU 解码。

### Q4: 如何检查是否正确迁移？

**A:** 查看解码器启动日志：
```
[VideoDecoder] 使用硬件加速预设：preset-dxva2  # ✅ 正确
[VideoDecoder] 未指定硬件加速预设，使用 CPU 解码  # ⚠️ 需要检查
```