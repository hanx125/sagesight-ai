import sqlite3

# 检查数据库路径
db_path = 'data/tasks.db'

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 获取所有表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

print('=' * 80)
print(f'数据库: {db_path}')
print('=' * 80)
print(f'表数量: {len(tables)}')
for table in tables:
    print(f'  - {table[0]}')

# 检查是否有 tasks 相关的表
task_tables = [t[0] for t in tables if 'task' in t[0].lower()]
print(f'\n任务相关的表: {task_tables}')

# 如果有 tasks 表，查看结构
if 'tasks' in [t[0] for t in tables]:
    cursor.execute('PRAGMA table_info(tasks)')
    columns = cursor.fetchall()
    print('\ntasks 表结构:')
    for col in columns:
        print(f'  {col[1]}: {col[2]}')

conn.close()