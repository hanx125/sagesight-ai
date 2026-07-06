# ONNX 转 RKNN 模型转换指南

## 概述

RK3588、RK3566、RK3568 等瑞芯微平台使用 RKNN 格式模型进行推理，需要将 ONNX 模型转换为 RKNN 格式。

本文档介绍如何使用 SageSight AI 项目中的模型转换脚本将 ONNX 模型转换为 RKNN 格式。

## 安装 RKNN-Toolkit2

### 1. 系统要求

RKNN-Toolkit2 仅支持在 PC 上进行模型转换（Windows/Linux），转换后的模型在目标平台（RK3588）上运行。

**注意：**
- PC 上安装 RKNN-Toolkit2 用于模型转换
- 目标平台（RK3588）上安装 RKNN-API 用于模型推理
- 两者是不同的包，不要混淆

### 2. 安装方法

```bash
# 方法1：pip 安装（推荐）
pip install rknn-toolkit2

# 方法2：从瑞芯微官网下载
# 下载地址：https://github.com/rockchip-linux/rknn-toolkit2
# 解压后安装：pip install rknn_toolkit2-*.whl
```

### 3. 验证安装

```python
# 测试是否安装成功
from rknn.api import RKNN
print("RKNN-Toolkit2 已安装")
```

## 模型转换

### 1. 基础转换（INT8量化）

```bash
# 转换 YOLOv11n 模型（INT8量化，推荐）
python scripts/convert_onnx_to_rknn.py \
    --onnx models/yolov11n.onnx \
    --output models/yolov11n.rknn \
    --target rk3588 \
    --quantize true \
    --input-size 640
```

**参数说明：**
- `--onnx`: ONNX 模型文件路径（必需）
- `--output`: 输出的 RKNN 文件路径（可选，默认同名.rknn）
- `--target`: 目标平台（rk3588, rk3566, rk3568, rv1103, rv1106）
- `--quantize`: 是否量化（true=INT8, false=FP16）
- `--input-size`: 输入尺寸（默认640）

### 2. FP16转换（不量化）

```bash
# 转换 YOLOv11n 模型（FP16，精度更高）
python scripts/convert_onnx_to_rknn.py \
    --onnx models/yolov11n.onnx \
    --output models/yolov11n_fp16.rknn \
    --target rk3588 \
    --no-quantization \
    --input-size 640
```

**适用场景：**
- 对精度要求高的场景
- 模型推理速度要求不高的场景

### 3. 使用自定义数据集量化

```bash
# 使用自定义数据集进行混合量化（提高精度）
python scripts/convert_onnx_to_rknn.py \
    --onnx models/yolov11n.onnx \
    --output models/yolov11n.rknn \
    --target rk3588 \
    --quantize true \
    --dataset data/custom_dataset.txt \
    --input-size 640
```

**数据集格式：**
- 文件内容：每行一个图像路径（`.bin` 或 `.npy` 格式）
- 图像数量：建议 50-200 张代表性图像
- 数据范围：模拟实际输入数据的分布

## 量化数据集准备

### 1. 自动生成（默认）

脚本会自动生成 100 张随机图像作为量化数据集，适用于大多数场景。

```bash
# 自动生成量化数据集（默认行为）
python scripts/convert_onnx_to_rknn.py --onnx models/yolov11n.onnx
```

### 2. 手动准备（提高精度）

```python
# 准备自定义量化数据集
import numpy as np
import cv2
import os

# 设置参数
input_size = 640  # 模型输入尺寸
num_samples = 100  # 数据集大小
output_dir = 'data/custom_quantization'

# 创建输出目录
os.makedirs(output_dir, exist_ok=True)

# 生成代表性图像（从实际场景采集）
# 示例：从摄像头采集或从视频提取关键帧
for i in range(num_samples):
    # 选项1：从摄像头采集
    # frame = cv2.VideoCapture('rtsp://...').read()
    
    # 选项2：从视频文件提取
    # frame = extract_frame_from_video('video.mp4', i)
    
    # 选项3：生成随机数据（用于测试）
    frame = np.random.randint(0, 256, (input_size, input_size, 3), dtype=np.uint8)
    
    # 预处理（匹配模型输入）
    # 1. resize 到 input_size
    # 2. 归一化（如果需要）
    # 3. 转换为 NCHW 格式
    
    # 保存为 .bin 文件
    frame_processed = frame.transpose(2, 0, 1)  # HWC → CHW
    frame_processed = np.expand_dims(frame_processed, 0)  # CHW → NCHW
    
    output_file = os.path.join(output_dir, f'sample_{i}.bin')
    frame_processed.tofile(output_file)

# 创建数据集列表文件
with open(os.path.join(output_dir, 'dataset.txt'), 'w') as f:
    for i in range(num_samples):
        f.write(os.path.join(output_dir, f'sample_{i}.bin') + '\n')
```

