import sqlite3

conn = sqlite3.connect('data/tasks.db')
cursor = conn.cursor()

# 修复任务 ID 6 的配置
# 1. 将模型路径改为 ONNX 格式
# 2. 保持 backend 为 onnx_cpu（或改为 onnx_cuda 如果有 GPU）

cursor.execute('''
    UPDATE detection_tasks 
    SET model_path = 'models/detection/yolov11n.onnx'
    WHERE id = 6
''')

conn.commit()

print(f'已更新 {cursor.rowcount} 条记录')

# 验证
cursor.execute('SELECT id, task_name, model_path FROM detection_tasks WHERE id = 6')
task = cursor.fetchone()
print(f'\n更新后的配置:')
print(f'  任务 ID: {task[0]}')
print(f'  任务名称: {task[1]}')
print(f'  模型路径: {task[2]}')

conn.close()