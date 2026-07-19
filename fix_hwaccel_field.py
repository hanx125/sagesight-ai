"""
修复数据库中的 decoder_config 字段
将旧版 hwaccel 参数替换为 hwaccel_preset
"""

import sqlite3
import json

def fix_hwaccel_field():
    """修复 decoder_config 中的 hwaccel 字段"""
    conn = sqlite3.connect('data/tasks.db')
    cursor = conn.cursor()

    # 查询所有任务
    cursor.execute('SELECT id, task_name, decoder_config FROM detection_tasks')
    rows = cursor.fetchall()

    updated_count = 0

    for row in rows:
        task_id, task_name, decoder_config_str = row

        if not decoder_config_str:
            continue

        try:
            decoder_config = json.loads(decoder_config_str)

            # 检查是否包含旧版 hwaccel 字段
            if 'hwaccel' in decoder_config:
                print(f"任务ID={task_id}, 名称={task_name}: 发现旧版 hwaccel 字段")

                # 移除旧版字段
                hwaccel_value = decoder_config.pop('hwaccel')
                print(f"  - 移除旧版字段: hwaccel={hwaccel_value}")

                # 添加新版字段（如果没有）
                if 'hwaccel_preset' not in decoder_config:
                    # 根据旧值转换为新值
                    hwaccel_preset_map = {
                        'dxva2': 'preset-dxva2',
                        'cuda': 'preset-nvidia',
                        'qsv': 'preset-intel-qsv-h264',
                        'none': '',
                        'auto': 'preset-dxva2'
                    }
                    hwaccel_preset_value = hwaccel_preset_map.get(hwaccel_value, 'preset-dxva2')
                    decoder_config['hwaccel_preset'] = hwaccel_preset_value
                    print(f"  - 添加新版字段: hwaccel_preset={hwaccel_preset_value}")
                else:
                    print(f"  - 已有新版字段: hwaccel_preset={decoder_config['hwaccel_preset']}")

                # 更新数据库
                cursor.execute(
                    'UPDATE detection_tasks SET decoder_config = ? WHERE id = ?',
                    (json.dumps(decoder_config), task_id)
                )
                updated_count += 1
                print(f"  ✓ 已修复")

        except json.JSONDecodeError as e:
            print(f"任务ID={task_id}: JSON解析失败 - {e}")
        except Exception as e:
            print(f"任务ID={task_id}: 修复失败 - {e}")

    # 提交事务
    conn.commit()

    print(f"\n总计修复 {updated_count} 个任务")

    # 验证修复结果
    print("\n验证修复结果:")
    cursor.execute('SELECT id, task_name, decoder_config FROM detection_tasks')
    rows = cursor.fetchall()

    for row in rows:
        task_id, task_name, decoder_config_str = row
        if decoder_config_str:
            decoder_config = json.loads(decoder_config_str)
            has_hwaccel = 'hwaccel' in decoder_config
            has_hwaccel_preset = 'hwaccel_preset' in decoder_config
            print(f"任务ID={task_id}, 名称={task_name}: hwaccel={has_hwaccel}, hwaccel_preset={has_hwaccel_preset}")

    conn.close()

if __name__ == '__main__':
    print("开始修复 decoder_config 中的 hwaccel 字段...\n")
    fix_hwaccel_field()
    print("\n修复完成！")