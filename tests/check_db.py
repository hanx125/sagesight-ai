import sqlite3

conn = sqlite3.connect('e:\\sagesight-AI\\data\\tasks.db')
cursor = conn.cursor()

# 检查source字段
cursor.execute('PRAGMA table_info(cameras)')
cols = cursor.fetchall()
print(f'总字段数: {len(cols)}')
for row in cols:
    print(f'{row[0]}: {row[1]}')

# 检查是否有source字段
col_names = [row[1] for row in cols]
if 'source' in col_names:
    print('\nsource字段已存在')
else:
    print('\nsource字段不存在，尝试添加')
    try:
        cursor.execute('ALTER TABLE cameras ADD COLUMN source TEXT DEFAULT ""')
        conn.commit()
        print('source字段添加成功')
    except Exception as e:
        print(f'添加失败: {e}')

conn.close()
