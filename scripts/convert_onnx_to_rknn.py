#!/usr/bin/env python3
"""
ONNX 到 RKNN 模型转换脚本

用途：
在PC上（Windows/Linux）将ONNX模型转换为RKNN格式，用于RK3588/RK3566等瑞芯微平台的NPU推理。

使用方法：
python scripts/convert_onnx_to_rknn.py --onnx models/yolov11n.onnx --output models/yolov11n.rknn --target rk3588

依赖：
pip install rknn-toolkit2-lite

注意：
- 此脚本在PC上运行（用于模型转换）
- 转换后的RKNN模型需要在RK3588上运行（使用rknn-toolkit2）
"""

import os
import sys
import argparse
from pathlib import Path

def convert_onnx_to_rknn(onnx_path: str, output_path: str, target: str = 'rk3588',
                         input_size: tuple = (640, 640), quantize: bool = True,
                         dataset_path: str = None):
    """
    将ONNX模型转换为RKNN格式

    Args:
        onnx_path: ONNX模型路径
        output_path: 输出的RKNN模型路径
        target: 目标平台 (rk3588, rk3566, rk3568, rv1103, rv1106)
        input_size: 输入尺寸 (height, width)
        quantize: 是否量化为INT8
        dataset_path: 量化数据集路径（INT8量化需要）
    """
    try:
        from rknn.api import RKNN
    except ImportError:
        print("[ERROR] rknn-toolkit2-lite 未安装")
        print("[INFO] 请先安装：pip install rknn-toolkit2-lite")
        print("[INFO] 下载地址：https://github.com/airockchip/rknn-toolkit2")
        return False

    print("=" * 60)
    print("ONNX → RKNN 模型转换")
    print("=" * 60)
    print(f"[INFO] 输入模型：{onnx_path}")
    print(f"[INFO] 输出模型：{output_path}")
    print(f"[INFO] 目标平台：{target}")
    print(f"[INFO] 输入尺寸：{input_size}")
    print(f"[INFO] INT8量化：{quantize}")

    # 检查输入文件
    if not os.path.exists(onnx_path):
        print(f"[ERROR] ONNX模型不存在：{onnx_path}")
        return False

    # 创建输出目录
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    try:
        # 创建RKNN对象
        rknn = RKNN()

        # ========================================
        # 配置模型
        # ========================================
        print("\n[1/4] 配置模型...")
        config = {
            'target_platform': target,
            'mean_values': [[0, 0, 0]],
            'std_values': [[255, 255, 255]],
        }

        # 可选：优化性能
        # config['optimization_level'] = 3
        # config['quantize_input_node'] = True

        ret = rknn.config(**config)
        if ret != 0:
            print(f"[ERROR] 配置失败：{ret}")
            return False
        print("[OK] 配置成功")

        # ========================================
        # 加载ONNX模型
        # ========================================
        print("\n[2/4] 加载ONNX模型...")
        ret = rknn.load_onnx(model=onnx_path)
        if ret != 0:
            print(f"[ERROR] 加载ONNX失败：{ret}")
            return False
        print("[OK] ONNX模型加载成功")

        # ========================================
        # 构建RKNN模型
        # ========================================
        print("\n[3/4] 构建RKNN模型...")

        # INT8量化需要数据集
        if quantize:
            if dataset_path and os.path.exists(dataset_path):
                print(f"[INFO] 使用量化数据集：{dataset_path}")
                ret = rknn.build(do_quantization=True, dataset=dataset_path)
            else:
                print("[WARN] 未提供量化数据集，使用默认量化")
                print("[INFO] 推荐：提供量化数据集以获得更好的精度")
                # 不提供数据集时，RKNN会自动生成随机数据进行量化
                ret = rknn.build(do_quantization=True)
        else:
            # 不量化，使用FP16
            ret = rknn.build(do_quantization=False)

        if ret != 0:
            print(f"[ERROR] 构建失败：{ret}")
            return False
        print("[OK] RKNN模型构建成功")

        # ========================================
        # 导出RKNN模型
        # ========================================
        print("\n[4/4] 导出RKNN模型...")
        ret = rknn.export_rknn(output_path)
        if ret != 0:
            print(f"[ERROR] 导出失败：{ret}")
            return False
        print(f"[OK] RKNN模型已保存：{output_path}")

        # ========================================
        # 显示模型信息
        # ========================================
        print("\n" + "=" * 60)
        print("模型信息")
        print("=" * 60)
        file_size = os.path.getsize(output_path) / (1024 * 1024)
        print(f"文件大小：{file_size:.2f} MB")
        print(f"目标平台：{target}")
        print(f"量化方式：{'INT8' if quantize else 'FP16'}")
        print(f"输入尺寸：{input_size}")

        # 清理
        rknn.release()

        print("\n" + "=" * 60)
        print("转换完成！")
        print("=" * 60)
        print(f"\n下一步操作：")
        print(f"1. 将RKNN模型传输到RK3588：")
        print(f"   scp {output_path} root@192.168.5.15:/root/sagesight-ai/models/")
        print(f"\n2. 修改配置文件：")
        print(f"   config/detector_config.yaml:")
        print(f"     backend: 'rknn'")
        print(f"     model_path: '{output_path}'")
        print(f"\n3. 启动服务：")
        print(f"   python3 run.py")

        return True

    except Exception as e:
        import traceback
        print(f"[ERROR] 转换失败：{e}")
        print(traceback.format_exc())
        return False


def main():
    parser = argparse.ArgumentParser(description='ONNX到RKNN模型转换工具')

    parser.add_argument('--onnx', '-i', required=True, help='ONNX模型路径')
    parser.add_argument('--output', '-o', required=True, help='输出RKNN模型路径')
    parser.add_argument('--target', '-t', default='rk3588',
                        choices=['rk3588', 'rk3566', 'rk3568', 'rv1103', 'rv1106'],
                        help='目标平台（默认：rk3588）')
    parser.add_argument('--size', '-s', nargs=2, type=int, default=[640, 640],
                        help='输入尺寸：height width（默认：640 640）')
    parser.add_argument('--quantize', '-q', action='store_true', default=True,
                        help='启用INT8量化（默认：True）')
    parser.add_argument('--no-quantize', dest='quantize', action='store_false',
                        help='禁用INT8量化，使用FP16')
    parser.add_argument('--dataset', '-d', help='量化数据集路径（INT8量化推荐）')

    args = parser.parse_args()

    # 转换模型
    success = convert_onnx_to_rknn(
        onnx_path=args.onnx,
        output_path=args.output,
        target=args.target,
        input_size=tuple(args.size),
        quantize=args.quantize,
        dataset_path=args.dataset
    )

    if success:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()