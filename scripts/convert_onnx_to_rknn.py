"""
ONNX 转 RKNN 模型转换脚本

使用 RKNN-Toolkit2 将 ONNX 模型转换为 RKNN 格式，适配 RK3588/RK3566/RK3568 等瑞芯微平台。

使用方法：
1. 安装 RKNN-Toolkit2：pip install rknn-toolkit2
2. 运行转换脚本：python scripts/convert_onnx_to_rknn.py --onnx models/yolov11n.onnx --output models/yolov11n.rknn --target rk3588

支持的参数：
- --onnx: ONNX 模型文件路径（必需）
- --output: 输出的 RKNN 模型文件路径（默认：同名.rknn）
- --target: 目标平台（rk3588, rk3566, rk3568, rv1103, rv1106）
- --quantize: 是否量化（默认：True，使用INT8量化）
- --dataset: 量化数据集路径（可选，用于混合量化）
- --input-size: 输入尺寸（默认：640x640）

注意事项：
1. RKNN-Toolkit2 仅支持在 PC 上进行模型转换，转换后的模型在目标平台上运行
2. 量化需要提供代表性数据集，用于统计各层张量的分布
3. 转换后的模型大小会显著减小，推理速度大幅提升
"""

import argparse
import os
import sys
import numpy as np
from pathlib import Path

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# RKNN-Toolkit2 导入
RKNN_TOOLKIT_AVAILABLE = False
try:
    from rknn.api import RKNN
    RKNN_TOOLKIT_AVAILABLE = True
    print("[模型转换] RKNN-Toolkit2 已加载")
except ImportError:
    print("[模型转换] RKNN-Toolkit2 未安装")
    print("[模型转换] 请安装：pip install rknn-toolkit2")
    print("[模型转换] 注意：RKNN-Toolkit2 仅在 PC 上用于模型转换，不需要在目标平台安装")


