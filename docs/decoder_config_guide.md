# 解码器配置参数说明

## 📊 配置来源优先级

解码器配置参数来自三个层级，优先级从高到低：

```
任务表中的配置（最高优先级）
    ↓
YAML 配置文件（中等优先级）
    ↓
DecoderConfig 类默认值（最低优先级）
```

### 优先级示例

假设解码宽度配置：

| 来源 | 值 | 是否使用 | 说明 |
|------|---|---------|------|
| 任务表 | 1920 | ✅ **使用此值** | 最高优先级 |
| YAML 配置文件 | 1280 | ❌ 被覆盖 | 中等优先级 |
| DecoderConfig 类 | 640 | ❌ 被覆盖 | 最低优先级 |

**最终使用的宽度：1920**

## 📝 配置方式

### 方式1：YAML 配置文件（默认值）

**文件位置：** `config/decoder_config.yaml`

```yaml
decoder:
  # 视频参数
  width: 640
  height: 640
  fps: 30

  # 硬件加速配置
  hwaccel_preset: "preset-dxva2"  # Windows DXVA2
  input_preset: "preset-rtsp-generic"

  # 环形缓冲区配置
  ring_buffer_block_count: 10
  index_queue_size: 50
```

**特点：**
- 适用于所有任务的默认配置
- 如果任务表没有指定，使用此配置
- 适合快速开发和测试

### 方式2：任务表动态配置（推荐）

**数据库字段：** `tasks.decoder_config`（JSON 格式）

```json
{
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "hwaccel_preset": "preset-nvidia",
  "input_preset": "preset-rtsp-generic",
  "ring_buffer_block_count": 15,
  "index_queue_size": 75
}
```

**特点：**
- 每个任务可以有不同的配置
- 优先级最高，会覆盖 YAML 配置
- 适合生产环境和多任务场景

### 方式3：代码硬编码（不推荐）

**位置：** `src/decoders/video_decoder.py` → `DecoderConfig` 类

```python
@dataclass
class DecoderConfig:
    width: int = 640  # 硬编码默认值
    height: int = 640
    fps: int = 30
```

**特点：**
- 最低优先级
- 仅作为兜底默认值
- 不推荐直接修改

## 🚀 实际使用流程

### 1. 任务管理器读取配置

```python
# src/tasks/task_manager.py

def get_default_decoder_config() -> Dict:
    """从 YAML 配置文件读取默认配置"""
    config = load_yaml_config(DECODER_CONFIG_PATH)
    decoder_config = config.get('decoder', {})

    default_config = {
        'width': decoder_config.get('width', 640),
        'height': decoder_config.get('height', 640),
        'fps': decoder_config.get('fps', 30),
        'hwaccel_preset': decoder_config.get('hwaccel_preset', 'preset-dxva2'),
        # ... 其他参数
    }

    return default_config
```

### 2. 任务执行器合并配置

```python
# src/tasks/task_executor.py

class CameraWorker:
    def __init__(self, camera_config: Dict, task_config: Dict, ...):
        # 从任务配置中获取解码器配置
        self.decoder_cfg = task_config.get('decoder_config', {})

        # 合并配置：任务表配置 → YAML 配置 → 类默认值
        from ..tasks.task_manager import get_default_decoder_config

        default_config = get_default_decoder_config()
        final_config = {**default_config, **self.decoder_cfg}

        # 创建解码器配置对象
        from ..decoders.video_decoder import DecoderConfig

        config = DecoderConfig(
            rtsp_url=self.rtsp_url,
            width=final_config.get('width', 640),
            height=final_config.get('height', 640),
            fps=final_config.get('fps', 30),
            hwaccel_preset=final_config.get('hwaccel_preset', 'preset-dxva2'),
            input_preset=final_config.get('input_preset', 'preset-rtsp-generic'),
            ring_buffer_block_count=final_config.get('ring_buffer_block_count', 10),
            index_queue_size=final_config.get('index_queue_size', 50),
        )
```

### 3. 解码器使用配置

```python
# src/decoders/video_decoder.py

class VideoDecoder:
    def __init__(self, config: DecoderConfig):
        self.config = config
        self.width = config.width  # 使用最终配置
        self.height = config.height
        self.fps = config.fps
        # ...
```

