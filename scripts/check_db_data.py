"""
数据库表数据检查脚本

检查任务编辑更换摄像机后的数据一致性
"""

import sqlite3
import os
import sys

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

db_path = os.path.join(project_root, 'data', 'tasks.db')

print("="*70)
print("数据库表数据检查")
print("="*70)
print(f"数据库路径: {db_path}")
print()

def query_table(table_name: str, columns: str = '*', condition: str = None):
    """查询表数据"""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        sql = f"SELECT {columns} FROM {table_name}"
        if condition:
            sql += f" WHERE {condition}"
        
        cursor.execute(sql)
        rows = cursor.fetchall()
        
        # 获取列名
        cursor.execute(f"PRAGMA table_info({table_name})")
        col_info = cursor.fetchall()
        col_names = [col[1] for col in col_info]
        
        return rows, col_names

def print_table_data(table_name: str, title: str = None, condition: str = None, columns: str = '*'):
    """打印表数据"""
    rows, col_names = query_table(table_name, columns, condition)
    
    if title:
        print(title)
    else:
        print(f"{table_name} 表数据:")
    
    print(f"  总行数: {len(rows)}")
    print()
    
    if len(rows) == 0:
        print("  (无数据)")
        print()
        return
    
    # 打印列名
    print("  列名:", col_names)
    print()
    
    # 打印数据（最多10行）
    max_rows = min(len(rows), 10)
    for i, row in enumerate(rows[:max_rows]):
        print(f"  行{i+1}: {row}")
    
    if len(rows) > max_rows:
        print(f"  ... (还有 {len(rows) - max_rows} 行)")
    
    print()

# 1. 检查任务表
print_table_data('detection_tasks', '1. 任务表 (detection_tasks)', columns='id, task_name, status, enabled')

# 2. 检查摄像机表
print_table_data('cameras', '2. 摄像机表 (cameras)', columns='id, name, rtsp_stream, source, ai_detection, enabled')

# 3. 检查通道池表
print_table_data('device_channel_pool', '3. 通道池表 (device_channel_pool)', columns='id, channel_name, rtsp_url, enabled')

# 4. 检查任务-通道绑定表
print_table_data('task_device_channel_bindings', '4. 任务-通道绑定表 (task_device_channel_bindings)', columns='task_id, channel_id, enabled, status')

# 5. 检查摄像机-通道绑定表
print_table_data('camera_device_channel_bindings', '5. 摄像机-通道绑定表 (camera_device_channel_bindings)', columns='camera_id, channel_id, enabled')

# 6. 详细检查：查询每个任务使用的通道信息
print("6. 任务使用的通道详细信息:")
print("-"*70)

with sqlite3.connect(db_path) as conn:
    cursor = conn.cursor()
    
    # 查询任务名称
    cursor.execute("SELECT id, task_name FROM detection_tasks")
    tasks = cursor.fetchall()
    
    for task_id, task_name in tasks:
        print(f"任务: {task_name} (ID={task_id})")
        
        # 查询该任务绑定的通道
        cursor.execute("""
            SELECT dc.id, dc.channel_name, dc.rtsp_url, dc.enabled
            FROM device_channel_pool dc
            INNER JOIN task_device_channel_bindings tcb ON dc.id = tcb.channel_id
            WHERE tcb.task_id = ? AND tcb.enabled = 1
        """, (task_id,))
        
        channels = cursor.fetchall()
        
        if len(channels) == 0:
            print("  (无绑定的通道)")
        else:
            for ch_id, ch_name, ch_rtsp, ch_enabled in channels:
                print(f"  通道ID={ch_id}, 名称={ch_name}, RTSP={ch_rtsp}, 启用={ch_enabled}")
        
        print()

# 7. 详细检查：查询每个通道关联的摄像机信息
print("7. 通道关联的摄像机信息:")
print("-"*70)

with sqlite3.connect(db_path) as conn:
    cursor = conn.cursor()
    
    # 查询所有通道
    cursor.execute("SELECT id, channel_name, rtsp_url FROM device_channel_pool")
    channels = cursor.fetchall()
    
    for ch_id, ch_name, ch_rtsp in channels:
        print(f"通道: {ch_name} (ID={ch_id}), RTSP={ch_rtsp}")
        
        # 查询关联的摄像机
        cursor.execute("""
            SELECT c.id, c.name, c.rtsp_stream, c.source
            FROM cameras c
            INNER JOIN camera_device_channel_bindings ccb ON c.id = ccb.camera_id
            WHERE ccb.channel_id = ? AND ccb.enabled = 1
        """, (ch_id,))
        
        cameras = cursor.fetchall()
        
        if len(cameras) == 0:
            print("  (无关联的摄像机)")
        else:
            for cam_id, cam_name, cam_rtsp, cam_source in cameras:
                print(f"  摄像机: {cam_name} (ID={cam_id}), RTSP={cam_rtsp}, Source={cam_source}")
                
                # 检查地址是否匹配
                if cam_rtsp != ch_rtsp and cam_source != ch_rtsp:
                    print(f"  ⚠️ 警告：地址不匹配！摄像机RTSP={cam_rtsp}, 通道RTSP={ch_rtsp}")
        
        print()

# 8. 检查RTSP地址是否重复或无效
print("8. RTSP地址检查:")
print("-"*70)

# 检查摄像机表的RTSP地址
with sqlite3.connect(db_path) as conn:
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, name, rtsp_stream, source FROM cameras")
    cameras = cursor.fetchall()
    
    print("摄像机表RTSP地址:")
    rtsp_urls = {}
    for cam_id, cam_name, cam_rtsp, cam_source in cameras:
        rtsp = cam_rtsp or cam_source
        print(f"  摄像机: {cam_name}, RTSP={rtsp}")
        
        if rtsp:
            if rtsp in rtsp_urls:
                print(f"    ⚠️ 警告：RTSP地址重复！已出现在 {rtsp_urls[rtsp]}")
            rtsp_urls[rtsp] = cam_name
    
    print()

# 检查通道池表的RTSP地址
print("通道池表RTSP地址:")
with sqlite3.connect(db_path) as conn:
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, channel_name, rtsp_url FROM device_channel_pool")
    channels = cursor.fetchall()
    
    channel_rtsp_urls = {}
    for ch_id, ch_name, ch_rtsp in channels:
        print(f"  通道: {ch_name}, RTSP={ch_rtsp}")
        
        if ch_rtsp:
            if ch_rtsp in channel_rtsp_urls:
                print(f"    ⚠️ 警告：RTSP地址重复！已出现在 {channel_rtsp_urls[ch_rtsp]}")
            channel_rtsp_urls[ch_rtsp] = ch_name

print()
print("="*70)
print("数据检查完成")
print("="*70)