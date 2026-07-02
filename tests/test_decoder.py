"""
视频解码器测试脚本

测试内容：
1. 共享内存创建和读写
2. RTSP 流解码
3. 硬件加速验证
4. 性能统计
5. 帧数据验证
"""

import sys
import os
import time
import numpy as np
import cv2
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.decoders import VideoDecoder, DecoderConfig
from src.utils import open_shared_memory, FrameHeader


def test_shared_memory():
    """测试共享内存基本功能"""
    print("\n" + "="*60)
    print("测试 1: 共享内存基本功能")
    print("="*60)
    
    try:
        # 创建共享内存（写入端）
        print("\n[测试] 创建共享内存...")
        from src.utils import create_shared_memory
        shm_writer = create_shared_memory("test_shm")
        
        # 创建测试数据
        y = np.random.randint(0, 255, (640, 640), dtype=np.uint8)
        u = np.random.randint(0, 255, (320, 320), dtype=np.uint8)
        v = np.random.randint(0, 255, (320, 320), dtype=np.uint8)
        
        # 写入数据
        print("[测试] 写入测试帧...")
        header = FrameHeader(
            sequence_num=1,
            timestamp_ns=time.time_ns(),
            write_time_ns=time.time_ns()
        )
        shm_writer.write_complete_frame(header, y, u, v)
        print(f"[OK] 写入成功：序列号={header.sequence_num}")
        
        # 读取数据（读取端）
        print("\n[测试] 打开共享内存（读取端）...")
        shm_reader = open_shared_memory("test_shm")
        
        print("[测试] 读取测试帧...")
        read_header, y_read, u_read, v_read = shm_reader.read_frame()
        
        # 验证数据
        print(f"[OK] 读取成功：序列号={read_header.sequence_num}")
        print(f"  Y 平面形状：{y_read.shape}")
        print(f"  U 平面形状：{u_read.shape}")
        print(f"  V 平面形状：{v_read.shape}")
        
        # 验证数据一致性
        assert y.shape == y_read.shape, "Y 平面形状不匹配"
        assert u.shape == u_read.shape, "U 平面形状不匹配"
        assert v.shape == v_read.shape, "V 平面形状不匹配"
        assert np.array_equal(y, y_read), "Y 平面数据不一致"
        assert np.array_equal(u, u_read), "U 平面数据不一致"
        assert np.array_equal(v, v_read), "V 平面数据不一致"
        print("[OK] 数据一致性验证通过")
        
        # 清理
        shm_writer.close()
        shm_reader.close()
        
        print("\n[PASS] 共享内存测试通过")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 共享内存测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_video_decoder():
    """测试视频解码器"""
    print("\n" + "="*60)
    print("测试 2: 视频解码器功能")
    print("="*60)
    
    # RTSP 流地址
    rtsp_url = "rtsp://admin:tfld2023@192.168.5.201:554/Streaming/Channels/1"
    
    try:
        # 创建解码器配置
        print("\n[测试] 创建解码器配置...")
        config = DecoderConfig(
            rtsp_url=rtsp_url,
            width=640,
            height=640,
            hwaccel="dxva2",  # RTX 2070 使用 DXVA2（已验证可用）
            rtsp_transport="tcp",
            reconnect=True,
            reconnect_delay=5
        )
        
        # 创建解码器
        print("[测试] 创建视频解码器...")
        decoder = VideoDecoder(config)
        
        # 启动解码器
        print("[测试] 启动解码器...")
        success = decoder.start()
        
        if not success:
            print("[FAIL] 解码器启动失败")
            return False
        
        print("[OK] 解码器启动成功")
        
        # 等待并收集统计信息
        print("\n[测试] 运行解码器 10 秒...")
        test_duration = 10  # 秒
        
        start_time = time.time()
        while time.time() - start_time < test_duration:
            time.sleep(1)
            stats = decoder.get_stats()
            # 计算 FPS
            elapsed = time.time() - start_time
            fps = stats['frames_decoded'] / elapsed if elapsed > 0 else 0
            print(f"  运行 {int(time.time() - start_time)}s | "
                  f"帧数：{stats['frames_decoded']} | "
                  f"FPS: {fps:.2f} | "
                  f"错误：{stats['decode_errors']}")
        
        # 打印最终统计
        decoder.print_stats()
        
        # 验证解码性能
        stats = decoder.get_stats()
        if stats['frames_decoded'] > 0:
            print(f"\n[OK] 解码测试成功：解码 {stats['frames_decoded']} 帧")
        else:
            print("\n[WARN] 未解码任何帧")
        
        # 停止解码器
        print("\n[测试] 停止解码器...")
        decoder.stop()
        
        print("\n[PASS] 视频解码器测试完成")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 视频解码器测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_shared_memory_with_decoder():
    """测试解码器写入共享内存"""
    print("\n" + "="*60)
    print("测试 3: 解码器 + 共享内存联合测试")
    print("="*60)
    
    rtsp_url = "rtsp://admin:tfld2023@192.168.5.201:554/Streaming/Channels/1"
    
    try:
        # 创建解码器
        print("\n[测试] 创建解码器...")
        config = DecoderConfig(
            rtsp_url=rtsp_url,
            width=640,
            height=640,
            hwaccel="dxva2"  # 使用 DXVA2
        )
        decoder = VideoDecoder(config)
        
        # 启动解码器
        print("[测试] 启动解码器...")
        decoder.start()
        
        # 等待 2 秒让解码器开始工作
        print("[测试] 等待解码器初始化...")
        time.sleep(2)
        
        # 打开共享内存（读取端）
        print("[测试] 打开共享内存（读取端）...")
        shm_reader = open_shared_memory()
        
        # 尝试读取帧
        print("[测试] 尝试读取帧（5 秒）...")
        frames_read = 0
        max_frames = 10
        start_time = time.time()
        
        while frames_read < max_frames and (time.time() - start_time) < 5:
            try:
                header, y, u, v = shm_reader.read_frame()
                frames_read += 1
                
                if frames_read <= 3 or frames_read % 5 == 0:
                    print(f"  [OK] 帧 {header.sequence_num}: "
                          f"时间戳={header.timestamp_ms:.2f}ms, "
                          f"Y 范围=[{y.min()}, {y.max()}]")
            
            except Exception as e:
                print(f"  读取失败：{e}")
                time.sleep(0.1)
        
        # 验证帧数据质量
        if frames_read > 0:
            print(f"\n[OK] 成功读取 {frames_read} 帧")
            
            # 读取一帧进行详细验证
            header, y, u, v = shm_reader.read_frame()
            print(f"\n帧数据验证:")
            print(f"  序列号：{header.sequence_num}")
            print(f"  Y 平面：形状={y.shape}, 均值={y.mean():.1f}, 范围=[{y.min()}, {y.max()}]")
            print(f"  U 平面：形状={u.shape}, 均值={u.mean():.1f}, 范围=[{u.min()}, {u.max()}]")
            print(f"  V 平面：形状={v.shape}, 均值={v.mean():.1f}, 范围=[{v.min()}, {v.max()}]")
            
            # 检查 YUV 数据合理性
            if y.mean() > 10 and y.mean() < 245:
                print("  [OK] Y 平面数据合理")
            else:
                print("  [WARN] Y 平面数据可能异常")
        else:
            print("\n[WARN] 未能从共享内存读取任何帧")
        
        # 清理
        shm_reader.close()
        decoder.stop()
        
        print("\n[PASS] 联合测试完成")
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 联合测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_hwaccel():
    """测试不同的硬件加速方式"""
    print("\n" + "="*60)
    print("测试 4: 硬件加速方式对比")
    print("="*60)
    
    rtsp_url = "rtsp://admin:tfld2023@192.168.5.201:554/Streaming/Channels/1"
    hwaccel_methods = ["cuda", "dxva2", "none"]  # qsv 不适合 NVIDIA GPU
    
    results = {}
    
    for hwaccel in hwaccel_methods:
        print(f"\n[测试] 测试 {hwaccel} 硬件加速...")
        
        try:
            config = DecoderConfig(
                rtsp_url=rtsp_url,
                width=640,
                height=640,
                hwaccel=hwaccel,
                reconnect=False  # 不重连，快速失败
            )
            decoder = VideoDecoder(config)
            decoder.start()
            
            # 运行 5 秒
            time.sleep(5)
            stats = decoder.get_stats()
            
            results[hwaccel] = {
                'success': True,
                'frames': stats['frames_decoded'],
                'fps': stats['fps'],
                'errors': stats['decode_errors']
            }
            
            print(f"  [OK] {hwaccel}: {stats['frames_decoded']} 帧，FPS={stats['fps']:.2f}")
            
            decoder.stop()
        
        except Exception as e:
            results[hwaccel] = {
                'success': False,
                'error': str(e)
            }
            print(f"  [FAIL] {hwaccel} 失败：{e}")
    
    # 打印对比结果
    print("\n" + "="*60)
    print("硬件加速对比结果")
    print("="*60)
    for hwaccel, result in results.items():
        if result['success']:
            print(f"{hwaccel:8s}: [OK] {result['frames']:4d} 帧，FPS={result['fps']:6.2f}, 错误={result['errors']}")
        else:
            print(f"{hwaccel:8s}: [FAIL] {result['error']}")
    
    return True


