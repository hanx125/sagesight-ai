"""
检查数据库字段顺序和映射

验证CREATE TABLE的字段顺序是否与_row_to_camera映射一致
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3

def check_db_fields():
    """检查数据库字段顺序"""
    db_path = "data/tasks.db"
    
    if not os.path.exists(db_path):
        print(f"错误: 数据库文件不存在: {db_path}")
        return
    
    # 输出到文件
    output_file = "scripts/db_fields_check_result.txt"
    
    with open(output_file, 'w', encoding='utf-8') as f:
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                
                # 获取表结构信息
                cursor.execute("PRAGMA table_info(cameras)")
                columns = cursor.fetchall()
                
                f.write("=" * 60 + "\n")
                f.write("数据库表字段顺序（PRAGMA table_info结果）：\n")
                f.write("=" * 60 + "\n")
                
                for idx, col in enumerate(columns):
                    col_id = col[0]  # 列ID（从0开始）
                    col_name = col[1]  # 列名
                    col_type = col[2]  # 列类型
                    col_notnull = col[3]  # 是否非空
                    col_default = col[4]  # 默认值
                    col_pk = col[5]  # 是否主键
                    
                    f.write(f"[{col_id}] {col_name} ({col_type}) - default: {col_default}\n")
                
                f.write("=" * 60 + "\n")
                f.write(f"总字段数: {len(columns)}\n")
                f.write("=" * 60 + "\n\n")
                
                # 获取一个摄像机数据示例
                cursor.execute("SELECT * FROM cameras LIMIT 1")
                row = cursor.fetchone()
                
                if row:
                    f.write("实际数据示例（SELECT * 结果）：\n")
                    f.write("=" * 60 + "\n")
                    
                    # 获取列名列表
                    col_names = [col[1] for col in columns]
                    
                    for idx, value in enumerate(row):
                        col_name = col_names[idx] if idx < len(col_names) else f"unknown_{idx}"
                        f.write(f"row[{idx}] = {col_name}: {value}\n")
                    
                    f.write("=" * 60 + "\n\n")
                    
                    # 特别检查protocol字段的位置
                    protocol_idx = None
                    ai_detection_idx = None
                    
                    for idx, col in enumerate(columns):
                        if col[1] == 'protocol':
                            protocol_idx = idx
                        if col[1] == 'ai_detection':
                            ai_detection_idx = idx
                    
                    f.write(f"关键字段位置：\n")
                    f.write(f"protocol字段位置: row[{protocol_idx}]\n")
                    f.write(f"ai_detection字段位置: row[{ai_detection_idx}]\n")
                    f.write(f"protocol实际值: {row[protocol_idx] if protocol_idx else 'N/A'}\n")
                    f.write(f"ai_detection实际值: {row[ai_detection_idx] if ai_detection_idx else 'N/A'}\n")
                
                print(f"检查结果已保存到: {output_file}")
                
        except Exception as e:
            f.write(f"检查失败: {e}\n")
            print(f"错误: {e}")

if __name__ == "__main__":
    print("开始检查数据库字段顺序...")
    check_db_fields()
    print("检查完成！")