#!/usr/bin/env python3
"""
设置RK3588的RKNN配置
"""
import re

config_file = '/root/sagesight-ai/config/detector_config.yaml'

with open(config_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 修改backend为rknn
content = re.sub(r'backend:\s*"[^"]*"', 'backend: "rknn"', content)

# 修改rknn_core_mask为3（双核NPU）
content = re.sub(r'rknn_core_mask:\s*\d+', 'rknn_core_mask: 3', content)

with open(config_file, 'w', encoding='utf-8') as f:
    f.write(content)

print("[OK] RK3588 RKNN配置完成：")
print("  - backend: rknn")
print("  - rknn_core_mask: 3 (双核NPU)")
print("  - model: models/yolov11n.rknn")