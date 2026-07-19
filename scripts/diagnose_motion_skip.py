"""
运动检测效率诊断

诊断项：
1. 运动检测跳过推理的频率
2. 运动检测参数是否合理
3. 场景是否持续有运动
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tasks.task_manager import TaskManager

def diagnose_motion_efficiency():
    """诊断运动检测效率"""
    print("\n" + "="*70)
    print("运动检测效率诊断")
    print("="*70)

    # 初始化任务管理器
    task_manager = TaskManager()
    tasks = task_manager.get_all_tasks()

    if not tasks:
        print("\n[WARN] 没有找到任何任务")
        return

    print(f"\n找到 {len(tasks)} 个任务")

    for task in tasks:
        print(f"\n任务: {task.task_name} (ID: {task.id})")
        print(f"  运动检测启用: {task.motion_detection_enabled}")

        motion_config = task.motion_config

        # 诊断1：运动检测参数是否合理
        print(f"\n  [诊断1] 运动检测参数分析:")
        var_threshold = motion_config.get('var_threshold', 16)
        min_area = motion_config.get('min_area', 500)
        detect_shadows = motion_config.get('detect_shadows', False)

        print(f"    var_threshold: {var_threshold}")
        if var_threshold < 16:
            print(f"      [WARN] 值过低，可能过于敏感（推荐: 16-25）")
        elif var_threshold > 40:
            print(f"      [WARN] 值过高，可能漏检（推荐: 16-25）")
        else:
            print(f"      [OK] 值合理")

        print(f"    min_area: {min_area}")
        if min_area < 500:
            print(f"      [WARN] 值过低，噪声多（推荐: 500-1500）")
        elif min_area > 2000:
            print(f"      [WARN] 值过高，可能漏检（推荐: 500-1500）")
        else:
            print(f"      [OK] 值合理")

        print(f"    detect_shadows: {detect_shadows}")
        if detect_shadows:
            print(f"      [WARN] 已启用阴影检测（会增加计算开销）")
        else:
            print(f"      [OK] 已禁用阴影检测（推荐）")

        # 诊断2：场景分析
        print(f"\n  [诊断2] 场景分析建议:")
        print(f"    如果CPU占用高，可能原因:")
        print(f"      1. 场景中持续有运动（树叶摇动、人员流动）")
        print(f"      2. 运动检测参数过于敏感（var_threshold太低）")
        print(f"      3. 光线变化导致背景建模不稳定")
        print(f"      4. min_area太小，检测到过多小区域")

        # 诊断3：优化建议
        print(f"\n  [诊断3] 优化建议:")
        if task.motion_detection_enabled:
            print(f"    [OK] 运动检测已启用")
            print(f"    建议:")
            print(f"      1. 查看任务日志，确认跳过推理的频率")
            print(f"         日志关键字: '无运动目标，已跳过 XXX 帧推理'")
            print(f"      2. 如果跳过频率低，调整运动检测参数:")
            print(f"         - 增加 var_threshold（如: 25-40）")
            print(f"         - 增加 min_area（如: 1500-2000）")
            print(f"         - 禁用 detect_shadows")
            print(f"      3. 如果场景本身就有持续运动，考虑:")
            print(f"         - 降低推理帧率（decoder_config.fps: 10）")
            print(f"         - 使用GPU加速（backend: onnx_cuda）")
        else:
            print(f"    [WARN] 运动检测已禁用")
            print(f"    建议:")
            print(f"      1. 启用运动检测以降低CPU占用")
            print(f"      2. 前端任务表单 -> 运动检测 -> 启用")

def show_log_analysis():
    """显示日志分析方法"""
    print("\n" + "="*70)
    print("日志分析方法")
    print("="*70)

    print("\n方法1: 查看任务日志文件")
    print("  位置: logs/tasks/task_<task_id>.log")
    print("  搜索关键字: '无运动目标，已跳过'")

    print("\n方法2: 实时查看日志")
    print("  Windows PowerShell:")
    print("    Get-Content logs\\tasks\\task_*.log -Wait | Select-String '无运动目标'")
    print("  Linux/Mac:")
    print("    tail -f logs/tasks/task_*.log | grep '无运动目标'")

    print("\n方法3: 统计跳过频率")
    print("  Windows PowerShell:")
    print("    (Select-String -Path 'logs\\tasks\\task_*.log' -Pattern '无运动目标' | Measure-Object).Count")
    print("  Linux/Mac:")
    print("    grep -c '无运动目标' logs/tasks/task_*.log")

    print("\n预期结果:")
    print("  如果运动检测工作正常:")
    print("    - 跳过推理次数 > 总帧数的50%")
    print("    - 日志中频繁出现 '无运动目标，已跳过 XXX 帧推理'")
    print("  如果运动检测工作不正常:")
    print("    - 跳过推理次数 < 总帧数的10%")
    print("    - 日志中很少出现跳过消息")

if __name__ == "__main__":
    try:
        diagnose_motion_efficiency()
        show_log_analysis()

        print("\n" + "="*70)
        print("诊断完成")
        print("="*70)

    except Exception as e:
        print(f"\n[ERROR] 诊断失败: {e}")
        import traceback
        traceback.print_exc()