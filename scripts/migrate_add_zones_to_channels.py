"""
数据库迁移脚本：在task_device_channel_bindings表添加zones_config字段

执行方式：
    python scripts/migrate_add_zones_to_channels.py

功能：
    1. 检查zones_config字段是否存在
    2. 如果不存在，添加zones_config字段
    3. 设置默认值为空JSON数组
"""

import sqlite3
import os
import json

# 数据库路径
DB_PATH = 'data/tasks.db'


def check_column_exists(cursor, table_name, column_name):
    """检查列是否存在"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def migrate_database():
    """执行数据库迁移"""
    # 确保数据目录存在
    os.makedirs('data', exist_ok=True)

    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 检查zones_config字段是否存在
        if check_column_exists(cursor, 'task_device_channel_bindings', 'zones_config'):
            print("[OK] zones_config字段已存在，无需迁移")
            return

        print("[START] 开始迁移：添加zones_config字段...")

        # 方法1：尝试直接ALTER TABLE（SQLite 3.35.0+支持）
        try:
            cursor.execute('''
                ALTER TABLE task_device_channel_bindings 
                ADD COLUMN zones_config TEXT DEFAULT '[]'
            ''')
            print("[OK] 方法1成功：直接ALTER TABLE")
        except sqlite3.OperationalError as e:
            print(f"[WARN] 方法1失败: {e}")
            print("[INFO] 尝试方法2：重建表...")

            # 方法2：重建表（兼容旧版本SQLite）
            # 1. 创建新表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS task_device_channel_bindings_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    status VARCHAR(20) DEFAULT 'stopped',
                    zones_config TEXT DEFAULT '[]',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES detection_tasks(id),
                    FOREIGN KEY (channel_id) REFERENCES device_channel_pool(id),
                    UNIQUE(task_id, channel_id)
                )
            ''')

            # 2. 复制数据
            cursor.execute('''
                INSERT INTO task_device_channel_bindings_new 
                (id, task_id, channel_id, enabled, status, created_at)
                SELECT id, task_id, channel_id, enabled, status, created_at
                FROM task_device_channel_bindings
            ''')

            # 3. 删除旧表
            cursor.execute('DROP TABLE task_device_channel_bindings')

            # 4. 重命名新表
            cursor.execute('''
                ALTER TABLE task_device_channel_bindings_new 
                RENAME TO task_device_channel_bindings
            ''')

            print("[OK] 方法2成功：重建表")

        # 提交事务
        conn.commit()
        print("[OK] 迁移完成：zones_config字段已添加")

        # 验证迁移结果
        cursor.execute("PRAGMA table_info(task_device_channel_bindings)")
        columns = cursor.fetchall()
        print("\n[INFO] 当前表结构:")
        for col in columns:
            print(f"  - {col[1]}: {col[2]}")

    except Exception as e:
        print(f"[ERROR] 迁移失败: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    migrate_database()