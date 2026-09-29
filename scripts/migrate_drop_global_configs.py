"""
数据库迁移脚本：任务表配置结构收敛

执行方式：
    python scripts/migrate_drop_global_configs.py

背景：
    解码/检测/跟踪/运动四类配置统一由 config/decoder_config.yaml、
    detector_config.yaml、tracker_config.yaml、motion_config.yaml 提供；
    事件输出与显示配置统一由 config/event_output.yaml、config/display_config.yaml 提供，
    均属于全局配置、所有任务一致，任务表不再保存整块 JSON。
    其中可按任务动态调整的参数提升为任务级独立列：
      - conf_threshold（原嵌套在 detector_config 中）
      - 事件输出/显示的布尔开关（原嵌套在 event_config / display_config 中）

迁移内容：
    1. detection_tasks 表新增 conf_threshold 列，并从旧 detector_config.conf_threshold 回填；
    2. 物理删除 decoder_config / detector_config / tracker_config / motion_config 四列；
    3. 新增事件输出/显示开关列（默认值取自 config/*.yaml），并从旧事件/显示 JSON 回填；
    4. 物理删除 event_config / display_config 两列；
    5. 计数区域分层：旧 counting_zones 中的单机画线行按通道合并搬迁到
       device_channel_pool.counting_lines，counting_zones 收敛为多机进出方向专用表。

脚本幂等，可重复执行；执行前会自动备份数据库为 data/tasks.db.bak。
"""

import os
import shutil
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tasks.task_manager import (
    migrate_task_config_schema,
    migrate_counting_zone_levels,
)

# 数据库路径（与 TaskManager 默认值保持一致）
DB_PATH = 'data/tasks.db'


def migrate_database():
    """执行数据库迁移"""
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] 数据库不存在: {DB_PATH}")
        return

    backup_path = DB_PATH + '.bak'
    shutil.copy2(DB_PATH, backup_path)
    print(f"[OK] 已备份数据库: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        migrate_task_config_schema(cursor)
        migrate_counting_zone_levels(cursor)
        conn.commit()
        print("[DONE] 迁移完成：任务表配置列收敛 + 计数区域按通道/任务分层")
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] 迁移失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    migrate_database()