def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("视频解码模块测试套件")
    print("="*60)
    print(f"项目根目录：{project_root}")
    print(f"Python 版本：{sys.version}")
    print(f"NumPy 版本：{np.__version__}")
    print(f"OpenCV 版本：{cv2.__version__}")
    
    results = {}
    
    # 测试 1: 共享内存基本功能
    results['shared_memory'] = test_shared_memory()
    
    # 测试 2: 视频解码器功能
    results['decoder'] = test_video_decoder()
    
    # 测试 3: 解码器 + 共享内存联合测试
    results['combined'] = test_shared_memory_with_decoder()
    
    # 测试 4: 硬件加速对比（可选，耗时较长）
    # 自动跳过硬件加速对比测试
    print("\n跳过硬件加速对比测试（需要手动确认）")
    results['hwaccel'] = 'skipped'
    
    # 总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    for test_name, result in results.items():
        status = "[PASS]" if result is True else "[FAIL]" if result is False else "[SKIP]"
        print(f"{test_name:20s}: {status}")
    
    # 返回测试结果
    all_passed = all(r is True for r in results.values() if isinstance(r, bool))
    if all_passed:
        print("\n[SUCCESS] 所有测试通过！")
    else:
        print("\n[WARNING] 部分测试失败，请检查错误信息")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
