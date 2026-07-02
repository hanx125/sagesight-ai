"""
更新算法配置的标签文件路径
"""
import sqlite3
from pathlib import Path

# 数据库路径
db_path = Path('data/tasks.db')

# 连接数据库
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 查询所有算法
cursor.execute('SELECT id, name, labels_file FROM algorithm_configs')
algorithms = cursor.fetchall()

print("当前算法配置：")
for algo_id, name, labels_file in algorithms:
    print(f"  ID: {algo_id}, Name: {name}, Labels: {labels_file}")

# 更新算法的标签文件路径
# YOLOv11使用COCO数据集的80个类别
cursor.execute('''
    UPDATE algorithm_configs 
    SET labels_file = 'models/labels/coco_labels.txt' 
    WHERE name LIKE '%yolo%'
''')

updated_count = cursor.rowcount
print(f"\n已更新 {updated_count} 个算法的标签文件路径")

# 查询更新后的结果
cursor.execute('SELECT id, name, labels_file FROM algorithm_configs WHERE name LIKE "%yolo%"')
updated_algorithms = cursor.fetchall()

print("\n更新后的算法配置：")
for algo_id, name, labels_file in updated_algorithms:
    print(f"  ID: {algo_id}, Name: {name}, Labels: {labels_file}")

# 提交更改
conn.commit()
conn.close()

print("\n✓ 更新完成")