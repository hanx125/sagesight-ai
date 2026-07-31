#!/usr/bin/env python3
"""
修改RK3588配置文件启用硬件加速解码
"""
import re

config_file = '/root/sagesight-ai/config/decoder_config.yaml'

with open(config_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 修改硬件加速预设为RK3588
content = re.sub(r'hwaccel_preset:\s*"[^"]*"', 'hwaccel_preset: "preset-rk-h264"', content)

with open(config_file, 'w', encoding='utf-8') as f:
    f.write(content)

print("[OK] RK3588硬件加速配置完成：")
print("  - hwaccel_preset: preset-rk-h264 (RKMPP硬件解码)")
print("  - 输出格式：DRM Prime（零拷贝）")