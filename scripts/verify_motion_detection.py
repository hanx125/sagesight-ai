"""
验证运动检测是否正确工作

检查项：
1. 任务配置中的 motion_detection_enabled 值
2. 运动检测跳过推理的逻辑
3. 运动检测参数配置
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tasks.task_manager import TaskManager, get_default_motion_config

def verify_motion_detection():
    """验证运动检测配置"""
    print("\n" + "="*70)
    print("运动检测验证")
    print("="*70)

    # 初始化任务管理器
    task_manager = TaskManager()

    # 获取所有任务
    tasks = task_manager.get_all_tasks()

    if not tasks:
        print("\n[WARN] 没有找到任何任务")
        return False

    print(f"\n找到 {len(tasks)} 个任务")

    # 检查每个任务的运动检测配置
    for task in tasks:
        print(f"\n任务: {task.task_name} (ID: {task.id})")
        print(f"  运动检测启用: {task.motion_detection_enabled}")

        # 检查 motion_config（全局配置：config/motion_config.yaml，所有任务一致）
        motion_config = get_default_motion_config()
        print(f"  运动检测配置:")
        print(f"    history: {motion_config.get('history', 'N/A')}")
        print(f"    var_threshold: {motion_config.get('var_threshold', 'N/A')}")
        print(f"    min_area: {motion_config.get('min_area', 'N/A')}")
        print(f"    detect_shadows: {motion_config.get('detect_shadows', 'N/A')}")
        print(f"    blur_size: {motion_config.get('blur_size', 'N/A')}")
        print(f"    morph_size: {motion_config.get('morph_size', 'N/A')}")

        # 运动检测跳过机制
        if task.motion_detection_enabled:
            print(f"  ✅ 运动检测已启用")
            print(f"  ✅ 无运动时会跳过推理（降低CPU占用）")
        else:
            print(f"  ❌ 运动检测已禁用")
            print(f"  ❌ 每帧都会进行推理（CPU占用高）")

    return True

def check_running_tasks():
    """检查正在运行的任务"""
    print("\n" + "="*70)
    print("运行中的任务检查")
    print("="*70)

    # 检查系统管理器中的运行任务
    from src.system.system_manager import ProcessManager
    import os

    # 检查是否有任务进程
    task_pids = []
    for item in os.listdir('.'):
        if item.endswith('.pid'):
            try:
                with open(item, 'r') as f:
                    pid = int(f.read().strip())
                    task_pids.append((item, pid))
            except:
                pass

    if task_pids:
        print(f"\n找到 {len(task_pids)} 个任务进程:")
        for pid_file, pid in task_pids:
            print(f"  PID文件: {pid_file}, PID: {pid}")
    else:
        print("\n没有找到运行中的任务进程")

    return True

if __name__ == "__main__":
    try:
        # 验证运动检测配置
        verify_motion_detection()

        # 检查运行中的任务
        check_running_tasks()

        print("\n" + "="*70)
        print("验证完成")
        print("="*70)

        print("\n建议:")
        print("1. 如果运动检测已禁用，建议启用以降低CPU占用")
        print("2. 如果运动检测已启用，查看任务日志确认跳过推理的频率")
        print("3. 日志关键字: '无运动目标，已跳过 XXX 帧推理'")

    except Exception as e:
        print(f"\n[ERROR] 验证失败: {e}")
        import traceback
        traceback.print_exc()