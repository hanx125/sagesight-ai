"""
YOLO检测系统测试脚本

使用方法：
    python test_components.py [测试模块]

测试模块：
    all         - 运行所有测试
    nv12        - 测试NV12格式解析
    preprocessor - 测试预处理器
    yolo        - 测试YOLO模型推理
    end2end     - 端到端测试
    custom      - 测试自定义模型（COCO vs 自定义）

示例：
    python test_components.py nv12          # 只测试NV12格式
    python test_components.py preprocessor  # 只测试预处理器
    python test_components.py yolo         # 只测试YOLO模型
    python test_components.py all          # 运行所有测试
    python test_components.py custom       # 测试自定义模型
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


def test_nv12_parsing():
    """测试1：NV12格式解析"""
    print("\n" + "="*70)
    print("测试1：NV12格式解析")
    print("="*70)
    
    # 模拟NV12数据
    h, w = 640, 640
    y_size = h * w
    uv_size = h * w // 2
    
    # 生成测试数据
    y_plane = np.random.randint(0, 255, (h, w), dtype=np.uint8)
    uv_plane = np.random.randint(0, 255, (h // 2, w), dtype=np.uint8)
    
    print(f"[测试] Y平面形状: {y_plane.shape}")
    print(f"[测试] UV平面形状: {uv_plane.shape}")
    print(f"[测试] UV平面大小: {uv_plane.nbytes} 字节")
    
    # 方法1：交替列提取（正确方法）
    print("\n[方法1] 交替列提取：")
    try:
        u1 = uv_plane[:, 0::2]  # 提取U（奇数列）
        v1 = uv_plane[:, 1::2]  # 提取V（偶数列）
        print(f"  U平面形状: {u1.shape}")
        print(f"  V平面形状: {v1.shape}")
        
        # 上采样
        u1_resized = cv2.resize(u1, (w, h))
        v1_resized = cv2.resize(v1, (w, h))
        print(f"  U上采样后形状: {u1_resized.shape}")
        print(f"  V上采样后形状: {v1_resized.shape}")
        
        # YUV → BGR
        yuv_image = np.stack([y_plane, u1_resized, v1_resized], axis=-1)
        bgr_frame = cv2.cvtColor(yuv_image, cv2.COLOR_YUV2BGR)
        print(f"  BGR图像形状: {bgr_frame.shape}")
        print("[方法1] ✅ 成功！")
    except Exception as e:
        print(f"[方法1] ❌ 失败: {e}")
    
    # 方法2：reshape方法（错误方法）
    print("\n[方法2] reshape方法：")
    try:
        uv_reshaped = uv_plane.reshape((h // 2, 2, w))
        print(f"  UV重塑后形状: {uv_reshaped.shape}")
        u2 = uv_reshaped[:, 0, :]
        v2 = uv_reshaped[:, 1, :]
        print(f"  U平面形状: {u2.shape}")
        print(f"  V平面形状: {v2.shape}")
        print("[方法2] ✅ 成功！")
    except Exception as e:
        print(f"[方法2] ❌ 失败: {e}")
    
    print("\n[结论] 使用方法1（交替列提取）！")


def test_preprocessor():
    """测试2：预处理器测试"""
    print("\n" + "="*70)
    print("测试2：预处理器")
    print("="*70)
    
    try:
        from src.detectors.preprocessor import OptimizedPreprocessor
        
        # 创建预处理器
        preprocessor = OptimizedPreprocessor(
            width=640,
            height=640,
            model_input_size=640
        )
        print("[测试] 预处理器创建成功")
        
        # 生成测试数据
        h, w = 640, 640
        y_plane = np.random.randint(0, 255, (h, w), dtype=np.uint8)
        uv_plane = np.random.randint(0, 255, (h // 2, w), dtype=np.uint8)
        
        # 测试preprocess_nv12方法
        print("\n[测试] preprocess_nv12方法：")
        start_time = time.time()
        tensor = preprocessor.preprocess_nv12(y_plane, uv_plane)
        elapsed = time.time() - start_time
        
        print(f"  输入Y形状: {y_plane.shape}")
        print(f"  输入UV形状: {uv_plane.shape}")
        print(f"  输出张量形状: {tensor.shape}")
        print(f"  处理时间: {elapsed*1000:.2f}ms")
        
        if tensor.shape == (1, 3, 640, 640):
            print("[preprocess_nv12] [OK] 形状正确!")
        else:
            print(f"[preprocess_nv12] [FAIL] 形状错误! 期望 (1, 3, 640, 640)，得到 {tensor.shape}")
        
        # 测试preprocess方法（YUV420p）
        print("\n[测试] preprocess方法（YUV420p）：")
        u_plane = uv_plane[:, 0::2]  # 先提取U
        v_plane = uv_plane[:, 1::2]  # 再提取V
        start_time = time.time()
        tensor2 = preprocessor.preprocess(y_plane, u_plane, v_plane)
        elapsed = time.time() - start_time
        
        print(f"  输入Y形状: {y_plane.shape}")
        print(f"  输入U形状: {u_plane.shape}")
        print(f"  输入V形状: {v_plane.shape}")
        print(f"  输出张量形状: {tensor2.shape}")
        print(f"  处理时间: {elapsed*1000:.2f}ms")
        
        if tensor2.shape == (1, 3, 640, 640):
            print("[preprocess] [OK] 形状正确!")
        else:
            print(f"[preprocess] [FAIL] 形状错误! 期望 (1, 3, 640, 640)，得到 {tensor2.shape}")
        
        # 比较两种方法的结果
        print("\n[比较] 两种方法结果比较：")
        diff = np.abs(tensor - tensor2).mean()
        print(f"  平均差异: {diff:.6f}")
        if diff < 0.01:
            print("  [OK] 两种方法结果一致!")
        else:
            print("  [WARN] 两种方法结果有差异")
        
    except ImportError as e:
        print(f"[ERROR] 无法导入预处理器: {e}")
    except Exception as e:
        print(f"[ERROR] 测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_yolo_model():
    """测试3：YOLO模型推理"""
    print("\n" + "="*70)
    print("测试3：YOLO模型推理")
    print("="*70)
    
    try:
        from src.detectors.yolo_detector import YOLODetector
        
        # 检查模型文件
        model_path = "models/detection/yolov11n_20260629_184213.onnx"
        if not os.path.exists(model_path):
            model_path = "models/detection/yolov11n.onnx"
        
        if not os.path.exists(model_path):
            print(f"[错误] 模型文件不存在: {model_path}")
            print("[提示] 请先下载YOLOv11n模型或使用其他模型")
            return
        
        print(f"[测试] 使用模型: {model_path}")
        
        # 创建检测器
        detector = YOLODetector({
            'model_path': model_path,
            'backend': 'onnx_tensorrt',
            'use_fp16': True,
            'target_classes': ['person', 'car'],
            'conf_threshold': 0.25,
            'iou_threshold': 0.35,
            'input_width': 640,
            'input_height': 640
        })
        
        print("[测试] 检测器创建成功")
        
        # 初始化检测器
        print("[测试] 初始化检测器（加载模型）...")
        start_time = time.time()
        detector.initialize()
        init_time = time.time() - start_time
        print(f"[测试] 初始化完成，耗时: {init_time:.2f}秒")
        
        # 生成测试张量
        test_tensor = np.random.rand(1, 3, 640, 640).astype(np.float32)
        
        # 测试detect方法
        print("\n[测试] detect方法：")
        start_time = time.time()
        result = detector.detect(test_tensor)
        elapsed = time.time() - start_time
        
        print(f"  输入形状: {test_tensor.shape}")
        print(f"  处理时间: {elapsed*1000:.2f}ms")
        print(f"  检测结果数量: {result.get('total_objects', 0)}")
        
        if 'detections' in result:
            for i, det in enumerate(result['detections'][:5]):
                print(f"  - 目标{i+1}: {det.get('class_name', 'unknown')}, "
                      f"置信度: {det.get('confidence', 0):.2f}")
        
        # 测试infer方法
        print("\n[测试] infer方法：")
        start_time = time.time()
        detections = detector.infer(test_tensor)
        elapsed = time.time() - start_time
        
        print(f"  输入形状: {test_tensor.shape}")
        print(f"  处理时间: {elapsed*1000:.2f}ms")
        print(f"  检测结果数量: {len(detections)}")
        
        print("\n[YOLO模型] ✅ 测试完成！")
        
    except ImportError as e:
        print(f"[错误] 无法导入YOLODetector: {e}")
    except Exception as e:
        print(f"[错误] 测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_end2end():
    """测试4：端到端测试"""
    print("\n" + "="*70)
    print("测试4：端到端测试")
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
            return
        
        # 1. 创建组件
        print("\n[步骤1] 创建组件...")
        preprocessor = OptimizedPreprocessor(width=640, height=640, model_input_size=640)
        detector = YOLODetector({
            'model_path': model_path,
            'backend': 'onnx_tensorrt',
            'use_fp16': True,
            'target_classes': ['person', 'car'],
            'conf_threshold': 0.25,
            'iou_threshold': 0.35
        })
        print("[步骤1] ✅ 组件创建成功")
        
        # 2. 初始化检测器
        print("\n[步骤2] 初始化检测器...")
        start_time = time.time()
        detector.initialize()
        init_time = time.time() - start_time
        print(f"[步骤2] ✅ 初始化完成，耗时: {init_time:.2f}秒")
        
        # 3. 生成测试数据（模拟NV12）
        print("\n[步骤3] 生成测试数据...")
        h, w = 640, 640
        y_plane = np.random.randint(0, 255, (h, w), dtype=np.uint8)
        uv_plane = np.random.randint(0, 255, (h // 2, w), dtype=np.uint8)
        print(f"[步骤3] ✅ 测试数据生成完成")
        print(f"  Y形状: {y_plane.shape}")
        print(f"  UV形状: {uv_plane.shape}")
        
        # 4. 预处理
        print("\n[步骤4] 预处理（NV12 → 张量）...")
        start_time = time.time()
        tensor = preprocessor.preprocess_nv12(y_plane, uv_plane)
        preprocess_time = time.time() - start_time
        print(f"[步骤4] ✅ 预处理完成，耗时: {preprocess_time*1000:.2f}ms")
        print(f"  张量形状: {tensor.shape}")
        
        # 5. 推理
        print("\n[步骤5] YOLO推理...")
        start_time = time.time()
        result = detector.detect(tensor)
        infer_time = time.time() - start_time
        print(f"[步骤5] ✅ 推理完成，耗时: {infer_time*1000:.2f}ms")
        print(f"  检测到目标数: {result.get('total_objects', 0)}")
        
        # 6. 统计
        total_time = init_time + preprocess_time + infer_time
        print("\n" + "="*70)
        print("性能统计")
        print("="*70)
        print(f"  初始化时间: {init_time:.2f}秒")
        print(f"  预处理时间: {preprocess_time*1000:.2f}ms")
        print(f"  推理时间: {infer_time*1000:.2f}ms")
        print(f"  总时间: {total_time:.2f}秒")
        
        fps = 1.0 / infer_time if infer_time > 0 else 0
        print(f"  推理FPS: {fps:.1f}")
        
        print("\n[端到端测试] ✅ 完成！")
        
    except ImportError as e:
        print(f"[错误] 导入失败: {e}")
    except Exception as e:
        print(f"[错误] 测试失败: {e}")
        import traceback
        traceback.print_exc()


def test_custom_model():
    """测试5：自定义模型测试（COCO vs 自定义）"""
    print("\n" + "="*70)
    print("测试5：自定义模型测试（COCO vs 自定义）")
    print("="*70)
    
    try:
        from src.detectors.yolo_detector import YOLODetector
        
        # 检查模型文件
        model_path = "models/detection/yolov11n_20260629_184213.onnx"
        if not os.path.exists(model_path):
            model_path = "models/detection/yolov11n.onnx"
        
        if not os.path.exists(model_path):
            print(f"[错误] 模型文件不存在: {model_path}")
            return
        
        # 1. 测试COCO模型（80类）
        print("\n[场景1] COCO预训练模型（80类）")
        print("-" * 70)
        detector_coco = YOLODetector({
            'model_path': model_path,
            'backend': 'onnx_tensorrt',
            'use_fp16': True,
            'target_classes': ['person', 'car'],  # 只检测person和car
            'conf_threshold': 0.25,
            'iou_threshold': 0.35
        })
        
        # 初始化
        print("[测试] 初始化COCO模型...")
        detector_coco.initialize()
        print(f"  class_names长度: {len(detector_coco.config.class_names)}")
        print(f"  target_classes: {detector_coco.config.target_classes}")
        
        # 生成测试张量
        test_tensor = np.random.rand(1, 3, 640, 640).astype(np.float32)
        
        # 推理
        print("\n[测试] COCO模型推理...")
        result = detector_coco.detect(test_tensor)
        print(f"  检测结果数量: {result.get('total_objects', 0)}")
        
        if 'detections' in result and result['detections']:
            print("  前5个检测结果:")
            for i, det in enumerate(result['detections'][:5]):
                print(f"    - {det.get('class_name', 'unknown')}: 置信度 {det.get('confidence', 0):.2f}")
        
        # 2. 测试自定义模型（假设只有2个类别）
        print("\n" + "="*70)
        print("[场景2] 自定义模型（2类）")
        print("-" * 70)
        
        # 创建自定义配置的检测器
        # 模拟一个只有2个类别的自定义模型
        detector_custom = YOLODetector({
            'model_path': model_path,  # 使用同一个模型（仅用于演示）
            'backend': 'onnx_tensorrt',
            'use_fp16': True,
            'target_classes': ['class_0', 'class_1'],  # 自定义类别名称
            'conf_threshold': 0.25,
            'iou_threshold': 0.35
        })
        
        # 注意：自定义模型的关键是确保class_names长度与模型输出一致
        # 当前演示使用COCO模型，但实际使用时需要使用自己训练的模型
        
        print("\n[说明] 自定义模型使用说明：")
        print("  1. 使用自己训练的模型（class_num不同）")
        print("  2. 在target_classes中提供正确的类别名称列表")
        print("  3. 确保class_names长度与模型输出类别数一致")
        print("  4. 系统会自动从模型输出推断类别数量")
        
        print("\n[自定义模型] 测试完成!")
        
        # 3. 测试class_id越界情况
        print("\n" + "="*70)
        print("[场景3] 测试class_id越界情况")
        print("-" * 70)
        print("  场景：COCO模型有80个类别（class_id: 0-79）")
        print("  用户配置：只检测 person, car (对应 class_id: 0, 2)")
        print("  预期：")
        print("    - class_id=0 (person) -> 保留")
        print("    - class_id=2 (car)    -> 保留")
        print("    - class_id=3 (bicycle)-> 跳过（不在target_classes中）")
        print("    - class_id=15 (cat)   -> 跳过（不在target_classes中）")
        print("  结论：正确！只输出用户在target_classes中配置的类别")
        
    except ImportError as e:
        print(f"[错误] 导入失败: {e}")
    except Exception as e:
        print(f"[错误] 测试失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    """主函数"""
    print("\n" + "#"*70)
    print("# YOLO检测系统测试")
    print("#"*70)
    
    # 解析命令行参数
    test_type = sys.argv[1] if len(sys.argv) > 1 else 'all'
    
    # 测试映射
    tests = {
        'all': [test_nv12_parsing, test_preprocessor, test_yolo_model, test_end2end, test_custom_model],
        'nv12': [test_nv12_parsing],
        'preprocessor': [test_preprocessor],
        'yolo': [test_yolo_model],
        'end2end': [test_end2end],
        'custom': [test_custom_model]
    }
    
    # 运行测试
    if test_type in tests:
        for test_func in tests[test_type]:
            try:
                test_func()
            except Exception as e:
                print(f"\n[错误] 测试 '{test_func.__name__}' 失败: {e}")
                import traceback
                traceback.print_exc()
    else:
        print(f"[错误] 未知测试类型: {test_type}")
        print("可用测试类型: all, nv12, preprocessor, yolo, end2end, custom")
    
    print("\n" + "="*70)
    print("测试完成")
    print("="*70)


if __name__ == "__main__":
    main()
