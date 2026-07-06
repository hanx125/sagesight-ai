"""
CPU升高诊断脚本 - 检查解码、推理、事件生成配置
"""
import os
import sys
import yaml
import psutil
import time
import sqlite3
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def load_config(config_file):
    """加载配置文件"""
    config_path = project_root / 'config' / config_file
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

def get_running_tasks():
    """获取正在运行的任务"""
    db_path = project_root / 'data' / 'tasks.db'
    if not db_path.exists():
        return []
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT id, task_name, status, detector_config FROM tasks WHERE status = 'running'")
    tasks = cursor.fetchall()
    conn.close()
    
    return tasks

def check_decoder_config():
    """检查解码器配置"""
    print("\n" + "="*60)
    print("1. 解码器配置检查")
    print("="*60)
    
    decoder_config = load_config('decoder_config.yaml')
    decoder = decoder_config.get('decoder', {})
    
    print(f"硬件加速方式: {decoder.get('hwaccel', 'none')}")
    print(f"输出格式: {decoder.get('output_format', 'auto')}")
    print(f"RTSP传输协议: {decoder.get('rtsp_transport', 'tcp')}")
    print(f"环形缓冲区: {decoder.get('use_ring_buffer', False)}")
    print(f"缓冲区块数: {decoder.get('ring_buffer_blocks', 3)}")
    
    # 检查是否有FPS限制
    fps_limit = decoder.get('fps_limit', '未配置')
    if fps_limit == '未配置':
        print("⚠️ FPS限制: 未配置（可能导致全速解码）")
    else:
        print(f"✓ FPS限制: {fps_limit}")
    
    # 检查硬件加速是否正确
    hwaccel = decoder.get('hwaccel', 'none')
    if hwaccel == 'none':
        print("⚠️ 硬件加速: 未启用（纯CPU解码，高CPU占用）")
    elif hwaccel in ['dxva2', 'cuda', 'qsv', 'rk3588']:
        print(f"✓ 硬件加速: 已启用 ({hwaccel})")
    else:
        print(f"⚠️ 硬件加速: 未知配置 ({hwaccel})")
    
    # CUDA解码线程数检查
    if hwaccel == 'cuda':
        cuda_threads = decoder.get('cuda_threads', 16)
        if cuda_threads > 8:
            print(f"⚠️ CUDA线程数: {cuda_threads}（过高，建议降低到4-8）")
        else:
            print(f"✓ CUDA线程数: {cuda_threads}")
    
    return decoder

def check_detector_config():
    """检查检测器配置"""
    print("\n" + "="*60)
    print("2. 检测器配置检查")
    print("="*60)
    
    detector_config = load_config('detector_config.yaml')
    detector = detector_config.get('detector', {})
    
    print(f"推理后端: {detector.get('backend', 'onnx_cuda')}")
    print(f"FP16加速: {detector.get('use_fp16', False)}")
    print(f"置信度阈值: {detector.get('conf_threshold', 0.25)}")
    print(f"IOU阈值: {detector.get('iou_threshold', 0.45)}")
    
    # 检查推理后端
    backend = detector.get('backend', 'onnx_cuda')
    if backend == 'onnx_cpu':
        print("⚠️ 推理后端: CPU推理（高CPU占用，建议使用GPU）")
        print("  - 建议修改为: onnx_cuda 或 onnx_tensorrt")
    elif backend in ['onnx_cuda', 'onnx_tensorrt', 'tensorrt']:
        print(f"✓ 推理后端: GPU推理 ({backend})")
    elif backend == 'rknn':
        print(f"✓ 推理后端: NPU推理 ({backend})")
    else:
        print(f"⚠️ 推理后端: 未知 ({backend})")
    
    return detector

