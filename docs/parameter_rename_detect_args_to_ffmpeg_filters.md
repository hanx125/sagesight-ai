# 参数重命名说明：detect_args → ffmpeg_filters

## 📋 变更摘要

**变更时间：** 2026-07-17

**变更原因：** 原参数名 `detect_args` 容易误导用户，让人误以为是"检测器参数"，但实际上是 **FFmpeg 滤镜参数**。

## 🔍 参数作用说明

### 实际用途：视频预处理

```python
# 在解码后、缩放前应用的 FFmpeg 滤镜
ffmpeg_filters: List[str] = []  # 如降噪、锐化等

# 示例：
ffmpeg_filters: ["noise=c1s=1:allf"]  # 降噪滤镜
ffmpeg_filters: ["unsharp=5:5:1.0:5:5:0.0"]  # 锐化滤镜
```

**作用环节：**
```
[网络摄像头] 
    ↓ 
[拉流 (FFmpeg)] 
    ↓ 
[解码 (GPU/CPU)] 
    ↓ 
[FFmpeg 滤镜处理] ← ffmpeg_filters 在这里应用
    ↓ 
[缩放 + 格式转换]
    ↓ 
[共享内存]
    ↓ 
[检测器] ← 检测器不使用此参数
```

## 📊 重命名对比

| 项目 | 旧名称 | 新名称 | 说明 |
|------|--------|--------|------|
| 参数名称 | `detect_args` | `ffmpeg_filters` | 更准确地描述参数用途 |
| 参数类型 | `List[str]` | `List[str]` | 不变 |
| 默认值 | `[]` | `[]` | 不变 |
| 所属模块 | 解码器 | 解码器 | 不变 |
| 作用对象 | FFmpeg 滤镜 | FFmpeg 滤镜 | 不变 |

## 🔄 修改的文件

### 1. 后端代码

| 文件 | 修改内容 | 行数 |
|------|---------|------|
| `src/decoders/video_decoder.py` | 参数定义 | 220 |
| `src/decoders/video_decoder.py` | 参数使用 | 299, 394 |
| `src/tasks/task_manager.py` | 配置加载 | 96 |

### 2. 配置文件

| 文件 | 修改内容 | 行数 |
|------|---------|------|
| `config/decoder_config.yaml` | 参数定义和说明 | 88-93, 108 |
| `docs/decoder_config_guide.md` | 参数列表 | （已更新）|

### 3. 前端代码

| 文件 | 修改内容 | 行数 |
|------|---------|------|
| `static/js/config_modal.js` | 参数加载 | 85 |
| `static/js/config_modal.js` | 参数保存 | 193-225 |
| `templates/tasks.html` | 输入字段 | 448-450 |

## 💡 使用示例

### 旧版代码（不再支持）

```python
# ❌ 旧版参数名（已弃用）
config = DecoderConfig(
    rtsp_url="rtsp://...",
    detect_args=["noise=c1s=1:allf"],  # ❌ 已重命名
)
```

### 新版代码（必须使用）

```python
# ✅ 新版参数名
config = DecoderConfig(
    rtsp_url="rtsp://...",
    ffmpeg_filters=["noise=c1s=1:allf"],  # ✅ 正确
)
```

### YAML 配置文件

```yaml
decoder:
  # ✅ FFmpeg 滤镜参数（视频预处理）
  ffmpeg_filters: []
  
  # 示例：应用降噪滤镜
  # ffmpeg_filters: ["noise=c1s=1:allf"]
```

### 任务表配置

```json
{
  "decoder_config": {
    "width": 1920,
    "height": 1080,
    "ffmpeg_filters": ["noise=c1s=1:allf"]  // ✅ 降噪滤镜
  }
}
```

## 🎯 为什么需要此参数？

### 常见用途

1. **降噪处理**
   ```python
   ffmpeg_filters: ["noise=c1s=1:allf"]
   ```

2. **锐化处理**
   ```python
   ffmpeg_filters: ["unsharp=5:5:1.0:5:5:0.0"]
   ```

3. **去隔行**
   ```python
   ffmpeg_filters: ["yadif=0"]
   ```

4. **色彩校正**
   ```python
   ffmpeg_filters: ["eq=brightness=0.1:contrast=1.2"]
   ```

### 何时使用？

- ✅ 视频质量较差，需要降噪
- ✅ 视频模糊，需要锐化
- ✅ 视频有隔行扫描问题
- ✅ 需要色彩校正

### 何时不需要？

- ❌ 视频质量良好（推荐：使用空列表 `[]`）
- ❌ 不了解 FFmpeg 滤镜参数（推荐：使用空列表 `[]`）

## ⚠️ 注意事项

1. **参数顺序**
   - FFmpeg 滤镜按列表顺序依次应用
   - 例如：`["noise=c1s=1:allf", "unsharp"]` → 先降噪，后锐化

2. **性能影响**
   - 每个滤镜都会增加处理时间
   - 推荐只使用必要的滤镜

3. **滤镜参数**
   - 必须符合 FFmpeg 滤镜语法
   - 错误的参数会导致 FFmpeg 命令失败

4. **与检测器无关**
   - 此参数不会影响检测器配置
   - 检测器配置在 `detector_config` 中

## 📚 相关文档

- [FFmpeg 滤镜文档](https://ffmpeg.org/ffmpeg-filters.html)
- [解码器配置指南](./decoder_config_guide.md)
- [解码器数据流程](./decoder_data_flow.md)

## 🔗 迁移指南

如果你的代码或配置中使用了 `detect_args`，请按以下步骤迁移：

### 步骤1：更新代码

```python
# 找到所有使用 detect_args 的地方
grep -r "detect_args" d:\sagesight-ai\

# 替换为 ffmpeg_filters
```

### 步骤2：更新配置文件

```yaml
# 将所有 detect_args 替换为 ffmpeg_filters
# config/decoder_config.yaml
decoder:
  ffmpeg_filters: []  # ✅ 新参数名
```

### 步骤3：更新任务表

```sql
-- 更新数据库中的字段名
UPDATE tasks 
SET decoder_config = JSON_MODIFY(
    JSON_MODIFY(decoder_config, '$.ffmpeg_filters', JSON_VALUE(decoder_config, '$.detect_args')),
    '$.detect_args', NULL
)
WHERE JSON_VALUE(decoder_config, '$.detect_args') IS NOT NULL;
```

### 步骤4：验证

运行解码器测试，确保 FFmpeg 命令正确：

```bash
python test_decoder.py
```

查看日志输出，确认参数已正确应用：

```
[VideoDecoder] FFmpeg 命令：ffmpeg ... -vf noise=c1s=1:allf ...
```

## ✅ 总结

- ✅ 参数已从 `detect_args` 重命名为 `ffmpeg_filters`
- ✅ 名称更准确地描述了参数用途（FFmpeg 滤镜）
- ✅ 所有相关文件已更新
- ✅ 向后兼容：旧配置需手动迁移

**核心改进：参数名称更清晰，避免误导用户！**