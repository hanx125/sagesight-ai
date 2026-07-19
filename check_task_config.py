import sqlite3
import json

conn = sqlite3.connect('data/tasks.db')
cursor = conn.cursor()

# 获取所有任务（表名是 detection_tasks）
cursor.execute('SELECT id, task_name, model_path, detector_config, enabled, status FROM detection_tasks')
tasks = cursor.fetchall()

print('=' * 80)
print('任务配置检查')
print('=' * 80)

for task in tasks:
    print(f'\n任务 ID: {task[0]}')
    print(f'任务名称: {task[1]}')
    print(f'模型路径: {task[2]}')
    print(f'状态: {task[5]}, 启用: {task[4]}')
    
    # 解析 detector_config
    if task[3]:
        try:
            detector_config = json.loads(task[3])
            print(f'detector_config.backend: {detector_config.get("backend", "N/A")}')
            print(f'detector_config.conf_threshold: {detector_config.get("conf_threshold", "N/A")}')
        except:
            print(f'detector_config: 解析失败 - {task[3][:50]}...')
    else:
        print('detector_config: 空')

conn.close()