def check_event_config():
    """检查事件生成配置"""
    print("\n" + "="*60)
    print("3. 事件生成配置检查")
    print("="*60)
    
    event_config = load_config('event_output.yaml')
    event = event_config.get('event', {})
    gif = event.get('gif', {})
    
    print(f"GIF输出方式: {gif.get('output_method', 'none')}")
    print(f"GIF最大帧数: {gif.get('max_frames', 30)}")
    print(f"GIF播放FPS: {gif.get('fps', 10)}")
    print(f"缓存采样FPS: {gif.get('cache_fps', 10)}")
    print(f"帧缓存保留时间: {gif.get('cache_retention', 20)}秒")
    
    # 检查GIF生成是否启用
    output_method = gif.get('output_method', 'none')
    if output_method != 'none':
        print(f"✓ GIF生成: 已启用 ({output_method})")
        
        # 检查缓存FPS是否过高
        cache_fps = gif.get('cache_fps', 10)
        if cache_fps > 15:
            print(f"⚠️ 缓存FPS: {cache_fps}（过高，建议降低到5-10）")
            print("  - 过高的缓存FPS会增加CPU占用")
        
        # 检查帧缓存保留时间
        cache_retention = gif.get('cache_retention', 20)
        if cache_retention > 30:
            print(f"⚠️ 缓存保留时间: {cache_retention}秒（过长，占用内存）")
            print("  - 建议设置为10-20秒")
    else:
        print("✓ GIF生成: 未启用（节省资源）")
    
    return event

def check_running_tasks_config():
    """检查正在运行的任务配置"""
    print("\n" + "="*60)
    print("4. 运行任务配置检查")
    print("="*60)
    
    tasks = get_running_tasks()
    if not tasks:
        print("当前没有运行中的任务")
        return
    
    print(f"当前运行任务数: {len(tasks)}")
    
    for task_id, task_name, status, detector_config_str in tasks:
        print(f"\n任务: {task_name} (ID: {task_id})")
        
        # 解析detector_config（JSON字符串）
        try:
            import json
            detector_config = json.loads(detector_config_str) if detector_config_str else {}
            
            backend = detector_config.get('backend', '未配置')
            print(f"  推理后端: {backend}")
            
            if backend == 'onnx_cpu':
                print("  ⚠️ 使用CPU推理！")
            elif backend in ['onnx_cuda', 'onnx_tensorrt']:
                print("  ✓ 使用GPU推理")
            
            # 检查display配置中的FPS限制
            display_config = detector_config.get('display_config', {})
            display = display_config.get('display', {})
            target_fps = display.get('target_fps', 0)
            
            if target_fps == 0:
                print("  ⚠️ FPS限制: 0（无限制，可能导致全速解码）")
                print("    - 建议设置为15-25FPS")
            else:
                print(f"  ✓ FPS限制: {target_fps}")
            
        except Exception as e:
            print(f"  无法解析配置: {e}")

def check_process_status():
    """检查进程状态"""
    print("\n" + "="*60)
    print("5. Python进程状态检查")
    print("="*60)
    
    # 查找所有Python进程
    python_procs = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'python' in proc.info['name'].lower() or \
               (proc.info['cmdline'] and any('python' in cmd.lower() for cmd in proc.info['cmdline'] if cmd)):
                python_procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    if not python_procs:
        print("未发现Python进程")
        return
    
    print(f"发现 {len(python_procs)} 个Python进程")
    
    for proc in python_procs:
        try:
            pid = proc.pid
            name = proc.name()
            cpu = proc.cpu_percent(interval=1.0)
            mem = proc.memory_info()
            threads = proc.num_threads()
            
            print(f"\n进程: {name} (PID: {pid})")
            print(f"  CPU使用率: {cpu:.1f}%")
            print(f"  内存使用: {mem.rss / 1024 / 1024:.2f} MB")
            print(f"  线程数: {threads}")
            
            # 判断是否异常
            if cpu > 80:
                print("  ⚠️ CPU异常高！")
                print("  建议检查:")
                print("    - 是否使用CPU推理（backend=onnx_cpu）")
                print("    - FPS限制是否为0（无限制）")
                print("    - 是否有大量线程泄漏")
            elif cpu > 50:
                print("  ⚠️ CPU偏高")
            
            if threads > 50:
                print(f"  ⚠️ 线程数过多: {threads}")
                print("  可能原因:")
                print("    - GIF生成线程泄漏")
                print("    - 解码器线程数过高")
                print("    - AI流线程泄漏")
            
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"  无法访问: {e}")

