"""
完整示例：运动检测 + 目标检测流程

演示如何使用新的架构：
    1. 运动检测（独立模块）
    2. 预处理（创建张量输入）
    3. 目标检测

架构流程：
    [共享内存 YUV帧] → [运动检测] → [运动区域] → [创建张量输入] → [目标检测]
"""

import numpy as np
from pathlib import Path

# 导入运动检测模块
from src.motion import MotionDetector, MotionFilter

# 导入检测模块
from src.detectors import OptimizedPreprocessor

# 导入解码器（用于测试）
from src.decoders import VideoDecoder, DecoderConfig, create_decoder
from src.utils import FFMPEG_HWACCEL_DXVA2


def demo_motion_detection():
    """演示运动检测流程"""
    print("=" * 80)
    print("示例1：运动检测独立模块")
    print("=" * 80)
    
    # 创建模拟帧数据（实际应从解码器获取）
    frame = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
    
    # 创建运动检测器
    motion_detector = MotionDetector(
        history=500,
        var_threshold=16.0,
        min_area=500,
        blur_size=5,
        morph_size=5
    )
    
    print("\n[运动检测器配置]")
    print(f"  - 历史: {motion_detector.history}")
    print(f"  - 方差阈值: {motion_detector.var_threshold}")
    print(f"  - 最小面积: {motion_detector.min_area}")
    
    # 预热运动检测器（需要30-500帧）
    print("\n[预热运动检测器]")
    for i in range(30):
        motion_detector.detect(frame)
    
    print(f"  - 已预热 {motion_detector.init_frame_count} 帧")
    print(f"  - 初始化状态: {motion_detector.is_initialized}")
    
    # 检测运动
    print("\n[检测运动]")
    motion_regions = motion_detector.detect(frame)
    print(f"  - 检测到 {len(motion_regions)} 个运动区域")
    
    # 显示运动区域详情
    for i, region in enumerate(motion_regions):
        print(f"  - 区域 {i+1}: bbox={region.bbox}, 面积={region.area}, 强度={region.intensity:.3f}")
    
    # 快速判断是否有运动
    has_motion = motion_detector.has_motion(frame)
    print(f"\n  - 是否有运动: {has_motion}")


