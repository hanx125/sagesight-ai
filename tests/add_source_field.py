import sqlite3

conn = sqlite3.connect('e:\\sagesight-AI\\data\\tasks.db')
cursor = conn.cursor()

# 检查source字段是否存在
cursor.execute('PRAGMA table_info(cameras)')
cols = [row[1] for row in cursor.fetchall()]

if 'source' not in cols:
    cursor.execute('ALTER TABLE cameras ADD COLUMN source TEXT DEFAULT ""')
    conn.commit()
    print('source字段已添加')
else:
    print('source字段已存在')

conn.close()
