"""
数据库迁移脚本：在detection_tasks表添加zones_config字段

执行方式：
    python scripts/migrate_add_zones_to_tasks.py

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
        if check_column_exists(cursor, 'detection_tasks', 'zones_config'):
            print("[OK] zones_config字段已存在，无需迁移")
            return

        print("[START] 开始迁移：添加zones_config字段到detection_tasks表...")

        # 添加zones_config字段
        cursor.execute('''
            ALTER TABLE detection_tasks 
            ADD COLUMN zones_config TEXT DEFAULT '[]'
        ''')

        # 提交事务
        conn.commit()
        print("[OK] 迁移完成：zones_config字段已添加")

        # 验证迁移结果
        cursor.execute("PRAGMA table_info(detection_tasks)")
        columns = cursor.fetchall()
        print("\n[INFO] detection_tasks表当前字段:")
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