def convert_onnx_to_rknn(
    onnx_path: str,
    output_path: str = None,
    target: str = 'rk3588',
    quantize: bool = True,
    dataset_path: str = None,
    input_size: int = 640,
    do_quantization: bool = True
):
    """
    将 ONNX 模型转换为 RKNN 格式
    
    Args:
        onnx_path: ONNX 模型文件路径
        output_path: 输出的 RKNN 模型文件路径（默认：同名.rknn）
        target: 目标平台（rk3588, rk3566, rk3568, rv1103, rv1106）
        quantize: 是否量化（True=INT8量化，False=FP16）
        dataset_path: 量化数据集路径（可选）
        input_size: 输入尺寸
        do_quantization: 是否执行量化（True=量化，False=不量化）
    
    Returns:
        bool: 转换是否成功
    """
    
    if not RKNN_TOOLKIT_AVAILABLE:
        print("[模型转换] 错误：RKNN-Toolkit2 未安装，无法进行模型转换")
        return False
    
    # 检查 ONNX 文件是否存在
    if not os.path.exists(onnx_path):
        print(f"[模型转换] 错误：ONNX 文件不存在: {onnx_path}")
        return False
    
    # 设置输出路径
    if output_path is None:
        output_path = onnx_path.replace('.onnx', '.rknn')
    
    print("="*70)
    print("ONNX 转 RKNN 模型转换")
    print("="*70)
    print(f"输入模型: {onnx_path}")
    print(f"输出模型: {output_path}")
    print(f"目标平台: {target}")
    print(f"量化模式: {'INT8量化' if quantize else 'FP16'}")
    print(f"输入尺寸: {input_size}x{input_size}")
    print(f"数据集路径: {dataset_path if dataset_path else '未提供（使用默认数据）'}")
    print()
    
    # 创建 RKNN 对象
    rknn = RKNN()
    
    # 1. 配置 RKNN 模型参数
    print("[步骤1] 配置 RKNN 模型参数")
    ret = rknn.config(
        target_platform=target,
        # 单输入单输出模型配置
        single_core_mode=True,  # 单核心模式（RK3588）
        # 优化配置
        model_version='v1',  # RKNN 模型版本
        # 量化配置（如果启用量化）
        optimization_level=3,  # 优化等级（0-3，默认3）
        # 输入输出配置
        inputs=['images'],  # YOLOv11 输入节点名称
        input_names=['images'],
        input_formats=['NCHW'],  # 输入格式：NCHW（batch, channels, height, width）
        input_types=['float32'],  # 输入类型
        input_shapes=[[1, 3, input_size, input_size]],  # 输入形状
        outputs=['output0'],  # YOLOv11 输出节点名称
        output_names=['output0'],
        output_formats=['NCHW'],  # 输出格式
        output_types=['float32'],  # 输出类型
    )
    
    if ret != 0:
        print(f"[模型转换] 错误：配置 RKNN 参数失败，错误码: {ret}")
        rknn.release()
        return False
    
    print("[步骤1] 配置完成")
    print()
    
    # 2. 加载 ONNX 模型
    print("[步骤2] 加载 ONNX 模型")
    ret = rknn.load_onnx(model=onnx_path)
    
    if ret != 0:
        print(f"[模型转换] 错误：加载 ONNX 模型失败，错误码: {ret}")
        rknn.release()
        return False
    
    print(f"[步骤2] 加载完成: {onnx_path}")
    print()
    
    # 3. 构建 RKNN 模型（不量化）
    if not do_quantization or not quantize:
        print("[步骤3] 构建 RKNN 模型（不量化）")
        ret = rknn.build(do_quantization=False)
        
        if ret != 0:
            print(f"[模型转换] 错误：构建 RKNN 模型失败，错误码: {ret}")
            rknn.release()
            return False
        
        print("[步骤3] 构建完成（FP16模式）")
        print()
    
    # 4. 构建 RKNN 模型（量化）
    else:
        print("[步骤3] 构建 RKNN 模型（INT8量化）")
        
        # 准备量化数据集
        if dataset_path and os.path.exists(dataset_path):
            # 使用用户提供的数据集
            print(f"[步骤3.1] 使用数据集: {dataset_path}")
            dataset_file = dataset_path
        else:
            # 生成默认量化数据（随机数据）
            print("[步骤3.1] 生成默认量化数据（随机数据）")
            dataset_file = None
            
            # 生成代表性数据集（用于统计张量分布）
            # YOLOv11 输入：[1, 3, 640, 640]，float32
            # 生成100张随机图像作为量化数据集
            num_samples = 100
            quant_data_dir = os.path.join(project_root, 'data', 'quantization_dataset')
            os.makedirs(quant_data_dir, exist_ok=True)
            
            print(f"[步骤3.1] 生成 {num_samples} 张随机图像作为量化数据集")
            for i in range(num_samples):
                # 生成随机图像数据（模拟实际输入范围）
                # YOLO 模型输入通常在 0-255 或归一化后的 0-1
                random_img = np.random.randint(0, 256, (1, 3, input_size, input_size), dtype=np.uint8)
                
                # 保存为临时文件
                img_file = os.path.join(quant_data_dir, f'sample_{i}.bin')
                random_img.tofile(img_file)
            
            # 创建数据集列表文件
            dataset_file = os.path.join(quant_data_dir, 'dataset.txt')
            with open(dataset_file, 'w') as f:
                for i in range(num_samples):
                    img_file = os.path.join(quant_data_dir, f'sample_{i}.bin')
                    f.write(img_file + '\n')
            
            print(f"[步骤3.1] 数据集已生成: {dataset_file}")
        
        # 执行量化构建
        print("[步骤3.2] 执行量化构建")
        ret = rknn.build(
            do_quantization=True,
            dataset=dataset_file,
            # 量化配置
            quantized_dtype='asymmetric_quantized-u8',  # 量化类型：asymmetric_quantized-u8（INT8）
            quantized_algorithm='normal',  # 量化算法：normal（默认）
        )
        
        if ret != 0:
            print(f"[模型转换] 错误：量化构建失败，错误码: {ret}")
            rknn.release()
            return False
        
        print("[步骤3] 构建完成（INT8量化模式）")
        print()
    
    # 5. 导出 RKNN 模型
    print("[步骤4] 导出 RKNN 模型")
    ret = rknn.export_rknn(output_path)
    
    if ret != 0:
        print(f"[模型转换] 错误：导出 RKNN 模型失败，错误码: {ret}")
        rknn.release()
        return False
    
    print(f"[步骤4] 导出完成: {output_path}")
    print()
    
    # 6. 验证模型（可选）
    print("[步骤5] 验证模型")
    
    # 初始化 runtime（模拟推理）
    print("[步骤5.1] 初始化 runtime 环境")
    ret = rknn.init_runtime(target=target)
    
    if ret != 0:
        print(f"[模型转换] 警告：初始化 runtime 失败，错误码: {ret}")
        print("[模型转换] 注意：这通常是因为在 PC 上运行，需要在目标平台上初始化")
    else:
        print("[步骤5.1] Runtime 初始化完成")
        
        # 执行一次推理测试
        print("[步骤5.2] 执行推理测试")
        test_input = np.random.randint(0, 256, (1, 3, input_size, input_size), dtype=np.uint8)
        outputs = rknn.inference(inputs=[test_input])
        
        if outputs:
            print(f"[步骤5.2] 推理测试成功，输出形状: {outputs[0].shape}")
        else:
            print("[步骤5.2] 警告：推理测试失败")
    
    print()
    
    # 清理资源
    rknn.release()
    
    print("="*70)
    print("模型转换完成")
    print("="*70)
    print(f"输出文件: {output_path}")
    print(f"模型大小: {os.path.getsize(output_path) / (1024*1024):.2f} MB")
    print()
    
    return True


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='ONNX 转 RKNN 模型转换脚本')
    
    parser.add_argument('--onnx', required=True, help='ONNX 模型文件路径')
    parser.add_argument('--output', help='输出的 RKNN 模型文件路径（默认：同名.rknn）')
    parser.add_argument('--target', default='rk3588', 
                        choices=['rk3588', 'rk3566', 'rk3568', 'rv1103', 'rv1106'],
                        help='目标平台（默认：rk3588）')
    parser.add_argument('--quantize', type=lambda x: x.lower() == 'true', default=True,
                        help='是否量化（True=INT8量化，False=FP16，默认：True）')
    parser.add_argument('--dataset', help='量化数据集路径（可选）')
    parser.add_argument('--input-size', type=int, default=640,
                        help='输入尺寸（默认：640）')
    parser.add_argument('--no-quantization', action='store_true',
                        help='跳过量化步骤（直接转换为FP16）')
    
    args = parser.parse_args()
    
    # 执行转换
    success = convert_onnx_to_rknn(
        onnx_path=args.onnx,
        output_path=args.output,
        target=args.target,
        quantize=args.quantize,
        dataset_path=args.dataset,
        input_size=args.input_size,
        do_quantization=not args.no_quantization
    )
    
    if success:
        print("✅ 模型转换成功")
        sys.exit(0)
    else:
        print("❌ 模型转换失败")
        sys.exit(1)


if __name__ == '__main__':
    main()