## 📊 完整示例

### 场景1：使用默认配置（YAML）

```python
# 任务表中不指定 decoder_config
# 使用 YAML 配置文件中的值

# config/decoder_config.yaml
decoder:
  width: 1280
  height: 720
  fps: 25
  hwaccel_preset: "preset-dxva2"

# 任务执行器
config = DecoderConfig(
    rtsp_url="rtsp://...",
    # 其他参数从 YAML 文件读取
)

# 最终配置：
# width: 1280
# height: 720
# fps: 25
# hwaccel_preset: "preset-dxva2"
```

### 场景2：任务表覆盖部分配置

```python
# 任务表中的 decoder_config
{
  "width": 1920,
  "height": 1080
}

# config/decoder_config.yaml
decoder:
  width: 1280
  height: 720
  fps: 25
  hwaccel_preset: "preset-dxva2"

# 任务执行器合并配置
final_config = {
    'width': 1920,  # 来自任务表（覆盖 YAML）
    'height': 1080,  # 来自任务表（覆盖 YAML）
    'fps': 25,  # 来自 YAML（任务表未指定）
    'hwaccel_preset': "preset-dxva2",  # 来自 YAML（任务表未指定）
}

# 最终配置：
# width: 1920（任务表）
# height: 1080（任务表）
# fps: 25（YAML）
# hwaccel_preset: "preset-dxva2"（YAML）
```

### 场景3：任务表完整覆盖

```python
# 任务表中的 decoder_config
{
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "hwaccel_preset": "preset-nvidia",
  "input_preset": "preset-rtsp-generic",
  "ring_buffer_block_count": 20,
  "index_queue_size": 100
}

# config/decoder_config.yaml
decoder:
  width: 640  # 被覆盖
  height: 640  # 被覆盖
  fps: 30  # 被覆盖

# 最终配置：
# width: 1920（任务表）
# height: 1080（任务表）
# fps: 30（任务表）
# hwaccel_preset: "preset-nvidia"（任务表）
# ring_buffer_block_count: 20（任务表）
# index_queue_size: 100（任务表）
```

## 🔧 配置参数列表

### 核心参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `width` | int | 640 | 解码输出宽度 |
| `height` | int | 640 | 解码输出高度 |
| `fps` | int | 30 | 检测帧率 |
| `hwaccel_preset` | str | "preset-dxva2" | 硬件加速预设（必须使用）|
| `input_preset` | str | "preset-rtsp-generic" | 输入预设 |
| `output_format` | str | "auto" | 输出格式 |

### 环形缓冲区参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `ring_buffer_block_count` | int | 10 | 环形缓冲区块数量 |
| `index_queue_size` | int | 50 | 帧索引队列大小 |

### RTSP 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `rtsp_transport` | str | "tcp" | RTSP 传输协议 |
| `reconnect` | bool | True | 是否自动重连 |
| `reconnect_delay` | int | 5 | 重连延迟（秒） |
| `buffer_size` | int | 2MB | 输入缓冲区大小 |

## 💡 最佳实践

### 1. 开发环境

使用 YAML 配置文件：
```yaml
decoder:
  width: 640
  height: 640
  fps: 15  # 降低帧率，减少计算量
  hwaccel_preset: "preset-dxva2"
```

### 2. 生产环境

使用任务表动态配置：
```json
{
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "hwaccel_preset": "preset-nvidia",
  "ring_buffer_block_count": 20,
  "index_queue_size": 100
}
```

### 3. 特殊场景

**高码率视频：**
```json
{
  "buffer_size": 4194304,  # 4MB
  "ring_buffer_block_count": 15
}
```

**低延迟场景：**
```json
{
  "fps": 30,
  "ring_buffer_block_count": 5,
  "index_queue_size": 20
}
```

**稳定性优先：**
```json
{
  "ring_buffer_block_count": 20,
  "index_queue_size": 100
}
```

## 📚 相关文档

- [解码器数据流程](./decoder_data_flow.md)
- [硬件加速对比](./hardware_acceleration_comparison.md)
- [使用示例](../examples/decoder_usage_example.py)