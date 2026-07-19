"""
检查任务的摄像机配置
"""
import sqlite3
import json

def check_task_cameras():
    """检查任务的摄像机配置"""
    conn = sqlite3.connect('data/tasks.db')
    cursor = conn.cursor()

    # 查询所有任务
    cursor.execute('''
        SELECT id, task_name, status, camera
        FROM tasks
        ORDER BY id
    ''')
    tasks = cursor.fetchall()

    print("=" * 80)
    print("任务列表:")
    print("=" * 80)

    for task_id, task_name, status, camera_json in tasks:
        print(f"\n任务 ID: {task_id}")
        print(f"任务名称: {task_name}")
        print(f"状态: {status}")

        # 解析 camera 字段（JSON数组）
        if camera_json:
            try:
                cameras = json.loads(camera_json)
                print(f"摄像机数量: {len(cameras)}")
                for i, cam in enumerate(cameras):
                    print(f"  摄像机 {i+1}:")
                    print(f"    camera_id: {cam.get('camera_id', 'N/A')}")
                    print(f"    rtsp_url: {cam.get('rtsp_url', 'N/A')[:50]}...")
            except Exception as e:
                print(f"  解析 camera 字段失败: {e}")

    # 查询任务-摄像机绑定表
    print("\n" + "=" * 80)
    print("任务-摄像机绑定表:")
    print("=" * 80)

    cursor.execute('''
        SELECT id, task_id, camera_id, status
        FROM task_camera_bindings
        ORDER BY task_id
    ''')
    bindings = cursor.fetchall()

    for binding_id, task_id, camera_id, status in bindings:
        print(f"绑定 ID: {binding_id}, 任务 ID: {task_id}, 摄像机 ID: {camera_id}, 状态: {status}")

    # 查询摄像机表
    print("\n" + "=" * 80)
    print("摄像机表:")
    print("=" * 80)

    cursor.execute('''
        SELECT id, camera_name, protocol, rtsp_url
        FROM cameras
        ORDER BY id
    ''')
    cameras = cursor.fetchall()

    for cam_id, cam_name, protocol, rtsp_url in cameras:
        print(f"摄像机 ID: {cam_id}, 名称: {cam_name}, 协议: {protocol}, URL: {rtsp_url[:50] if rtsp_url else 'N/A'}...")

    conn.close()

    print("\n" + "=" * 80)
    print("检查完成")
    print("=" * 80)


if __name__ == "__main__":
    check_task_cameras()