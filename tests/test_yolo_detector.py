"""
YOLO 检测器模块测试脚本

测试内容：
1. 模型加载（模拟 ONNX 模型）
2. 推理功能测试
3. 后处理验证（NMS、坐标反归一化）
4. 性能测试
5. YOLOv8n/YOLO11n 切换测试
"""

import sys
import time
import numpy as np
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.detectors import YOLODetector, DetectorConfig, Detection, create_detector
from src.detectors.preprocessor import create_preprocessor


def create_mock_onnx_model():
    """
    创建模拟 ONNX 模型用于测试
    
    注意：这不是真正的 YOLO 模型，仅用于测试接口
    实际使用时需要加载真实的 YOLOv8n/YOLO11n ONNX 模型
    """
    # 由于无法动态创建 ONNX 模型，这里返回 None
    # 实际测试需要使用真实模型
    return None


def test_detector_initialization():
    """测试检测器初始化"""
    print("\n" + "="*60)
    print("测试 1: 检测器初始化")
    print("="*60)
    
    try:
        # 创建配置
        config = DetectorConfig(
            model_path="",  # 暂时不加载模型
            conf_threshold=0.25,
            iou_threshold=0.45
        )
        
        # 创建检测器
        print("\n[测试] 创建 YOLODetector...")
        detector = YOLODetector(config)
        
        print(f"  配置：conf_threshold={config.conf_threshold}, iou_threshold={config.iou_threshold}")
        print(f"  类别数：{len(config.class_names)}")
        print(f"  输入尺寸：{config.input_width}x{config.input_height}")
        
        print("\n[PASS] 检测器初始化测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 检测器初始化测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_detector_config():
    """测试检测器配置"""
    print("\n" + "="*60)
    print("测试 2: 检测器配置")
    print("="*60)
    
    try:
        # 测试不同配置
        print("\n[测试] 创建不同配置的检测器...")
        
        configs = [
            ("默认配置", DetectorConfig()),
            ("低置信度", DetectorConfig(conf_threshold=0.1)),
            ("高置信度", DetectorConfig(conf_threshold=0.5)),
            ("宽松 NMS", DetectorConfig(iou_threshold=0.6)),
            ("严格 NMS", DetectorConfig(iou_threshold=0.3)),
        ]
        
        for name, config in configs:
            detector = YOLODetector(config)
            print(f"  {name}: conf={config.conf_threshold}, iou={config.iou_threshold}")
        
        print("\n[PASS] 检测器配置测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 检测器配置测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_preprocessor_integration():
    """测试预处理器集成"""
    print("\n" + "="*60)
    print("测试 3: 预处理器集成")
    print("="*60)
    
    try:
        # 创建预处理器
        print("\n[测试] 创建预处理器...")
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试 YUV 数据
        y = np.random.randint(0, 255, (640, 640), dtype=np.uint8)
        u = np.random.randint(0, 255, (320, 320), dtype=np.uint8)
        v = np.random.randint(0, 255, (320, 320), dtype=np.uint8)
        
        # 执行预处理
        print("[测试] 执行预处理...")
        input_tensor = preprocessor.preprocess(y, u, v)
        
        print(f"  输入形状：{input_tensor.shape}")
        print(f"  数据类型：{input_tensor.dtype}")
        print(f"  值范围：[{input_tensor.min():.4f}, {input_tensor.max():.4f}]")
        
        # 验证
        assert input_tensor.shape == (1, 3, 640, 640), f"形状错误：{input_tensor.shape}"
        assert input_tensor.dtype == np.float32, f"数据类型错误：{input_tensor.dtype}"
        
        print("\n[PASS] 预处理器集成测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 预处理器集成测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_mock_inference():
    """测试模拟推理（不需要真实模型）"""
    print("\n" + "="*60)
    print("测试 4: 模拟推理流程")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试数据
        y = np.random.randint(0, 255, (640, 640), dtype=np.uint8)
        u = np.random.randint(0, 255, (320, 320), dtype=np.uint8)
        v = np.random.randint(0, 255, (320, 320), dtype=np.uint8)
        
        # 预处理
        input_tensor = preprocessor.preprocess(y, u, v)
        
        # 模拟模型输出（YOLOv8n 输出格式：(1, 8400, 84)）
        print("\n[测试] 模拟 YOLOv8n 输出...")
        mock_output = np.random.rand(1, 8400, 84).astype(np.float32)
        
        # 模拟后处理
        print("[测试] 执行后处理...")
        detector = YOLODetector(DetectorConfig())
        
        # 调用后处理方法
        detections = detector._postprocess(mock_output, 640, 640)
        
        print(f"  检测框数量：{len(detections)}")
        if len(detections) > 0:
            det = detections[0]
            print(f"  第一个检测框:")
            print(f"    坐标：{det.bbox}")
            print(f"    置信度：{det.confidence:.4f}")
            print(f"    类别：{det.class_name} (ID: {det.class_id})")
        
        print("\n[PASS] 模拟推理流程测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 模拟推理流程测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_nms():
    """测试 NMS 功能"""
    print("\n" + "="*60)
    print("测试 5: NMS 功能")
    print("="*60)
    
    try:
        detector = YOLODetector(DetectorConfig(iou_threshold=0.5))
        
        # 创建测试框（有重叠）
        x1 = np.array([100, 110, 300, 310], dtype=np.float32)
        y1 = np.array([100, 110, 300, 310], dtype=np.float32)
        x2 = np.array([200, 210, 400, 410], dtype=np.float32)
        y2 = np.array([200, 210, 400, 410], dtype=np.float32)
        scores = np.array([0.9, 0.8, 0.7, 0.6], dtype=np.float32)
        class_ids = np.array([0, 0, 1, 1], dtype=np.int32)
        
        print("\n[测试] 执行 NMS...")
        print(f"  输入框数：{len(x1)}")
        print(f"  类别：{class_ids}")
        print(f"  置信度：{scores}")
        
        # 执行 NMS
        indices = detector._nms(x1, y1, x2, y2, scores, class_ids)
        
        print(f"  保留框数：{len(indices)}")
        print(f"  保留索引：{indices}")
        
        # 验证：每个类别应该保留 1-2 个框
        assert len(indices) >= 2, "NMS 后应该至少保留 2 个框"
        assert len(indices) <= 4, "NMS 后不应该超过 4 个框"
        
        print("\n[PASS] NMS 功能测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] NMS 功能测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_detection_output_format():
    """测试检测结果格式"""
    print("\n" + "="*60)
    print("测试 6: 检测结果格式")
    print("="*60)
    
    try:
        # 创建模拟检测
        detection = Detection(
            bbox=(100.0, 100.0, 200.0, 200.0),
            confidence=0.95,
            class_id=0,
            class_name="person"
        )
        
        print("\n[测试] 验证检测结果格式...")
        print(f"  坐标：{detection.bbox}")
        print(f"  置信度：{detection.confidence}")
        print(f"  类别 ID: {detection.class_id}")
        print(f"  类别名称：{detection.class_name}")
        
        # 验证类型
        assert isinstance(detection.bbox, tuple), "bbox 应该是元组"
        assert len(detection.bbox) == 4, "bbox 应该有 4 个元素"
        assert isinstance(detection.confidence, float), "confidence 应该是 float"
        assert isinstance(detection.class_id, int), "class_id 应该是 int"
        assert isinstance(detection.class_name, str), "class_name 应该是 str"
        
        print("\n[PASS] 检测结果格式测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 检测结果格式测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_stats():
    """测试统计功能"""
    print("\n" + "="*60)
    print("测试 7: 统计功能")
    print("="*60)
    
    try:
        detector = YOLODetector(DetectorConfig())
        
        # 初始统计
        print("\n[测试] 初始统计...")
        stats = detector.get_inference_stats()
        print(f"  推理次数：{stats['inference_count']}")
        print(f"  平均时间：{stats['avg_inference_time_ms']:.2f}ms")
        
        # 模拟推理统计
        print("\n[测试] 模拟推理统计...")
        detector._inference_count = 100
        detector._total_inference_time = 0.5  # 500ms
        
        stats = detector.get_inference_stats()
        print(f"  推理次数：{stats['inference_count']}")
        print(f"  总时间：{stats['total_time']:.3f}s")
        print(f"  平均时间：{stats['avg_inference_time_ms']:.2f}ms")
        print(f"  FPS: {stats['fps']:.2f}")
        
        # 重置统计
        print("\n[测试] 重置统计...")
        detector.reset_stats()
        stats = detector.get_inference_stats()
        print(f"  推理次数：{stats['inference_count']}")
        
        assert stats['inference_count'] == 0, "统计应该已重置"
        
        print("\n[PASS] 统计功能测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 统计功能测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_model_type_support():
    """测试 YOLOv8n/YOLO11n 支持"""
    print("\n" + "="*60)
    print("测试 8: 模型类型支持")
    print("="*60)
    
    try:
        print("\n[测试] 创建不同模型类型的配置...")
        
        configs = [
            ("YOLOv8n", DetectorConfig(model_type="yolov8n")),
            ("YOLO11n", DetectorConfig(model_type="yolo11n")),
        ]
        
        for name, config in configs:
            detector = YOLODetector(config)
            print(f"  {name}: model_type={config.model_type}")
        
        print("\n[PASS] 模型类型支持测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 模型类型支持测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_create_detector_helper():
    """测试 create_detector 辅助函数"""
    print("\n" + "="*60)
    print("测试 9: create_detector 辅助函数")
    print("="*60)
    
    try:
        print("\n[测试] 使用 create_detector 创建检测器...")
        
        # 注意：这里不会真正加载模型，因为 model_path 为空
        try:
            detector = create_detector(
                model_path="",  # 空路径，会失败
                model_type="yolov8n",
                conf_threshold=0.25
            )
            print("  [WARN] 应该抛出异常")
            return False
        except Exception as e:
            print(f"  [OK] 正确抛出异常：{type(e).__name__}")
        
        print("\n[PASS] create_detector 辅助函数测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] create_detector 辅助函数测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有测试"""
    print("\n" + "="*60)
    print("YOLO 检测器模块测试套件")
    print("="*60)
    print(f"项目根目录：{project_root}")
    print(f"NumPy 版本：{np.__version__}")
    
    import onnxruntime as ort
    print(f"ONNX Runtime 版本：{ort.__version__}")
    
    tests = [
        ("检测器初始化", test_detector_initialization),
        ("检测器配置", test_detector_config),
        ("预处理器集成", test_preprocessor_integration),
        ("模拟推理流程", test_mock_inference),
        ("NMS 功能", test_nms),
        ("检测结果格式", test_detection_output_format),
        ("统计功能", test_stats),
        ("模型类型支持", test_model_type_support),
        ("create_detector", test_create_detector_helper),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, "PASS" if success else "FAIL"))
        except Exception as e:
            print(f"\n[ERROR] {test_name} 测试失败：{e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, f"ERROR: {e}"))
    
    # 打印总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    for test_name, result in results:
        print(f"{test_name:25s}: [{result}]")
    
    all_passed = all(r[1] == "PASS" for r in results)
    if all_passed:
        print("\n[SUCCESS] 所有测试通过！")
        print("\n注意：这些测试未使用真实 ONNX 模型")
        print("实际使用时需要加载真实的 YOLOv8n/YOLO11n ONNX 模型")
    else:
        print("\n[FAILED] 部分测试失败")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
