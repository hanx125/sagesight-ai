import sqlite3

conn = sqlite3.connect('data/tasks.db')
cursor = conn.cursor()

cursor.execute('''
    SELECT id, name, source
    FROM cameras
    WHERE id = '7813c985-368a-4822-b461-74cbe8631780'
''')

row = cursor.fetchone()
if row:
    print(f'摄像机ID: {row[0]}')
    print(f'名称: {row[1]}')
    print(f'源: {row[2]}')
else:
    print('未找到该摄像机')

conn.close()