def demo_preprocessing():
    """演示预处理流程"""
    print("\n" + "=" * 80)
    print("示例2：预处理模块（create_tensor_input）")
    print("=" * 80)
    
    # 创建模拟帧数据（NV12 格式）
    height, width = 640, 640
    y_plane = np.random.randint(0, 255, (height, width), dtype=np.uint8)
    uv_plane = np.random.randint(0, 255, (height // 2, width), dtype=np.uint8)
    
    # 创建预处理器
    preprocessor = OptimizedPreprocessor(
        width=width,
        height=height,
        model_input_size=640,
        rgb_mean=(0.0, 0.0, 0.0),
        rgb_std=(1.0, 1.0, 1.0)
    )
    
    print("\n[预处理器配置]")
    print(f"  - 输入尺寸: {preprocessor.width}x{preprocessor.height}")
    print(f"  - 模型输入尺寸: {preprocessor.model_input_size}")
    
    # 方式1：处理完整帧
    print("\n[方式1：处理完整帧]")
    frame_data = {
        'y': y_plane,
        'uv': uv_plane,
        'format': 'nv12'
    }
    
    tensor = preprocessor.create_tensor_input(frame_data)
    print(f"  - 输出张量形状: {tensor.shape}")
    print(f"  - 输出张量类型: {tensor.dtype}")
    print(f"  - 输出张量范围: [{tensor.min():.3f}, {tensor.max():.3f}]")
    
    # 方式2：处理运动区域（裁剪）
    print("\n[方式2：处理运动区域（裁剪）]")
    motion_region = (100, 100, 500, 500)  # (x1, y1, x2, y2)
    
    tensor_cropped = preprocessor.create_tensor_input(
        frame_data=frame_data,
        region=motion_region,
        frame_shape=(height, width)
    )
    print(f"  - 区域: {motion_region}")
    print(f"  - 输出张量形状: {tensor_cropped.shape}")
    print(f"  - 输出张量类型: {tensor_cropped.dtype}")


def demo_motion_filter():
    """演示运动区域过滤"""
    print("\n" + "=" * 80)
    print("示例3：运动区域过滤工具")
    print("=" * 80)
    
    # 创建模拟运动区域
    from src.motion import MotionRegion
    
    regions = [
        MotionRegion(bbox=(100, 100, 200, 200), area=10000, center=(150, 150), intensity=0.1),
        MotionRegion(bbox=(150, 150, 250, 250), area=10000, center=(200, 200), intensity=0.1),  # 重叠
        MotionRegion(bbox=(300, 300, 400, 400), area=10000, center=(350, 350), intensity=0.15),
        MotionRegion(bbox=(10, 10, 30, 30), area=400, center=(20, 20), intensity=0.05),  # 小区域
    ]
    
    print(f"\n[原始运动区域] {len(regions)} 个")
    for i, r in enumerate(regions):
        print(f"  - 区域 {i+1}: bbox={r.bbox}, 面积={r.area}")
    
    # 过滤小区域
    print("\n[步骤1：过滤小区域]")
    filtered = MotionFilter.filter_small_regions(regions, min_area=500)
    print(f"  - 过滤后: {len(filtered)} 个区域")
    
    # 合并重叠区域
    print("\n[步骤2：合并重叠区域]")
    merged = MotionFilter.merge_overlapping_regions(filtered, overlap_threshold=0.3)
    print(f"  - 合并后: {len(merged)} 个区域")
    
    # 扩展区域
    print("\n[步骤3：扩展区域]")
    expanded = MotionFilter.expand_regions(merged, frame_shape=(640, 640), expand_ratio=0.1)
    for i, r in enumerate(expanded):
        print(f"  - 区域 {i+1}: bbox={r.bbox}, 面积={r.area}")
    
    # 限制区域数量
    print("\n[步骤4：限制区域数量]")
    limited = MotionFilter.limit_region_count(expanded, max_count=2)
    print(f"  - 限制后: {len(limited)} 个区域")


def demo_complete_workflow():
    """演示完整工作流程"""
    print("\n" + "=" * 80)
    print("示例4：完整工作流程（运动检测 + 预处理）")
    print("=" * 80)
    
    # 模拟帧数据
    height, width = 640, 640
    frame = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
    y_plane = np.random.randint(0, 255, (height, width), dtype=np.uint8)
    uv_plane = np.random.randint(0, 255, (height // 2, width), dtype=np.uint8)
    
    # 步骤1：运动检测
    print("\n[步骤1：运动检测]")
    motion_detector = MotionDetector(history=500, var_threshold=16.0)
    
    # 预热
    for _ in range(30):
        motion_detector.detect(frame)
    
    motion_regions = motion_detector.detect(frame)
    print(f"  - 检测到 {len(motion_regions)} 个运动区域")
    
    # 步骤2：运动区域过滤
    print("\n[步骤2：运动区域过滤]")
    if motion_regions:
        # 合并重叠
        merged = MotionFilter.merge_overlapping_regions(motion_regions)
        # 扩展区域
        expanded = MotionFilter.expand_regions(merged, frame_shape=(height, width))
        # 限制数量
        final_regions = MotionFilter.limit_region_count(expanded, max_count=5)
        print(f"  - 最终区域: {len(final_regions} 个")
    else:
        final_regions = []
        print("  - 无运动，跳过检测")
    
    # 步骤3：创建张量输入
    print("\n[步骤3：创建张量输入]")
    preprocessor = OptimizedPreprocessor(width=width, height=height, model_input_size=640)
    
    frame_data = {
        'y': y_plane,
        'uv': uv_plane,
        'format': 'nv12'
    }
    
    tensors = []
    if final_regions:
        for region in final_regions:
            tensor = preprocessor.create_tensor_input(
                frame_data=frame_data,
                region=region.bbox,
                frame_shape=(height, width)
            )
            tensors.append(tensor)
        print(f"  - 创建了 {len(tensors)} 个张量")
    else:
        # 无运动，处理完整帧
        tensor = preprocessor.create_tensor_input(frame_data)
        tensors.append(tensor)
        print("  - 创建了 1 个完整帧张量")
    
    # 步骤4：目标检测（示例）
    print("\n[步骤4：目标检测]")
    print("  - 此处应调用目标检测器（YOLODetector）")
    print(f"  - 需要处理 {len(tensors)} 个张量")
    print("  - 示例代码：")
    print("      from src.detectors import YOLODetector")
    print("      detector = YOLODetector(config)")
    print("      for tensor in tensors:")
    print("          detections = detector.detect(tensor)")


def demo_with_real_video():
    """演示使用真实视频"""
    print("\n" + "=" * 80)
    print("示例5：使用真实视频文件测试")
    print("=" * 80)
    
    video_file = r"d:\sagesight-ai\打电话.mp4"
    
    if not Path(video_file).exists():
        print(f"\n[错误] 视频文件不存在: {video_file}")
        return
    
    print(f"\n[视频文件] {video_file}")
    
    # 创建解码器
    config = DecoderConfig(
        video_file=video_file,
        width=640,
        height=640,
        fps=30,
        hwaccel_preset=FFMPEG_HWACCEL_DXVA2,
        ring_buffer_block_count=10,
        index_queue_size=50
    )
    
    decoder = VideoDecoder(config)
    decoder.start()
    
    # 创建运动检测器和预处理器
    motion_detector = MotionDetector(history=500, var_threshold=16.0)
    preprocessor = OptimizedPreprocessor(width=640, height=640, model_input_size=640)
    
    print("\n[开始处理]")
    print("  - 解码器已启动")
    print("  - 运动检测器已创建")
    print("  - 预处理器已创建")
    
    # 处理10帧示例
    frame_count = 0
    max_frames = 10
    
    print(f"\n[处理 {max_frames} 帧]")
    
    while frame_count < max_frames:
        # 获取帧索引
        frame_index = decoder.get_frame_index()
        
        if frame_index:
            frame_count += 1
            
            # 从环形缓冲区读取帧数据（零拷贝）
            y, uv = decoder.get_frame_from_block(frame_index.shm_block_id)
            
            # 创建帧数据字典
            frame_data = {
                'y': y,
                'uv': uv,
                'format': 'nv12'
            }
            
            # 运动检测
            motion_regions = motion_detector.detect(frame_data.get('y'))  # 简化：使用Y平面
            
            # 创建张量输入
            tensor = preprocessor.create_tensor_input(frame_data)
            
            print(f"  帧 {frame_count}: 运动区域={len(motion_regions)}, 张量形状={tensor.shape}")
    
    # 停止解码器
    decoder.stop()
    
    print("\n[处理完成]")
    print(f"  - 总帧数: {frame_count}")
    print("  - 解码器已停止")


if __name__ == "__main__":
    # 运行所有示例
    demo_motion_detection()
    demo_preprocessing()
    demo_motion_filter()
    demo_complete_workflow()
    
    # 使用真实视频测试（可选）
    demo_with_real_video()
    
    print("\n" + "=" * 80)
    print("所有示例完成！")
    print("=" * 80)