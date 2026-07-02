"""
性能分析测试脚本

测试每个环节的耗时，帮助定位FPS低的原因。

使用方法：
    python test_performance.py
"""

import sys
import os
import time
import numpy as np
import cv2

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 设置输出编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def test_nv12_to_bgr():
    """测试1：NV12到BGR转换"""
    print("\n" + "="*70)
    print("测试1：NV12到BGR转换")
    print("="*70)
    
    # 生成模拟NV12数据
    h, w = 640, 640
    y_plane = np.random.randint(0, 255, (h, w), dtype=np.uint8)
    uv_plane = np.random.randint(0, 255, (h // 2, w), dtype=np.uint8)
    
    print(f"[测试] 输入：Y={y_plane.shape}, UV={uv_plane.shape}")
    
    # 测试1：交替列提取方法
    print("\n[方法1] 交替列提取（正确方法）：")
    times = []
    for i in range(100):
        start = time.time()
        
        # NV12 → YUV420p
        u = uv_plane[:, 0::2]  # (320, 320)
        v = uv_plane[:, 1::2]  # (320, 320)
        
        # 上采样
        u_resized = cv2.resize(u, (w, h))
        v_resized = cv2.resize(v, (w, h))
        
        # YUV → BGR
        yuv_image = np.stack([y_plane, u_resized, v_resized], axis=-1)
        bgr_frame = cv2.cvtColor(yuv_image, cv2.COLOR_YUV2BGR)
        
        elapsed = time.time() - start
        times.append(elapsed * 1000)  # 转换为毫秒
    
    avg_time = np.mean(times)
    print(f"  平均耗时: {avg_time:.2f}ms")
    print(f"  最小耗时: {np.min(times):.2f}ms")
    print(f"  最大耗时: {np.max(times):.2f}ms")
    print(f"  FPS: {1000/avg_time:.1f}")
    
    return avg_time


def test_rgb_to_tensor():
    """测试2：RGB到张量转换"""
    print("\n" + "="*70)
    print("测试2：RGB到张量转换")
    print("="*70)
    
    # 生成模拟BGR数据
    h, w = 640, 640
    bgr_frame = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    
    print(f"[测试] 输入：BGR={bgr_frame.shape}")
    
    times = []
    for i in range(100):
        start = time.time()
        
        # BGR → RGB
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        
        # resize到模型输入尺寸
        resized = cv2.resize(rgb, (640, 640))
        
        # HWC → CHW
        transposed = resized.transpose(2, 0, 1)  # (3, 640, 640)
        
        # 归一化
        normalized = transposed.astype(np.float32) / 255.0
        
        # 添加batch维度
        batched = np.expand_dims(normalized, axis=0)
        
        elapsed = time.time() - start
        times.append(elapsed * 1000)
    
    avg_time = np.mean(times)
    print(f"  平均耗时: {avg_time:.2f}ms")
    print(f"  FPS: {1000/avg_time:.1f}")
    
    return avg_time


def test_yolo_inference():
    """测试3：YOLO推理"""
    print("\n" + "="*70)
    print("测试3：YOLO推理")
    print("="*70)
    
    try:
        from src.detectors.yolo_detector import YOLODetector
        
        # 检查模型文件
        model_path = "models/detection/yolov11n_20260629_184213.onnx"
        if not os.path.exists(model_path):
            model_path = "models/detection/yolov11n.onnx"
        
        if not os.path.exists(model_path):
            print(f"[错误] 模型文件不存在: {model_path}")
            return None
        
        print(f"[测试] 使用模型: {model_path}")
        
        # 创建检测器
        detector = YOLODetector({
            'model_path': model_path,
            'backend': 'onnx_tensorrt',
            'use_fp16': True,
            'target_classes': ['person', 'car'],
            'conf_threshold': 0.25,
            'iou_threshold': 0.35
        })
        
        print("[测试] 初始化检测器...")
        detector.initialize()
        
        # 生成测试张量
        test_tensor = np.random.rand(1, 3, 640, 640).astype(np.float32)
        
        print("\n[测试] 预热推理（5次）：")
        for i in range(5):
            _ = detector.infer(test_tensor)
        
        print("[测试] 正式推理（100次）：")
        times = []
        for i in range(100):
            start = time.time()
            detections = detector.infer(test_tensor)
            elapsed = time.time() - start
            times.append(elapsed * 1000)
        
        avg_time = np.mean(times)
        print(f"  平均耗时: {avg_time:.2f}ms")
        print(f"  最小耗时: {np.min(times):.2f}ms")
        print(f"  最大耗时: {np.max(times):.2f}ms")
        print(f"  FPS: {1000/avg_time:.1f}")
        
        # 测试detect方法
        print("\n[测试] detect方法（100次）：")
        times = []
        for i in range(100):
            start = time.time()
            result = detector.detect(test_tensor)
            elapsed = time.time() - start
            times.append(elapsed * 1000)
        
        avg_time = np.mean(times)
        print(f"  平均耗时: {avg_time:.2f}ms")
        print(f"  FPS: {1000/avg_time:.1f}")
        
        return avg_time
        
    except ImportError as e:
        print(f"[错误] 导入失败: {e}")
        return None
    except Exception as e:
        print(f"[错误] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_preprocessor():
    """测试4：预处理器"""
    print("\n" + "="*70)
    print("测试4：预处理器（NV12 → Tensor）")
    print("="*70)
    
    try:
        from src.detectors.preprocessor import OptimizedPreprocessor
        
        # 创建预处理器
        preprocessor = OptimizedPreprocessor(
            width=640,
            height=640,
            model_input_size=640
        )
        
        # 生成模拟NV12数据
        h, w = 640, 640
        y_plane = np.random.randint(0, 255, (h, w), dtype=np.uint8)
        uv_plane = np.random.randint(0, 255, (h // 2, w), dtype=np.uint8)
        
        print(f"[测试] 输入：Y={y_plane.shape}, UV={uv_plane.shape}")
        
        print("\n[测试] preprocess_nv12方法（100次）：")
        times = []
        for i in range(100):
            start = time.time()
            tensor = preprocessor.preprocess_nv12(y_plane, uv_plane)
            elapsed = time.time() - start
            times.append(elapsed * 1000)
        
        avg_time = np.mean(times)
        print(f"  平均耗时: {avg_time:.2f}ms")
        print(f"  最小耗时: {np.min(times):.2f}ms")
        print(f"  最大耗时: {np.max(times):.2f}ms")
        print(f"  FPS: {1000/avg_time:.1f}")
        
        return avg_time
        
    except ImportError as e:
        print(f"[错误] 导入失败: {e}")
        return None
    except Exception as e:
        print(f"[错误] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_end_to_end():
    """测试5：端到端性能"""
    print("\n" + "="*70)
    print("测试5：端到端性能")
    print("="*70)
    
    try:
        from src.detectors.preprocessor import OptimizedPreprocessor
        from src.detectors.yolo_detector import YOLODetector
        
        # 检查模型文件
        model_path = "models/detection/yolov11n_20260629_184213.onnx"
        if not os.path.exists(model_path):
            model_path = "models/detection/yolov11n.onnx"
        
        if not os.path.exists(model_path):
            print(f"[错误] 模型文件不存在: {model_path}")
            return None
        
        # 创建组件
        preprocessor = OptimizedPreprocessor(width=640, height=640, model_input_size=640)
        detector = YOLODetector({
            'model_path': model_path,
            'backend': 'onnx_tensorrt',
            'use_fp16': True,
            'target_classes': ['person', 'car'],
            'conf_threshold': 0.25,
            'iou_threshold': 0.35
        })
        
        print("[测试] 初始化检测器...")
        detector.initialize()
        
        # 生成模拟NV12数据
        h, w = 640, 640
        y_plane = np.random.randint(0, 255, (h, w), dtype=np.uint8)
        uv_plane = np.random.randint(0, 255, (h // 2, w), dtype=np.uint8)
        
        print("\n[测试] 端到端流程（100次）：")
        
        preprocess_times = []
        infer_times = []
        total_times = []
        
        for i in range(100):
            # 预处理
            start = time.time()
            tensor = preprocessor.preprocess_nv12(y_plane, uv_plane)
            preprocess_time = time.time() - start
            preprocess_times.append(preprocess_time * 1000)
            
            # 推理
            start = time.time()
            result = detector.detect(tensor)
            infer_time = time.time() - start
            infer_times.append(infer_time * 1000)
            
            # 总时间
            total_time = preprocess_time + infer_time
            total_times.append(total_time * 1000)
        
        print(f"\n  预处理平均耗时: {np.mean(preprocess_times):.2f}ms")
        print(f"  推理平均耗时: {np.mean(infer_times):.2f}ms")
        print(f"  总平均耗时: {np.mean(total_times):.2f}ms")
        print(f"  理论最大FPS: {1000/np.mean(total_times):.1f}")
        
        print(f"\n  预处理占比: {np.mean(preprocess_times)/np.mean(total_times)*100:.1f}%")
        print(f"  推理占比: {np.mean(infer_times)/np.mean(total_times)*100:.1f}%")
        
        return np.mean(total_times)
        
    except ImportError as e:
        print(f"[错误] 导入失败: {e}")
        return None
    except Exception as e:
        print(f"[错误] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """主函数"""
    print("\n" + "#"*70)
    print("# 性能分析测试")
    print("#"*70)
    
    results = {}
    
    # 测试1：NV12到BGR
    results['nv12_to_bgr'] = test_nv12_to_bgr()
    
    # 测试2：RGB到张量
    results['rgb_to_tensor'] = test_rgb_to_tensor()
    
    # 测试3：YOLO推理
    results['yolo_inference'] = test_yolo_inference()
    
    # 测试4：预处理器
    results['preprocessor'] = test_preprocessor()
    
    # 测试5：端到端
    results['end_to_end'] = test_end_to_end()
    
    # 总结
    print("\n" + "="*70)
    print("性能总结")
    print("="*70)
    
    total_time = 0
    for key, value in results.items():
        if value is not None:
            print(f"{key:20s}: {value:6.2f}ms")
            total_time += value
    
    if total_time > 0:
        print("-" * 70)
        print(f"{'总计':20s}: {total_time:6.2f}ms")
        print(f"{'理论最大FPS':20s}: {1000/total_time:6.1f}")
        
        print("\n" + "="*70)
        print("优化建议")
        print("="*70)
        
        if results.get('preprocessor', 0) > 30:
            print("[高] 预处理耗时过高（>30ms），建议：")
            print("     1. 降低输入分辨率（640→320）")
            print("     2. 使用CUDA加速转换")
            print("     3. 优化NV12到RGB的转换流程")
        
        if results.get('yolo_inference', 0) > 50:
            print("[高] YOLO推理耗时过高（>50ms），建议：")
            print("     1. 使用TensorRT Engine缓存")
            print("     2. 降低模型输入分辨率")
            print("     3. 使用更小的模型（yolov8n → yolov11n）")
        
        if results.get('nv12_to_bgr', 0) > 20:
            print("[高] NV12转换耗时过高（>20ms），建议：")
            print("     1. 使用CUDA加速")
            print("     2. 跳过BGR转换（如果有其他用途）")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    main()
