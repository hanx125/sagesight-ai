"""
Python进程CPU和内存监控脚本
实时监控Python进程的CPU、内存、线程数使用情况
"""
import psutil
import time
import sys
from datetime import datetime

def clear_screen():
    """清屏"""
    import os
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header():
    """打印标题"""
    print("=" * 80)
    print("Python 进程 CPU 和内存监控工具")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

def find_python_processes():
    """查找所有Python进程"""
    procs = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'python' in proc.info['name'].lower() or \
               (proc.info['cmdline'] and any('python' in cmd.lower() for cmd in proc.info['cmdline'])):
                procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return procs

def analyze_process(proc):
    """分析单个进程"""
    try:
        pid = proc.pid
        name = proc.name()
        cmdline = proc.cmdline()
        
        # 获取CPU和内存信息
        cpu = proc.cpu_percent(interval=1.0)
        mem = proc.memory_info()
        threads = proc.num_threads()
        
        # 判断是否异常
        status = "正常"
        warnings = []
        
        if cpu > 80:
            status = "⚠️ CPU异常高"
            warnings.append("CPU超过80%")
        elif cpu > 50:
            status = "⚠️ CPU偏高"
            warnings.append("CPU超过50%")
        
        if mem.rss > 2 * 1024 * 1024 * 1024:  # 2GB
            status += " | ⚠️ 内存异常高"
            warnings.append("内存超过2GB")
        
        # 判断是否是sagesight-AI的进程
        is_sagesight = any('sagesight' in cmd.lower() or 'run.py' in cmd.lower() for cmd in cmdline) if cmdline else False
        
        print(f"\n进程: {name} (PID: {pid})")
        if is_sagesight:
            print(f"  📌 SageSight AI 进程")
        print(f"  状态: {status}")
        print(f"  CPU: {cpu:.1f}%")
        print(f"  内存: {mem.rss / 1024 / 1024:.2f} MB (RSS)")
        print(f"  线程数: {threads}")
        
        # 如果CPU高，显示线程详情
        if cpu > 50:
            print(f"\n  线程详情（高CPU使用）:")
            thread_list = proc.threads()
            for i, thread in enumerate(thread_list[:5]):
                total_time = thread.user_time + thread.system_time
                print(f"    线程 {i+1}: ID={thread.id}, 用户={thread.user_time:.2f}s, 系统={thread.system_time:.2f}s")
            
            if len(thread_list) > 5:
                print(f"    ... 还有 {len(thread_list) - 5} 个线程")
        
        # 检查子进程
        children = proc.children()
        if children:
            print(f"\n  子进程 ({len(children)} 个):")
            for child in children[:5]:
                try:
                    child_cpu = child.cpu_percent(interval=0.1)
                    child_mem = child.memory_info()
                    child_name = child.name()
                    print(f"    - {child_name} (PID: {child.pid}): CPU={child_cpu:.1f}%, 内存={child_mem.rss / 1024 / 1024:.2f}MB")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            if len(children) > 5:
                print(f"    ... 还有 {len(children) - 5} 个子进程")
        
        return cpu, mem.rss, warnings
        
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        print(f"  无法访问: {e}")
        return 0, 0, []

def monitor_continuous(pid=None):
    """持续监控"""
    prev_data = {}  # 存储上一次的数据
    
    while True:
        clear_screen()
        print_header()
        
        current_warnings = []
        
        if pid:
            try:
                proc = psutil.Process(pid)
                cpu, mem, warnings = analyze_process(proc)
                current_warnings.extend(warnings)
                
                # 检查内存增长
                if pid in prev_data:
                    mem_growth = mem - prev_data[pid]['mem']
                    if mem_growth > 50 * 1024 * 1024:  # 50MB增长
                        print(f"\n  ⚠️ 内存增长: {mem_growth / 1024 / 1024:.2f} MB")
                        current_warnings.append(f"内存增长{mem_growth / 1024 / 1024:.2f}MB")
                
                prev_data[pid] = {'cpu': cpu, 'mem': mem, 'time': time.time()}
                
            except psutil.NoSuchProcess:
                print(f"\n进程 {pid} 不存在")
                break
        else:
            procs = find_python_processes()
            if not procs:
                print("\n未发现Python进程")
            else:
                print(f"\n发现 {len(procs)} 个Python进程:\n")
                for proc in procs:
                    cpu, mem, warnings = analyze_process(proc)
                    current_warnings.extend(warnings)
                    
                    # 检查内存增长
                    if proc.pid in prev_data:
                        mem_growth = mem - prev_data[proc.pid]['mem']
                        if mem_growth > 50 * 1024 * 1024:
                            print(f"\n  ⚠️ 内存增长: {mem_growth / 1024 / 1024:.2f} MB")
                            current_warnings.append(f"内存增长{mem_growth / 1024 / 1024:.2f}MB")
                    
                    prev_data[proc.pid] = {'cpu': cpu, 'mem': mem, 'time': time.time()}
        
        # 显示警告汇总
        if current_warnings:
            print("\n" + "=" * 80)
            print("⚠️ 警告汇总:")
            for warning in current_warnings:
                print(f"  - {warning}")
            print("=" * 80)
        
        print("\n按 Ctrl+C 退出")
        time.sleep(5)

def main():
    """主函数"""
    print("\n使用说明:")
    print("1. 监控所有Python进程: python monitor_python.py")
    print("2. 监控特定进程: python monitor_python.py <PID>")
    print("\n")
    
    if len(sys.argv) > 1:
        try:
            pid = int(sys.argv[1])
            print(f"监控指定进程 PID: {pid}")
        except ValueError:
            print("错误: PID必须是数字")
            sys.exit(1)
    else:
        pid = None
        print("监控所有Python进程")
    
    try:
        monitor_continuous(pid)
    except KeyboardInterrupt:
        print("\n\n监控已停止")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()