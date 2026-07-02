"""
检查 YOLO11n 模型输出格式
"""

import sys
import numpy as np
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import onnxruntime as ort

# 加载模型
model_path = project_root / "models" / "yolov11n.onnx"
print(f"加载模型：{model_path}")

session = ort.InferenceSession(str(model_path))

# 获取输入输出信息
inputs = session.get_inputs()
outputs = session.get_outputs()

print(f"\n输入信息:")
for i, inp in enumerate(inputs):
    print(f"  [{i}] 名称：{inp.name}, 形状：{inp.shape}")

print(f"\n输出信息:")
for i, out in enumerate(outputs):
    print(f"  [{i}] 名称：{out.name}, 形状：{out.shape}")

# 创建测试输入
print(f"\n创建测试输入...")
test_input = np.random.rand(1, 3, 640, 640).astype(np.float32)

# 执行推理
print(f"执行推理...")
outputs = session.run(None, {inputs[0].name: test_input})

print(f"\n输出详情:")
for i, output in enumerate(outputs):
    print(f"  输出 [{i}]:")
    print(f"    形状：{output.shape}")
    print(f"    数据类型：{output.dtype}")
    print(f"    最小值：{output.min():.6f}")
    print(f"    最大值：{output.max():.6f}")
    print(f"    均值：{output.mean():.6f}")
    
    # 如果是 3D 输出，打印详细形状
    if output.ndim == 3:
        print(f"    解析：(batch={output.shape[0]}, boxes={output.shape[1]}, attrs={output.shape[2]})")
        print(f"    前 4 列 (x,y,w,h) 均值：{output[0, :, :4].mean(axis=0)}")
        print(f"    后 80 列 (class_confs) 均值：{output[0, :, 4:].mean(axis=0)[:5]}...")