## 转换后的模型使用

### 1. 在 RK3588 上使用

```python
from src.utils.rknn_executor import RKNNExecutor

# 加载 RKNN 模型
executor = RKNNExecutor(
    model_path='models/yolov11n.rknn',
    target='rk3588'
)

# 执行推理
import numpy as np
input_image = np.random.randint(0, 256, (1, 3, 640, 640), dtype=np.uint8)
outputs = executor.run(input_image)

# 处理输出结果
print(f"输出形状: {outputs[0].shape}")

# 释放资源
executor.release()
```

### 2. 在 SageSight AI 任务中使用

在任务配置中选择 RKNN 后端：

```yaml
# config/detector_config.yaml
detector_config:
  backend: rknn  # 使用 RKNN 后端
  rknn_target: rk3588  # 目标平台
  rknn_device_id: ''  # 设备ID（可选）
```

或在任务创建时配置：

```json
{
  "algorithm": "yolov11",
  "model_path": "models/yolov11n.rknn",
  "detector_config": {
    "backend": "rknn",
    "rknn_target": "rk3588",
    "rknn_device_id": ""
  }
}
```

## 性能对比

### RK3588 平台性能参考

| 模型格式 | 模型大小 | 推理时间 | 精度 | 适用场景 |
|---------|---------|---------|------|---------|
| ONNX    | ~6 MB   | ~100ms  | FP32 | PC 推理 |
| RKNN FP16 | ~3 MB | ~50ms | FP16 | 高精度场景 |
| RKNN INT8 | ~1.5 MB | ~30ms | INT8 | 实时检测（推荐） |

**建议：**
- 实时检测任务：使用 INT8 量化（速度最快）
- 高精度场景：使用 FP16（精度更高）

## 常见问题

### 1. RKNN-Toolkit2 安装失败

**问题：** `pip install rknn-toolkit2` 报错

**解决：**
```bash
# 检查 Python 版本（RKNN-Toolkit2 支持 Python 3.6-3.9）
python --version

# 如果版本不兼容，创建虚拟环境
conda create -n rknn python=3.8
conda activate rknn
pip install rknn-toolkit2
```

### 2. 模型转换失败

**问题：** `加载 ONNX 模型失败`

**解决：**
- 检查 ONNX 模型是否有效
- 使用 ONNX Simplifier 优化模型：
```bash
pip install onnx-simplifier
onnxsim models/yolov11n.onnx models/yolov11n_simplified.onnx
python scripts/convert_onnx_to_rknn.py --onnx models/yolov11n_simplified.onnx
```

### 3. 量化精度损失

**问题：** INT8 量化后检测精度下降

**解决：**
- 使用自定义量化数据集（提高代表性）
- 尝试 FP16 模式（不量化）
- 检查输入数据的预处理是否正确

### 4. RK3588 上推理失败

**问题：** 目标平台上 `RKNNExecutor` 初始化失败

**解决：**
- 确认 RKNN-API 已安装（不同于 RKNN-Toolkit2）
- 检查 target 参数是否正确（`rk3588`）
- 确认模型文件路径正确

## 参考链接

- [RKNN-Toolkit2 GitHub](https://github.com/rockchip-linux/rknn-toolkit2)
- [RKNN-Toolkit2 文档](https://github.com/rockchip-linux/rknn-toolkit2/blob/master/docs/RKNN-Toolkit2_User_Guide.pdf)
- [瑞芯微 NPU 开发指南](https://github.com/rockchip-linux/rknpu)

## 下一步

1. 转换您的 ONNX 模型到 RKNN 格式
2. 在 RK3588 上部署并测试
3. 根据性能需求选择量化模式（INT8/FP16）
4. 配置 SageSight AI 任务使用 RKNN 后端