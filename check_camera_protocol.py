import sqlite3
import json

conn = sqlite3.connect('data/tasks.db')
cursor = conn.cursor()

# 检查摄像机的protocol字段
cursor.execute('''
    SELECT id, name, protocol, source
    FROM cameras
    WHERE id = '7813c985-368a-4822-b461-74cbe8631780'
''')

row = cursor.fetchone()
if row:
    print(f'摄像机ID: {row[0]}')
    print(f'名称: {row[1]}')
    print(f'协议: {row[2]}')
    print(f'源: {row[3][:80] if row[3] else "N/A"}...')
else:
    print('未找到该摄像机')

# 检查任务的decoder_config
cursor.execute('''
    SELECT id, task_name, decoder_config
    FROM detection_tasks
''')

print('\n任务的decoder_config:')
for row in cursor.fetchall():
    task_id, task_name, decoder_config_str = row
    if decoder_config_str:
        decoder_config = json.loads(decoder_config_str)
        has_hwaccel = 'hwaccel' in decoder_config
        has_hwaccel_preset = 'hwaccel_preset' in decoder_config
        print(f'任务ID={task_id}, 名称={task_name}:')
        print(f'  hwaccel={has_hwaccel}, hwaccel_preset={has_hwaccel_preset}')
        if has_hwaccel:
            print(f'  ⚠️ 旧版字段: hwaccel={decoder_config.get("hwaccel")}')

conn.close()