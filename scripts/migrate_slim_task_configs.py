"""
数据库迁移脚本：清理任务表中的冗余配置项

执行方式：
    python scripts/migrate_slim_task_configs.py

背景：
    任务创建/编辑中原先可动态调整的全部配置项（解码器、检测器、跟踪器、运动检测、
    事件输出、显示）已统一收敛到 config/*.yaml，任务表不再保存这些配置，
    仅保留少数需要按任务动态调整的参数。本脚本清理存量任务 JSON 中的冗余 key，
    使配置文件成为唯一来源，避免同一参数存在多个来源。

清理规则：
    decoder_config   -> {}
    tracker_config   -> {}
    motion_config    -> {}
    display_config   -> {}
    detector_config  -> 仅保留 model_path / input_width / input_height / conf_threshold
    event_config     -> 仅保留 enabled / filter / metadata / trajectory

脚本幂等，可重复执行；执行前会自动备份数据库为 data/tasks.db.bak。
"""

import json
import os
import shutil
import sqlite3

# 数据库路径（与 TaskManager 默认值保持一致）
DB_PATH = 'data/tasks.db'

# 需要处理的配置列
CONFIG_COLUMNS = [
    'decoder_config', 'detector_config', 'tracker_config',
    'motion_config', 'event_config', 'display_config'
]

# 整体清空、改由 config/*.yaml 提供的配置列
EMPTY_COLUMNS = ['decoder_config', 'tracker_config', 'motion_config', 'display_config']

# 需要按任务保留的参数（检测器：模型来源与置信度阈值）
DETECTOR_KEEP = ('model_path', 'input_width', 'input_height', 'conf_threshold')

# 需要按任务保留的参数（事件输出：开关、过滤、元数据）
EVENT_KEEP = ('enabled', 'filter', 'metadata', 'trajectory')


def _load_config(raw):
    """安全解析配置 JSON，非法内容按空字典处理"""
    if not raw:
        return {}
    try:
        config = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def _slim_config(config, keep_keys):
    """按保留清单过滤配置字段"""
    return {key: value for key, value in config.items() if key in keep_keys}


def migrate_database():
    """执行数据库迁移"""
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] 数据库不存在: {DB_PATH}")
        return

    backup_path = DB_PATH + '.bak'
    shutil.copy2(DB_PATH, backup_path)
    print(f"[OK] 已备份数据库: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        rows = cursor.execute(
            "SELECT id, task_name, decoder_config, detector_config, tracker_config, "
            "motion_config, event_config, display_config FROM detection_tasks"
        ).fetchall()

        changed = 0
        for row in rows:
            new_values = {column: {} for column in EMPTY_COLUMNS}
            new_values['detector_config'] = _slim_config(
                _load_config(row['detector_config']), DETECTOR_KEEP
            )
            new_values['event_config'] = _slim_config(
                _load_config(row['event_config']), EVENT_KEEP
            )

            # 已清理过的任务跳过，保证幂等
            if all(_load_config(row[column]) == new_values[column] for column in CONFIG_COLUMNS):
                continue

            cursor.execute(
                "UPDATE detection_tasks SET decoder_config = ?, detector_config = ?, "
                "tracker_config = ?, motion_config = ?, event_config = ?, display_config = ? "
                "WHERE id = ?",
                (
                    json.dumps(new_values['decoder_config'], ensure_ascii=False),
                    json.dumps(new_values['detector_config'], ensure_ascii=False),
                    json.dumps(new_values['tracker_config'], ensure_ascii=False),
                    json.dumps(new_values['motion_config'], ensure_ascii=False),
                    json.dumps(new_values['event_config'], ensure_ascii=False),
                    json.dumps(new_values['display_config'], ensure_ascii=False),
                    row['id'],
                )
            )
            changed += 1
            print(f"[OK] 任务 {row['id']} ({row['task_name']}): 已清理冗余配置")

        conn.commit()
        print(f"[DONE] 共 {len(rows)} 个任务，实际修改 {changed} 个")
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] 迁移失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    migrate_database()