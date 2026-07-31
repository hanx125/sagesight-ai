#!/usr/bin/env python3
"""
修改RK3588配置文件为ONNX CPU模式
"""
import re

config_file = '/root/sagesight-ai/config/detector_config.yaml'

with open(config_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 修改backend为onnx_cpu
content = re.sub(r'backend:\s*"rknn"', 'backend: "onnx_cpu"', content)

with open(config_file, 'w', encoding='utf-8') as f:
    f.write(content)

print("[OK] 配置文件已修改：backend: rknn → onnx_cpu")