import sqlite3
import json

conn = sqlite3.connect('data/tasks.db')
cursor = conn.cursor()
cursor.execute('SELECT task_name, detector_config FROM detection_tasks WHERE task_name LIKE "%车%"')
task = cursor.fetchone()

if task:
    task_name, detector_config_str = task
    detector_config = json.loads(detector_config_str) if detector_config_str else {}
    print(f'任务: {task_name}')
    print(f'use_detection_pool: {detector_config.get("use_detection_pool", "未配置")}')
else:
    print('未找到"车"任务')

conn.close()