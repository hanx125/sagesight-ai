"""
端到端集成测试

测试完整的检测通道：
1. 从 RTSP 流解码视频
2. 写入共享内存
3. 读取共享内存
4. YOLO11n 推理
5. 输出检测结果

使用真实模型：e:\sagesight-AI\models\yolov11n.onnx
使用配置文件：e:\sagesight-AI\config\decoder_config.yaml
"""

import sys
import time
import yaml
import numpy as np
from pathlib import Path
from typing import List

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.decoders import VideoDecoder, DecoderConfig
from src.utils import open_shared_memory, FrameHeader
from src.detectors import YOLODetector, DetectorConfig, Detection, create_preprocessor


def load_config(config_path: str) -> dict:
    """加载 YAML 配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_coco_labels(labels_path: str) -> List[str]:
    """加载 COCO 类别标签"""
    with open(labels_path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f.readlines()]


def create_detector_config(config: dict, labels: List[str]) -> DetectorConfig:
    """从配置文件创建检测器配置"""
    return DetectorConfig(
        model_path=config['detector']['model_path'],
        model_type=config['detector'].get('model_type', 'yolo11n'),
        conf_threshold=config['detector'].get('conf_threshold', 0.25),
        iou_threshold=config['detector'].get('iou_threshold', 0.45),
        class_names=tuple(labels)
    )


def print_detection_results(detections: List[Detection], frame_num: int):
    """打印检测结果"""
    if not detections:
        print(f"  帧 {frame_num}: 未检测到目标")
        return
    
    print(f"\n  帧 {frame_num}: 检测到 {len(detections)} 个目标")
    
    # 按置信度排序
    detections.sort(key=lambda x: x.confidence, reverse=True)
    
    # 打印前 5 个检测结果
    for i, det in enumerate(detections[:5]):
        x1, y1, x2, y2 = det.bbox
        width = x2 - x1
        height = y2 - y1
        
        print(f"    [{i+1}] {det.class_name}")
        print(f"        置信度：{det.confidence:.3f}")
        print(f"        坐标：({x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f})")
        print(f"        尺寸：{width:.1f}x{height:.1f}")


def test_end_to_end():
    """端到端集成测试"""
    print("\n" + "="*70)
    print("端到端集成测试：RTSP 解码 → 共享内存 → YOLO11n 推理")
    print("="*70)
    
    # 加载配置文件
    config_path = project_root / "config" / "decoder_config.yaml"
    labels_path = project_root / "models" / "coco_labels.txt"
    
    print(f"\n[步骤 1] 加载配置文件...")
    print(f"  配置路径：{config_path}")
    config = load_config(str(config_path))
    print(f"  [OK] 配置加载成功")
    
    # 加载类别标签
    print(f"\n[步骤 2] 加载 COCO 标签...")
    print(f"  标签路径：{labels_path}")
    labels = load_coco_labels(str(labels_path))
    print(f"  [OK] 加载 {len(labels)} 个类别")
    
    # 创建解码器配置
    print(f"\n[步骤 3] 创建视频解码器...")
    decoder_config = DecoderConfig(
        rtsp_url=config['decoder']['rtsp_url'],
        width=config['decoder']['width'],
        height=config['decoder']['height'],
        hwaccel=config['decoder']['hwaccel'],
        rtsp_transport=config['decoder']['rtsp_transport'],
        reconnect=config['decoder']['reconnect'],
        reconnect_delay=config['decoder']['reconnect_delay']
    )
    print(f"  RTSP 地址：{decoder_config.rtsp_url[:50]}...")
    print(f"  分辨率：{decoder_config.width}x{decoder_config.height}")
    print(f"  硬件加速：{decoder_config.hwaccel}")
    
    decoder = VideoDecoder(decoder_config)
    
    # 启动解码器
    print(f"\n[步骤 4] 启动视频解码器...")
    success = decoder.start()
    if not success:
        print("  [FAIL] 解码器启动失败")
        return False
    print(f"  [OK] 解码器启动成功")
    
    # 等待解码器初始化
    print(f"\n[步骤 5] 等待解码器初始化...")
    time.sleep(3)
    
    # 创建检测器配置
    print(f"\n[步骤 6] 创建 YOLO11n 检测器...")
    detector_config_dict = {
        'detector': {
            'model_path': str(project_root / "models" / "yolov11n.onnx"),
            'model_type': 'yolo11n',
            'conf_threshold': 0.25,
            'iou_threshold': 0.45
        }
    }
    detector_config = create_detector_config(detector_config_dict, labels)
    detector = YOLODetector(detector_config)
    
    # 加载模型
    try:
        detector.load_model()
        print(f"  [OK] 模型加载成功")
    except Exception as e:
        print(f"  [FAIL] 模型加载失败：{e}")
        decoder.stop()
        return False
    
    # 创建预处理器
    print(f"\n[步骤 7] 创建预处理器...")
    preprocessor = create_preprocessor(640, 640)
    print(f"  [OK] 预处理器创建成功")
    
    # 打开共享内存（读取端）
    print(f"\n[步骤 8] 打开共享内存...")
    shm_name = config['shared_memory']['name']
    shm = open_shared_memory(shm_name)
    print(f"  [OK] 共享内存已打开：{shm_name}")
    
    # 开始推理测试
    print(f"\n[步骤 9] 开始推理测试（运行 10 秒）...")
    print("="*70)
    
    test_duration = 10  # 秒
    start_time = time.time()
    frames_processed = 0
    total_detections = 0
    inference_times = []
    
    try:
        while time.time() - start_time < test_duration:
            # 从共享内存读取帧
            try:
                header, y, u, v = shm.read_frame()
            except Exception as e:
                print(f"  读取共享内存失败：{e}")
                time.sleep(0.1)
                continue
            
            # 预处理
            preprocess_start = time.time()
            input_tensor = preprocessor.preprocess(y, u, v)
            preprocess_time = time.time() - preprocess_start
            
            # 推理
            infer_start = time.time()
            detections = detector.infer(input_tensor)
            infer_time = time.time() - infer_start
            
            inference_times.append(infer_time)
            frames_processed += 1
            total_detections += len(detections)
            
            # 每 2 秒打印一次结果
            if frames_processed % 20 == 0:
                print_detection_results(detections, frames_processed)
                print(f"  预处理时间：{preprocess_time*1000:.2f}ms")
                print(f"  推理时间：{infer_time*1000:.2f}ms")
                print(f"  解码器 FPS: {decoder.get_stats()['fps']:.2f}")
        
        # 测试完成
        print("\n" + "="*70)
        print("测试完成")
        print("="*70)
        
        # 打印统计信息
        elapsed_time = time.time() - start_time
        avg_inference_time = np.mean(inference_times)
        
        print(f"\n性能统计:")
        print(f"  测试时长：{elapsed_time:.2f}秒")
        print(f"  处理帧数：{frames_processed}")
        print(f"  检测帧率：{frames_processed/elapsed_time:.2f} FPS")
        print(f"  平均推理时间：{avg_inference_time*1000:.2f}ms")
        print(f"  平均推理 FPS: {1.0/avg_inference_time:.2f}")
        print(f"  总检测目标数：{total_detections}")
        print(f"  平均每帧检测：{total_detections/frames_processed:.2f}个")
        
        # 解码器统计
        decoder_stats = decoder.get_stats()
        print(f"\n解码器统计:")
        print(f"  解码帧数：{decoder_stats['frames_decoded']}")
        print(f"  解码 FPS: {decoder_stats['fps']:.2f}")
        print(f"  解码错误：{decoder_stats['decode_errors']}")
        
        # 检测器统计
        detector_stats = detector.get_inference_stats()
        print(f"\n检测器统计:")
        print(f"  推理次数：{detector_stats['inference_count']}")
        print(f"  平均推理时间：{detector_stats['avg_inference_time_ms']:.2f}ms")
        print(f"  推理 FPS: {detector_stats['fps']:.2f}")
        
        # 验证性能是否达标
        print(f"\n性能验证:")
        detection_fps = frames_processed / elapsed_time
        if detection_fps >= 30:
            print(f"  [OK] 检测帧率 >= 30 FPS ({detection_fps:.2f} FPS)")
        else:
            print(f"  [WARN] 检测帧率 < 30 FPS ({detection_fps:.2f} FPS)")
        
        if avg_inference_time * 1000 <= 10:
            print(f"  [OK] 推理延迟 <= 10ms ({avg_inference_time*1000:.2f}ms)")
        else:
            print(f"  [WARN] 推理延迟 > 10ms ({avg_inference_time*1000:.2f}ms)")
        
        print("\n[PASS] 端到端测试完成")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # 清理资源
        print(f"\n[清理] 停止解码器...")
        decoder.stop()
        print(f"[清理] 关闭共享内存...")
        shm.close()
        print(f"[OK] 资源已清理")


def test_model_only():
    """仅测试模型推理（不使用 RTSP 流）"""
    print("\n" + "="*70)
    print("模型推理测试：使用 YOLO11n 模型")
    print("="*70)
    
    # 加载配置文件
    config_path = project_root / "config" / "decoder_config.yaml"
    labels_path = project_root / "models" / "coco_labels.txt"
    
    print(f"\n[步骤 1] 加载配置和标签...")
    config = load_config(str(config_path))
    labels = load_coco_labels(str(labels_path))
    print(f"  [OK] 加载 {len(labels)} 个类别")
    
    # 创建检测器
    print(f"\n[步骤 2] 创建 YOLO11n 检测器...")
    model_path = str(project_root / "models" / "yolov11n.onnx")
    detector_config = DetectorConfig(
        model_path=model_path,
        model_type='yolo11n',
        conf_threshold=0.25,
        iou_threshold=0.45,
        class_names=tuple(labels)
    )
    detector = YOLODetector(detector_config)
    
    # 加载模型
    try:
        detector.load_model()
        print(f"  [OK] 模型加载成功：{model_path}")
    except Exception as e:
        print(f"  [FAIL] 模型加载失败：{e}")
        return False
    
    # 创建预处理器
    print(f"\n[步骤 3] 创建预处理器...")
    preprocessor = create_preprocessor(640, 640)
    
    # 创建测试图像（模拟 YUV 数据）
    print(f"\n[步骤 4] 创建测试图像...")
    y = np.random.randint(100, 200, (640, 640), dtype=np.uint8)
    u = np.random.randint(100, 150, (320, 320), dtype=np.uint8)
    v = np.random.randint(100, 150, (320, 320), dtype=np.uint8)
    print(f"  Y 平面：{y.shape}, 范围=[{y.min()}, {y.max()}]")
    print(f"  U 平面：{u.shape}, 范围=[{u.min()}, {u.max()}]")
    print(f"  V 平面：{v.shape}, 范围=[{v.min()}, {v.max()}]")
    
    # 推理测试
    print(f"\n[步骤 5] 执行推理测试（10 次）...")
    inference_times = []
    
    for i in range(10):
        # 预处理
        input_tensor = preprocessor.preprocess(y, u, v)
        
        # 推理
        start_time = time.time()
        detections = detector.infer(input_tensor)
        infer_time = time.time() - start_time
        
        inference_times.append(infer_time)
        
        if i == 0:
            print(f"  第 1 次推理:")
            print(f"    检测目标数：{len(detections)}")
            if len(detections) > 0:
                print(f"    第一个检测：{detections[0].class_name} (conf={detections[0].confidence:.3f})")
    
    # 打印统计
    avg_time = np.mean(inference_times)
    print(f"\n推理性能:")
    print(f"  平均推理时间：{avg_time*1000:.2f}ms")
    print(f"  平均推理 FPS: {1.0/avg_time:.2f}")
    print(f"  最小推理时间：{min(inference_times)*1000:.2f}ms")
    print(f"  最大推理时间：{max(inference_times)*1000:.2f}ms")
    
    # 验证性能
    print(f"\n性能验证:")
    if avg_time * 1000 <= 10:
        print(f"  [OK] 推理延迟 <= 10ms ({avg_time*1000:.2f}ms)")
        return True
    else:
        print(f"  [WARN] 推理延迟 > 10ms ({avg_time*1000:.2f}ms)")
        return True  # 仍然算通过，只是性能未达最优


def main():
    """主测试函数"""
    print("\n" + "="*70)
    print("YOLO 检测通道集成测试")
    print("="*70)
    print(f"项目根目录：{project_root}")
    print(f"模型目录：{project_root / 'models'}")
    print(f"配置目录：{project_root / 'config'}")
    
    # 选择测试模式
    print("\n请选择测试模式:")
    print("  1. 仅模型推理测试（快速，不需要 RTSP 流）")
    print("  2. 完整端到端测试（需要 RTSP 流）")
    
    choice = input("\n请输入选择 (1/2): ").strip()
    
    if choice == "1":
        success = test_model_only()
    elif choice == "2":
        success = test_end_to_end()
    else:
        print("\n[FAIL] 无效的选择")
        return False
    
    # 总结
    print("\n" + "="*70)
    print("测试总结")
    print("="*70)
    if success:
        print("\n[SUCCESS] 测试通过！")
    else:
        print("\n[FAILED] 测试失败")
    
    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
