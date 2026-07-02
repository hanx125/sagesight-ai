"""
测试API响应
"""
import json
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.cameras.camera_manager import get_camera_manager
from src.cameras.directory_service import get_directory_service

# 测试目录API
print("=" * 60)
print("测试目录API")
print("=" * 60)

service = get_directory_service()
directories = service.get_all_directories()

print("\n1. 目录列表（directory_service）：")
for d in directories:
    print(f"  - ID: {d.id}, Name: {d.name}")
    dir_dict = d.to_dict()
    print(f"    to_dict(): {json.dumps(dir_dict, ensure_ascii=False, indent=2)}")

# 测试摄像机API
print("\n" + "=" * 60)
print("测试摄像机API")
print("=" * 60)

manager = get_camera_manager()
cameras = manager.get_all_cameras()

print("\n2. 摄像机列表（camera_manager）：")
for c in cameras:
    print(f"  - ID: {c.id}, Name: {c.name}")
    camera_dict = c.to_dict()
    print(f"    to_dict(): {json.dumps(camera_dict, ensure_ascii=False, indent=2)}")

# 测试目录下的摄像机
print("\n" + "=" * 60)
print("测试目录下的摄像机")
print("=" * 60)

for d in directories:
    cameras_in_dir = manager.get_cameras_by_directory(d.id)
    print(f"\n目录 {d.name} (ID: {d.id}) 下的摄像机数量: {len(cameras_in_dir)}")
    for c in cameras_in_dir:
        print(f"  - {c.name}")

print("\n测试完成！")