"""
验证 stride 对齐修复是否有效

测试 hwdownload 滤镜是否能去除 DXVA2/CUDA/QSV 硬件解码器的 stride 对齐问题。
如果修复有效，输出的帧数据应该紧凑（无 stride 填充），画面正常。
"""

import sys
import os
import subprocess
import numpy as np
import cv2

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

def test_stride_fix(rtsp_url, width=640, height=640, hwaccel='dxva2'):
    """
    测试 stride 对齐修复
    
    Args:
        rtsp_url: RTSP 流地址
        width: 输出宽度
        height: 输出高度
        hwaccel: 硬件加速方式（dxva2/cuda/qsv）
    """
    print("="*70)
    print("Stride 对齐修复验证测试")
    print("="*70)
    print(f"RTSP URL: {rtsp_url}")
    print(f"分辨率: {width}x{height}")
    print(f"硬件加速: {hwaccel}")
    print()
    
    # 构建 FFmpeg 命令（使用修复后的滤镜配置）
    pix_fmt = 'nv12'  # 硬件加速使用 NV12 格式
    
    ffmpeg_args = [
        'ffmpeg',
        '-loglevel', 'warning',
        '-rtsp_transport', 'tcp',
    ]
    
    # 硬件加速参数
    if hwaccel == 'dxva2':
        ffmpeg_args.extend(['-hwaccel', 'dxva2'])
    elif hwaccel == 'cuda':
        ffmpeg_args.extend([
            '-hwaccel', 'cuda',
            '-hwaccel_output_format', 'nv12',
            '-extra_hw_frames', '16',
            '-threads', '16',
        ])
    elif hwaccel == 'qsv':
        ffmpeg_args.extend([
            '-hwaccel', 'qsv',
            '-hwaccel_output_format', 'nv12',
        ])
    
    # 输入源
    ffmpeg_args.extend(['-i', rtsp_url])
    
    # 像素格式
    ffmpeg_args.extend(['-pix_fmt', pix_fmt])
    
    # 滤镜配置（修复后的配置）
    vf_filters = []
    
    # 硬件解码器需要 hwdownload 去除 stride 对齐
    if hwaccel in ['dxva2', 'cuda', 'qsv']:
        vf_filters.append('hwdownload')
    
    # 缩放滤镜
    vf_filters.append(f'scale={width}:{height}:flags=lanczos')
    
    # 强制像素格式
    vf_filters.append(f'format={pix_fmt}')
    
    ffmpeg_args.extend(['-vf', ','.join(vf_filters)])
    
    # 输出格式
    ffmpeg_args.extend([
        '-f', 'rawvideo',
        'pipe:1'
    ])
    
    print(f"FFmpeg 命令: {' '.join(ffmpeg_args)}")
    print()
    
    # 计算 NV12 帧大小
    frame_size = width * height
    y_size = frame_size
    uv_size = frame_size // 2
    total_size = y_size + uv_size
    
    print(f"NV12 帧大小计算:")
    print(f"  Y 平面: {y_size} 字节 ({width}x{height})")
    print(f"  UV 平面: {uv_size} 字节 ({width}x{height//2})")
    print(f"  总大小: {total_size} 字节")
    print()
    
    # 启动 FFmpeg 进程
    try:
        process = subprocess.Popen(
            ffmpeg_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1024 * 1024
        )
        
        print("FFmpeg 进程已启动，等待输出...")
        
        # 读取前 5 帧进行验证
        buffer = b''
        frames_read = 0
        max_frames = 5
        
        while frames_read < max_frames:
            # 从 FFmpeg stdout 读取数据
            chunk = process.stdout.read(4096)
            
            if not chunk:
                print("FFmpeg 进程已退出或无数据输出")
                break
            
            buffer += chunk
            
            # 当缓冲区有足够数据时，提取帧
            while len(buffer) >= total_size:
                frame_data = buffer[:total_size]
                buffer = buffer[total_size:]
                
                # 解析 NV12 帧
                y_data = frame_data[:y_size]
                uv_data = frame_data[y_size:]
                
                # 转换为 numpy 数组
                y = np.frombuffer(y_data, dtype=np.uint8).reshape((height, width))
                uv = np.frombuffer(uv_data, dtype=np.uint8).reshape((height // 2, width))
                
                # NV12 → BGR（OpenCV）
                yuv_nv12 = np.ascontiguousarray(np.concatenate([y, uv], axis=0))
                bgr = cv2.cvtColor(yuv_nv12, cv2.COLOR_YUV2BGR_NV12)
                
                frames_read += 1
                print(f"帧 {frames_read}: Y shape={y.shape}, UV shape={uv.shape}, BGR shape={bgr.shape}")
                
                # 检查帧是否正常（无 stride 错乱）
                # 如果 stride 正常，帧应该可以正确显示
                # 如果 stride 错乱，画面会裁剪成两半
                
                # 保存帧为图片（用于验证）
                output_file = os.path.join(project_root, 'output', f'stride_test_frame_{frames_read}.jpg')
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                cv2.imwrite(output_file, bgr)
                print(f"  已保存: {output_file}")
        
        # 终止 FFmpeg 进程
        process.terminate()
        process.wait(timeout=5)
        
        print()
        print("="*70)
        print("验证结果:")
        print("="*70)
        
        if frames_read > 0:
            print(f"成功读取 {frames_read} 帧")
            print(f"帧数据大小正确（{total_size} 字节）")
            print()
            print("请检查保存的图片文件，确认画面是否正常：")
            for i in range(1, frames_read + 1):
                output_file = os.path.join(project_root, 'output', f'stride_test_frame_{i}.jpg')
                print(f"  {output_file}")
            print()
            print("如果画面正常（无裁剪错乱），说明 stride 对齐修复有效。")
            print("如果画面裁剪成两半（左右互换），说明 stride 对齐问题仍然存在。")
        else:
            print("未能读取任何帧数据，可能原因：")
            print("  1. RTSP 连接失败")
            print("  2. FFmpeg 启动失败")
            print("  3. hwdownload 滤镜不支持（FFmpeg 版本过低）")
            print()
            print("请检查 FFmpeg stderr 输出：")
            if process.stdout:
                stderr_output = process.stdout.read().decode('utf-8', errors='replace')
                if stderr_output:
                    print(stderr_output[:1000])
        
    except FileNotFoundError:
        print("错误: FFmpeg 未找到，请确保已安装 FFmpeg 并添加到 PATH")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='验证 stride 对齐修复')
    parser.add_argument('--rtsp', required=True, help='RTSP 流地址')
    parser.add_argument('--width', type=int, default=640, help='输出宽度')
    parser.add_argument('--height', type=int, default=640, help='输出高度')
    parser.add_argument('--hwaccel', default='dxva2', choices=['dxva2', 'cuda', 'qsv'], help='硬件加速方式')
    
    args = parser.parse_args()
    
    test_stride_fix(args.rtsp, args.width, args.height, args.hwaccel)