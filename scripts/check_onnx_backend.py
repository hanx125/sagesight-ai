"""
检查 ONNX Runtime 推理后端

用于诊断：
- CUDA 是否可用
- 是否使用了 GPU 加速
- 推理性能如何
"""

import onnxruntime as ort
import numpy as np
import time


def check_onnx_providers():
    """检查 ONNX Runtime 可用的 Execution Providers"""
    print("\n" + "="*70)
    print("ONNX Runtime Execution Providers 检查")
    print("="*70)
    
    # 获取所有可用的 Execution Providers
    available_providers = ort.get_available_providers()
    
    print(f"\n可用的 Execution Providers:")
    for provider in available_providers:
        print(f"  - {provider}")
    
    print(f"\n推荐的 Execution Providers（按性能排序）:")
    print(f"  1. CUDAExecutionProvider (NVIDIA GPU)")
    print(f"  2. TensorrtExecutionProvider (TensorRT)")
    print(f"  3. DmlExecutionProvider (DirectML - Windows)")
    print(f"  4. CPUExecutionProvider (CPU)")
    
    # 检查是否有 GPU 加速
    has_cuda = 'CUDAExecutionProvider' in available_providers
    has_tensorrt = 'TensorrtExecutionProvider' in available_providers
    has_dml = 'DmlExecutionProvider' in available_providers
    
    print(f"\nGPU 加速状态:")
    print(f"  CUDA: {'✅ 可用' if has_cuda else '❌ 不可用'}")
    print(f"  TensorRT: {'✅ 可用' if has_tensorrt else '❌ 不可用'}")
    print(f"  DirectML: {'✅ 可用' if has_dml else '❌ 不可用'}")
    
    if not (has_cuda or has_tensorrt or has_dml):
        print("\n⚠️  警告: 没有 GPU 加速，推理将使用 CPU（性能较低）")
        print("   解决方案:")
        print("   - 安装 onnxruntime-gpu: pip install onnxruntime-gpu")
        print("   - 或安装 CUDA: https://developer.nvidia.com/cuda-downloads")
    
    return available_providers


def benchmark_inference(
    model_path: str = "models/yolov11n.onnx",
    provider: str = "CPUExecutionProvider",
    warmup_runs: int = 5,
    benchmark_runs: int = 20
):
    """基准测试推理性能"""
    print("\n" + "="*70)
    print(f"推理性能基准测试 ({provider})")
    print("="*70)
    
    try:
        # 创建推理会话
        session = ort.InferenceSession(
            model_path,
            providers=[provider]
        )
        
        # 获取输入信息
        input_name = session.get_inputs()[0].name
        input_shape = session.get_inputs()[0].shape
        print(f"\n模型输入: {input_name}, 形状: {input_shape}")
        
        # 创建随机输入
        batch_size = 1
        channels = 3
        height = input_shape[2] if input_shape[2] != -1 else 640
        width = input_shape[3] if input_shape[3] != -1 else 640
        
        input_data = np.random.rand(batch_size, channels, height, width).astype(np.float32)
        
        # 预热
        print(f"\n预热中（{warmup_runs} 次）...")
        for _ in range(warmup_runs):
            session.run(None, {input_name: input_data})
        
        # 基准测试
        print(f"基准测试中（{benchmark_runs} 次）...")
        start_time = time.time()
        
        for _ in range(benchmark_runs):
            session.run(None, {input_name: input_data})
        
        elapsed = time.time() - start_time
        
        # 计算性能指标
        avg_time_ms = (elapsed / benchmark_runs) * 1000
        fps = benchmark_runs / elapsed
        
        print(f"\n性能结果:")
        print(f"  平均推理时间: {avg_time_ms:.2f} ms")
        print(f"  FPS: {fps:.2f}")
        print(f"  总耗时: {elapsed:.2f} s（{benchmark_runs} 次推理）")
        
        return avg_time_ms, fps
        
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        return None, None


def compare_backends(model_path: str = "models/yolov11n.onnx"):
    """对比不同后端的性能"""
    print("\n" + "="*70)
    print("不同后端性能对比")
    print("="*70)
    
    available_providers = ort.get_available_providers()
    
    results = {}
    
    # 测试 CPU
    if 'CPUExecutionProvider' in available_providers:
        print("\n测试 CPU...")
        avg_time, fps = benchmark_inference(model_path, "CPUExecutionProvider")
        if avg_time:
            results['CPU'] = {'avg_time': avg_time, 'fps': fps}
    
    # 测试 CUDA
    if 'CUDAExecutionProvider' in available_providers:
        print("\n测试 CUDA...")
        avg_time, fps = benchmark_inference(model_path, "CUDAExecutionProvider")
        if avg_time:
            results['CUDA'] = {'avg_time': avg_time, 'fps': fps}
    
    # 测试 TensorRT
    if 'TensorrtExecutionProvider' in available_providers:
        print("\n测试 TensorRT...")
        avg_time, fps = benchmark_inference(model_path, "TensorrtExecutionProvider")
        if avg_time:
            results['TensorRT'] = {'avg_time': avg_time, 'fps': fps}
    
    # 输出对比结果
    if results:
        print("\n" + "="*70)
        print("性能对比结果")
        print("="*70)
        print(f"\n{'后端':<15} {'平均时间 (ms)':<20} {'FPS':<10}")
        print("-" * 50)
        
        for backend, metrics in results.items():
            print(f"{backend:<15} {metrics['avg_time']:<20.2f} {metrics['fps']:<10.2f}")
        
        # 计算加速比
        if 'CPU' in results and len(results) > 1:
            cpu_fps = results['CPU']['fps']
            print("\n加速比:")
            for backend, metrics in results.items():
                if backend != 'CPU':
                    speedup = metrics['fps'] / cpu_fps
                    print(f"  {backend} vs CPU: {speedup:.2f}x")
    
    return results


if __name__ == "__main__":
    # 1. 检查可用的 Execution Providers
    check_onnx_providers()
    
    # 2. 基准测试（需要模型文件）
    model_path = "models/yolov11n.onnx"
    
    import os
    if os.path.exists(model_path):
        print("\n" + "="*70)
        print("开始基准测试...")
        print("="*70)
        
        # 对比不同后端
        compare_backends(model_path)
    else:
        print(f"\n⚠️  模型文件不存在: {model_path}")
        print("   请将模型文件放在 models/ 目录下")
    
    print("\n" + "="*70)
    print("检查完成")
    print("="*70)