def print_summary():
    """打印诊断总结"""
    print("\n" + "="*60)
    print("诊断总结与建议")
    print("="*60)
    
    print("\n【关键问题】")
    print("1. FPS限制未设置或为0")
    print("   - 现状: target_fps默认为0，导致全速解码")
    print("   - 影响: FFmpeg全速解码，CPU持续升高")
    print("   - 修复: 在display_config.yaml中设置target_fps=15-25")
    
    print("\n2. GIF生成线程泄漏")
    print("   - 现状: 每次GIF生成创建新线程，无限制")
    print("   - 影响: 线程数持续增长，CPU升高")
    print("   - 修复: 使用ThreadPoolExecutor限制线程数")
    
    print("\n3. CUDA解码线程数过高")
    print("   - 现状: threads=16（CUDA解码时）")
    print("   - 影响: CPU占用过高")
    print("   - 修复: 降低到threads=4-8")
    
    print("\n4. ONNX Runtime线程数优化")
    print("   - 现状: 已修复为每个任务最多4个线程")
    print("   - 效果: 多任务时CPU不会翻倍")
    
    print("\n【修复步骤】")
    print("1. 立即修复FPS限制:")
    print("   - 编辑 config/display_config.yaml")
    print("   - 设置 target_fps: 15")
    print("   - 重启任务")
    
    print("\n2. 修复GIF线程泄漏（代码已优化）:")
    print("   - task_executor.py 已添加清理逻辑")
    print("   - processed_track_ids 阈值降低到200")
    
    print("\n3. 检查推理后端:")
    print("   - 确保 backend 不是 'onnx_cpu'")
    print("   - 使用 'onnx_cuda' 或 'onnx_tensorrt'")
    
    print("\n4. 监控线程数:")
    print("   - 运行 diagnose_cpu_high.py --monitor")
    print("   - 观察线程数是否稳定")

def monitor_continuous():
    """持续监控"""
    print("\n开始持续监控（每3秒更新）...")
    print("按 Ctrl+C 退出")
    
    while True:
        try:
            # 清屏
            os.system('cls' if os.name == 'nt' else 'clear')
            
            print(f"\n时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*60)
            
            # 检查Python进程
            python_procs = []
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if 'python' in proc.info['name'].lower() or \
                       (proc.info['cmdline'] and any('python' in cmd.lower() for cmd in proc.info['cmdline'] if cmd)):
                        python_procs.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            for proc in python_procs:
                try:
                    pid = proc.pid
                    name = proc.name()
                    cpu = proc.cpu_percent(interval=0.5)
                    mem = proc.memory_info()
                    threads = proc.num_threads()
                    
                    print(f"\n进程: {name} (PID: {pid})")
                    print(f"  CPU: {cpu:.1f}%")
                    print(f"  内存: {mem.rss / 1024 / 1024:.2f} MB")
                    print(f"  线程数: {threads}")
                    
                    # 异常判断
                    status = []
                    if cpu > 80:
                        status.append("CPU异常")
                    if threads > 50:
                        status.append("线程过多")
                    if mem.rss > 2 * 1024 * 1024 * 1024:
                        status.append("内存异常")
                    
                    if status:
                        print(f"  ⚠️ 状态: {', '.join(status)}")
                    else:
                        print(f"  ✓ 正常")
                    
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            time.sleep(3)
            
        except KeyboardInterrupt:
            print("\n监控已停止")
            break

def main():
    """主函数"""
    if len(sys.argv) > 1 and sys.argv[1] == '--monitor':
        monitor_continuous()
    else:
        print("="*60)
        print("CPU升高诊断脚本")
        print("="*60)
        
        check_decoder_config()
        check_detector_config()
        check_event_config()
        check_running_tasks_config()
        check_process_status()
        print_summary()

if __name__ == "__main__":
    main()