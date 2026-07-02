"""
解码环节性能测试
测试FFmpeg硬件解码的真实性能
"""
import sys
import os
import time
import subprocess
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 70)
print("FFmpeg解码环节性能测试")
print("=" * 70)

# RTSP流地址
rtsp_url = "rtsp://admin:tfld2023@192.168.5.201:554/Streaming/Channels/101?transportmode=unicast&profile=Profile_1"
print(f"\n[信息] 使用RTSP流: {rtsp_url}")
use_rtsp = True

# FFmpeg命令配置
width = 640
height = 640
hwaccel = "cuda"  # 使用CUDA硬件加速

# 构建FFmpeg命令
if use_rtsp:
    ffmpeg_args = [
        'ffmpeg',
        '-loglevel', 'warning',
        '-rtsp_transport', 'tcp',
        '-hwaccel', hwaccel,
        '-hwaccel_output_format', 'nv12',
        '-extra_hw_frames', '16',
        '-i', rtsp_url,
        '-vf', f'scale={width}:{height},format=nv12',
        '-f', 'rawvideo',
        'pipe:1'
    ]
else:
    ffmpeg_args = [
        'ffmpeg',
        '-loglevel', 'warning',
        '-hwaccel', hwaccel,
        '-hwaccel_output_format', 'nv12',
        '-i', video_file,
        '-vf', f'scale={width}:{height},format=nv12',
        '-f', 'rawvideo',
        'pipe:1'
    ]

print(f"\n[配置]")
print(f"  分辨率: {width}x{height}")
print(f"  硬件加速: {hwaccel}")
print(f"  输出格式: NV12")

# 计算帧大小
frame_size = width * height * 3 // 2  # NV12: Y + UV = 1.5倍
print(f"  帧大小: {frame_size} bytes")

print("\n[测试1] 启动FFmpeg进程...")
start_time = time.time()
process = subprocess.Popen(
    ffmpeg_args,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    bufsize=1024 * 1024
)
startup_time = time.time() - start_time
print(f"  FFmpeg启动耗时: {startup_time * 1000:.2f}ms")

# 等待FFmpeg就绪
time.sleep(0.5)

if process.poll() is not None:
    print(f"\n[错误] FFmpeg进程启动失败")
    stderr_output = process.stderr.read().decode('utf-8', errors='replace')
    print(f"错误信息:\n{stderr_output}")
    sys.exit(1)

print(f"  FFmpeg进程已启动，PID: {process.pid}")

# 测试2: 解码前100帧
print("\n[测试2] 解码前100帧...")
y_size = width * height
uv_size = width * height // 2

decode_times = []
read_times = []
parse_times = []
total_times = []

buffer = b''
frame_count = 0
max_frames = 100

try:
    while frame_count < max_frames:
        # 读取数据
        t0 = time.perf_counter()
        chunk = process.stdout.read(65536)  # 读取64KB
        t1 = time.perf_counter()
        
        if not chunk:
            print(f"\n[信息] 视频结束，共解码 {frame_count} 帧")
            break
        
        buffer += chunk
        read_time = (t1 - t0) * 1000
        read_times.append(read_time)
        
        # 解析帧
        while len(buffer) >= frame_size and frame_count < max_frames:
            # 提取一帧
            t2 = time.perf_counter()
            frame_data = buffer[:frame_size]
            buffer = buffer[frame_size:]
            
            # 解析NV12
            y_data = frame_data[:y_size]
            uv_data = frame_data[y_size:]
            
            y = np.frombuffer(y_data, dtype=np.uint8).reshape((height, width))
            uv = np.frombuffer(uv_data, dtype=np.uint8).reshape((height // 2, width))
            
            t3 = time.perf_counter()
            
            parse_time = (t3 - t2) * 1000
            parse_times.append(parse_time)
            
            decode_time = read_time + parse_time
            decode_times.append(decode_time)
            total_times.append(decode_time)
            
            frame_count += 1
            
            if frame_count % 20 == 0:
                print(f"  已解码 {frame_count} 帧...")

except KeyboardInterrupt:
    print(f"\n[信息] 测试中断，共解码 {frame_count} 帧")

# 停止FFmpeg
process.terminate()
try:
    process.wait(timeout=3)
except subprocess.TimeoutExpired:
    process.kill()

# 统计结果
if len(decode_times) > 0:
    print(f"\n{'=' * 70}")
    print("解码性能统计")
    print(f"{'=' * 70}")
    
    print(f"\n[数据读取]")
    print(f"  平均耗时: {np.mean(read_times):.2f}ms")
    print(f"  最小耗时: {np.min(read_times):.2f}ms")
    print(f"  最大耗时: {np.max(read_times):.2f}ms")
    print(f"  标准差: {np.std(read_times):.2f}ms")
    
    print(f"\n[帧解析]")
    print(f"  平均耗时: {np.mean(parse_times):.2f}ms")
    print(f"  最小耗时: {np.min(parse_times):.2f}ms")
    print(f"  最大耗时: {np.max(parse_times):.2f}ms")
    
    print(f"\n[解码总耗时（读取+解析）]")
    print(f"  平均耗时: {np.mean(decode_times):.2f}ms")
    print(f"  最小耗时: {np.min(decode_times):.2f}ms")
    print(f"  最大耗时: {np.max(decode_times):.2f}ms")
    print(f"  标准差: {np.std(decode_times):.2f}ms")
    print(f"  理论FPS: {1000 / np.mean(decode_times):.1f}")
    
    print(f"\n[总体统计]")
    print(f"  总帧数: {frame_count}")
    print(f"  总耗时: {sum(total_times):.2f}ms")
    print(f"  平均FPS: {frame_count / (sum(total_times) / 1000):.1f}")
    
    # 性能评估
    print(f"\n{'=' * 70}")
    print("性能评估")
    print(f"{'=' * 70}")
    
    avg_decode = np.mean(decode_times)
    if avg_decode < 5:
        print(f"✓ 解码性能优秀 ({avg_decode:.2f}ms/帧)")
        print(f"  硬件加速工作正常")
    elif avg_decode < 15:
        print(f"○ 解码性能良好 ({avg_decode:.2f}ms/帧)")
        print(f"  可以考虑优化读取策略")
    elif avg_decode < 30:
        print(f"△ 解码性能一般 ({avg_decode:.2f}ms/帧)")
        print(f"  建议检查硬件加速是否生效")
        print(f"  或考虑降低分辨率")
    else:
        print(f"✗ 解码性能较差 ({avg_decode:.2f}ms/帧)")
        print(f"  可能原因:")
        print(f"    1. 硬件加速未生效（回退到CPU解码）")
        print(f"    2. 视频编码格式不支持硬件解码")
        print(f"    3. 分辨率过高")
        print(f"    4. 磁盘I/O瓶颈（如果是本地文件）")
else:
    print(f"\n[错误] 未能解码任何帧")

print(f"\n{'=' * 70}")
