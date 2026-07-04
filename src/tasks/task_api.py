"""
任务管理 REST API

提供任务、摄像机、任务-摄像机关联的 CRUD 操作接口。
支持多摄像机多任务架构。
"""

from flask import Blueprint, request, jsonify
from datetime import datetime
from typing import Dict, List
import sqlite3
import logging

# 创建 Blueprint
task_bp = Blueprint('tasks', __name__, url_prefix='/api')

# 日志器
logger = logging.getLogger(__name__)

# 任务管理器（延迟初始化）
_task_manager = None
_socketio = None


def get_task_manager():
    """获取任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        from src.tasks.task_manager import TaskManager
        _task_manager = TaskManager()
    return _task_manager


def set_socketio(socketio_instance):
    """设置 SocketIO 实例"""
    global _socketio
    _socketio = socketio_instance


# ========================================
# 摄像机管理 API
# ========================================

# ========================================
# 任务管理 API
# ========================================

@task_bp.route('/tasks', methods=['GET'])
def get_tasks():
    """获取所有任务（后端分页）"""
    enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'
    status = request.args.get('status')
    search = request.args.get('search', '')
    
    # 获取分页参数
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    
    task_manager = get_task_manager()
    tasks = task_manager.get_all_tasks(enabled_only=enabled_only, status=status)
    
    # 获取每个任务关联的设备通道数量
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    channel_pool_manager = get_device_channel_pool_manager()
    
    task_list = []
    for task in tasks:
        task_dict = task.to_dict()
        # 统计device_channel_pool表中该任务关联的设备通道数量
        channels = channel_pool_manager.get_task_device_channels(task.id)
        task_dict['channel_count'] = len(channels)
        task_list.append(task_dict)
    
    # 应用搜索筛选
    if search:
        search_lower = search.lower()
        task_list = [t for t in task_list if 
                     search_lower in t.get('task_name', '').lower()]
    
    # 后端分页
    total = len(task_list)
    total_pages = (total + per_page - 1) // per_page if per_page > 0 else 1
    start = (page - 1) * per_page
    end = start + per_page
    tasks_page = task_list[start:end]
    
    return jsonify({
        'tasks': tasks_page,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages
    })


@task_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id: int):
    """获取单个任务"""
    task_manager = get_task_manager()
    task = task_manager.get_task(task_id)
    
    if task:
        from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
        channel_pool_manager = get_device_channel_pool_manager()
        channels = channel_pool_manager.get_task_device_channels(task_id)
        
        task_dict = task.to_dict()
        task_dict['channels'] = [ch.to_dict() for ch in channels]
        
        return jsonify(task_dict)
    return jsonify({'error': '任务不存在'}), 404


@task_bp.route('/tasks', methods=['POST'])
def create_task():
    """创建任务"""
    data = request.json

    if not data:
        return jsonify({'error': '请求数据无效'}), 400

    # 必填字段检查
    if not data.get('task_name'):
        return jsonify({'error': '缺少必填字段: task_name'}), 400

    from src.tasks.task_manager import (
        DetectionTask,
        get_default_decoder_config,
        get_default_detector_config,
        get_default_tracker_config,
        get_default_motion_config,
        get_default_event_config,
        get_default_display_config
    )
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager, DeviceChannelPool
    from src.cameras.camera_manager import get_camera_manager

    # 配置优先级：前端传入 > 配置文件默认值
    # 如果前端未传配置或传空配置，使用配置文件的默认值
    decoder_config = data.get('decoder_config') if data.get('decoder_config') else get_default_decoder_config()
    detector_config = data.get('detector_config') if data.get('detector_config') else get_default_detector_config()
    tracker_config = data.get('tracker_config') if data.get('tracker_config') else get_default_tracker_config()
    motion_config = data.get('motion_config') if data.get('motion_config') else get_default_motion_config()
    event_config = data.get('event_config') if data.get('event_config') else get_default_event_config()
    display_config = data.get('display_config') if data.get('display_config') else get_default_display_config()

    # target_classes：如果前端传空数组，使用默认值
    target_classes = data.get('target_classes')
    if not target_classes:  # 空数组或None都使用默认值
        target_classes = ['person', 'car']

    task = DetectionTask(
        task_name=data.get('task_name', ''),
        enabled=data.get('enabled', True),
        detection_type=data.get('detection_type', 'realtime'),
        algorithm=data.get('algorithm', 'yolov11'),
        model_path=data.get('model_path', 'models/yolov11n.onnx'),
        model_type=data.get('model_type', 'yolov11n'),  # 新增：模型类型字段
        target_classes=target_classes,
        motion_detection_enabled=data.get('motion_detection_enabled', True),
        tracking_enabled=data.get('tracking_enabled', True),
        decoder_config=decoder_config,
        detector_config=detector_config,
        tracker_config=tracker_config,
        motion_config=motion_config,
        event_config=event_config,
        display_config=display_config
    )

    task_manager = get_task_manager()
    task_id = task_manager.add_task(task)

    # 处理临时通道（批量入库）
    temp_channels = data.get('temp_channels', [])
    existing_channel_ids = data.get('existing_channel_ids', [])

    if temp_channels or existing_channel_ids:
        channel_pool_manager = get_device_channel_pool_manager()
        camera_manager = get_camera_manager()

        # 1. 批量入库临时通道到 device_channel_pool 表
        channel_ids = []
        for temp_channel in temp_channels:
            channel = DeviceChannelPool(
                channel_name=temp_channel.get('channel_name', ''),
                rtsp_url=temp_channel.get('rtsp_url', ''),
                channel_source=temp_channel.get('channel_source', 'local_platform'),
                enabled=temp_channel.get('enabled', True)
            )
            channel_id = channel_pool_manager.add_device_channel(channel)
            channel_ids.append(channel_id)

            # 2. 更新摄像机表的 ai_detection 状态
            camera_id = temp_channel.get('camera_id')
            if camera_id:
                camera_manager.update_camera(camera_id, {'ai_detection': True})
                # 3. 创建摄像机与设备通道池的关联
                try:
                    channel_pool_manager.bind_camera_to_channel(camera_id, channel_id)
                    logger.info(f"摄像机与通道已关联: camera_id={camera_id}, channel_id={channel_id}")
                except Exception as bind_err:
                    logger.warning(f"关联摄像机与通道失败: camera_id={camera_id}, channel_id={channel_id}, error={bind_err}")

        # 4. 合并已存在的通道ID
        channel_ids.extend([int(id) for id in existing_channel_ids])

        # 5. 将通道绑定到任务
        for channel_id in channel_ids:
            channel_pool_manager.bind_device_channel_to_task(task_id, channel_id)

    # WebSocket 通知
    if _socketio:
        _socketio.emit('task_created', {'task_id': task_id})

    result_task = task_manager.get_task(task_id)

    return jsonify({
        'task_id': task_id,
        'message': '任务创建成功',
        'task': result_task.to_dict()
    }), 201


@task_bp.route('/tasks/<int:task_id>', methods=['PUT'])
def update_task(task_id: int):
    """更新任务"""
    data = request.json
    
    if not data:
        return jsonify({'error': '请求数据无效'}), 400
    
    task_manager = get_task_manager()
    success = task_manager.update_task(task_id, data)
    
    if success:
        task = task_manager.get_task(task_id)
        
        # WebSocket 通知
        if _socketio:
            _socketio.emit('task_updated', {'task_id': task_id})
        
        return jsonify({
            'message': '任务更新成功',
            'task': task.to_dict()
        })
    return jsonify({'error': '任务不存在'}), 404


@task_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id: int):
    """删除任务
    
    删除任务时会：
    1. 删除任务关联的所有设备通道（device_channel_pool）
    2. 删除任务本身
    3. 更新camera.ai_detection状态（如果设备不再被其他任务使用）
    """
    task_manager = get_task_manager()

    # 检查任务是否存在
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    from src.cameras.camera_manager import get_camera_manager

    channel_pool_manager = get_device_channel_pool_manager()
    camera_manager = get_camera_manager()

    # 1. 获取任务关联的所有设备通道
    channels = channel_pool_manager.get_task_device_channels(task_id)

    # 2. 删除每个设备通道
    for channel in channels:
        channel_id = channel.id

        # 检查该设备通道是否还被其他任务使用
        other_task_ids = channel_pool_manager.get_device_channel_tasks(channel_id)

        # 如果该设备通道只被当前任务使用，则删除并更新camera.ai_detection
        if len(other_task_ids) <= 1:  # 只被当前任务使用（或没有其他任务）
            # 删除设备通道
            channel_pool_manager.delete_device_channel(channel_id)

            # 更新camera.ai_detection状态（根据rtsp_url匹配）
            try:
                # 获取所有摄像机，查找匹配的摄像机
                all_cameras = camera_manager.get_all_cameras()
                matching_camera = None

                # 尝试通过rtsp_url匹配
                for camera in all_cameras:
                    if camera.rtsp_stream == channel.rtsp_url or camera.source == channel.rtsp_url:
                        matching_camera = camera
                        break

                # 如果找到匹配的摄像机，更新ai_detection状态
                if matching_camera:
                    camera_manager.update_camera(matching_camera.id, {'ai_detection': False})
                    logger.info(f"[TaskAPI] 更新摄像机AI检测状态: camera_id={matching_camera.id}, ai_detection=False")
            except Exception as e:
                logger.error(f"[TaskAPI] 更新摄像机AI检测状态失败: {e}")

    # 3. 删除任务本身（会自动删除task_device_channel_bindings表的关联记录）
    success = task_manager.delete_task(task_id)

    if success:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('task_deleted', {'task_id': task_id})

        return jsonify({'message': '任务删除成功'})
    return jsonify({'error': '任务删除失败'}), 500


@task_bp.route('/tasks/<int:task_id>/start', methods=['POST'])
def start_task(task_id: int):
    """启动任务"""
    task_manager = get_task_manager()
    
    # 获取任务和关联的设备通道
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    # 更新任务状态
    task_manager.update_task(task_id, {'status': 'running', 'enabled': True})

    # 更新所有绑定的设备通道状态
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    channel_pool_manager = get_device_channel_pool_manager()
    channels = channel_pool_manager.get_task_device_channels(task_id)
    
    for channel in channels:
        # 更新绑定状态为running
        with sqlite3.connect(channel_pool_manager.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE task_device_channel_bindings 
                SET status = 'running' 
                WHERE task_id = ? AND channel_id = ?
            ''', (task_id, channel.id))
            conn.commit()

    # WebSocket 通知
    if _socketio:
        _socketio.emit('task_started', {'task_id': task_id})

    return jsonify({'message': '任务已启动', 'task': task_manager.get_task(task_id).to_dict()})


@task_bp.route('/tasks/<int:task_id>/stop', methods=['POST'])
def stop_task(task_id: int):
    """停止任务"""
    task_manager = get_task_manager()
    
    # 获取任务和关联的设备通道
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    # 更新任务状态
    task_manager.update_task(task_id, {'status': 'stopped'})

    # 更新所有绑定的设备通道状态
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    channel_pool_manager = get_device_channel_pool_manager()
    channels = channel_pool_manager.get_task_device_channels(task_id)
    
    for channel in channels:
        # 更新绑定状态为stopped
        with sqlite3.connect(channel_pool_manager.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE task_device_channel_bindings 
                SET status = 'stopped' 
                WHERE task_id = ? AND channel_id = ?
            ''', (task_id, channel.id))
            conn.commit()

    # WebSocket 通知
    if _socketio:
        _socketio.emit('task_stopped', {'task_id': task_id})

    return jsonify({'message': '任务已停止', 'task': task_manager.get_task(task_id).to_dict()})


# ========================================
# 任务-摄像机关联 API
# ========================================

@task_bp.route('/tasks/<int:task_id>/cameras', methods=['GET'])
def get_task_cameras(task_id: int):
    """获取任务关联的摄像机ID列表"""
    task_manager = get_task_manager()
    camera_ids = task_manager.get_task_cameras(task_id)
    
    return jsonify(camera_ids)


@task_bp.route('/tasks/<int:task_id>/cameras/<camera_id>', methods=['POST'])
def bind_camera_to_task(task_id: int, camera_id: str):
    """将摄像机绑定到任务
    
    Args:
        task_id: 任务 ID
        camera_id: 摄像机 ID（UUID字符串）
    """
    task_manager = get_task_manager()
    
    # 检查任务是否存在
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    
    # 检查摄像机是否存在（从 camera_manager 获取）
    from src.cameras.camera_manager import get_camera_manager
    camera_manager = get_camera_manager()
    camera = camera_manager.get_camera(camera_id)
    if not camera:
        return jsonify({'error': '摄像机不存在'}), 404
    
    binding_id = task_manager.bind_camera_to_task(task_id, camera_id)
    
    if binding_id:
        # 绑定成功后，更新摄像机的AI检测状态为True
        if not camera.ai_detection:
            camera_manager.update_camera(camera_id, {'ai_detection': True})
            logger.info(f"[Task] 摄像机绑定到任务，启用AI检测: camera_id={camera_id}, task_id={task_id}")
        
        # WebSocket 通知
        if _socketio:
            _socketio.emit('camera_bound', {'task_id': task_id, 'camera_id': camera_id})
        
        return jsonify({
            'message': '摄像机绑定成功',
            'binding_id': binding_id,
            'task': task_manager.get_task(task_id).to_dict()
        })
    return jsonify({'error': '绑定失败或已存在'}), 400


@task_bp.route('/tasks/<int:task_id>/cameras/<camera_id>', methods=['DELETE'])
def unbind_camera_from_task(task_id: int, camera_id: str):
    """解除摄像机与任务的绑定
    
    Args:
        task_id: 任务 ID
        camera_id: 摄像机 ID（UUID字符串）
    """
    task_manager = get_task_manager()
    success = task_manager.unbind_camera_from_task(task_id, camera_id)
    
    if success:
        # 解除绑定后，检查摄像机是否还有其他任务绑定
        from src.cameras.camera_manager import get_camera_manager
        camera_manager = get_camera_manager()
        camera = camera_manager.get_camera(camera_id)
        
        # 获取该摄像机绑定的所有任务
        camera_tasks = task_manager.get_camera_tasks(camera_id)
        
        # 如果没有绑定任何任务，将AI检测状态设为False
        if len(camera_tasks) == 0 and camera and camera.ai_detection:
            camera_manager.update_camera(camera_id, {'ai_detection': False})
            logger.info(f"[Task] 摄像机解除所有任务绑定，禁用AI检测: camera_id={camera_id}")
        
        # WebSocket 通知
        if _socketio:
            _socketio.emit('camera_unbound', {'task_id': task_id, 'camera_id': camera_id})
        
        return jsonify({
            'message': '解除绑定成功',
            'task': task_manager.get_task(task_id).to_dict()
        })
    return jsonify({'error': '绑定不存在'}), 404


@task_bp.route('/cameras/<camera_id>/tasks', methods=['GET'])
def get_camera_tasks(camera_id: str):
    """获取摄像机关联的所有任务
    
    Args:
        camera_id: 摄像机 ID（UUID字符串）
    """
    task_manager = get_task_manager()
    tasks = task_manager.get_camera_tasks(camera_id)
    
    return jsonify([task.to_dict() for task in tasks])


# ========================================
# 算法管理 API
# ========================================

@task_bp.route('/algorithms', methods=['GET'])
def get_algorithms():
    """获取可用算法列表"""
    task_manager = get_task_manager()
    algorithms = task_manager.get_algorithms()
    
    return jsonify(algorithms)


# ========================================
# 事件管理 API
# ========================================

@task_bp.route('/events', methods=['GET'])
def get_events():
    """获取事件列表"""
    # 分页参数
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    offset = (page - 1) * per_page
    
    # 筛选参数
    task_id = request.args.get('task_id', type=int)
    camera_id = request.args.get('camera_id')
    algorithm = request.args.get('algorithm')
    class_name = request.args.get('class_name')
    
    task_manager = get_task_manager()
    events = task_manager.get_events(
        task_id=task_id, 
        camera_id=camera_id,
        algorithm=algorithm,
        limit=per_page, 
        offset=offset
    )
    
    # 获取总数
    total = task_manager.get_event_count(
        task_id=task_id,
        camera_id=camera_id,
        algorithm=algorithm
    )
    
    events_dict = [event.to_dict() for event in events]
    
    # 如果有 class_name 筛选，在这里过滤
    if class_name:
        events_dict = [e for e in events_dict if e.get('class_name') == class_name]
        total = len(events_dict)
    
    # 关联查询设备通道名称
    try:
        import sqlite3
        # 通过 JOIN 查询建立 camera_id -> channel_name 的映射
        db_path = channel_pool_manager.db_path if 'channel_pool_manager' in dir() else 'data/tasks.db'
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT b.camera_id, d.channel_name 
                FROM camera_device_channel_bindings b 
                JOIN device_channel_pool d ON b.channel_id = d.id
            ''')
            channel_name_map = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 为每个事件添加 channel_name 字段
        for event_dict in events_dict:
            camera_id_value = event_dict.get('camera_id', '')
            event_dict['channel_name'] = channel_name_map.get(camera_id_value, camera_id_value)
    except Exception as e:
        # 如果查询失败，使用 camera_id 作为显示名称
        for event_dict in events_dict:
            event_dict['channel_name'] = event_dict.get('camera_id', '-')
    
    return jsonify({
        'events': events_dict,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page if per_page > 0 else 1
    })


@task_bp.route('/events/<event_id>', methods=['GET'])
def get_event_detail(event_id):
    """获取单个事件详情"""
    task_manager = get_task_manager()
    
    # event_id 是 UUID 字符串，需要通过数据库查询
    events = task_manager.get_events(limit=1000)
    event = next((e for e in events if e.event_id == event_id), None)
    
    if event:
        event_dict = event.to_dict()
        # 添加 channel_name 字段
        try:
            import sqlite3
            with sqlite3.connect('data/tasks.db') as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT d.channel_name 
                    FROM camera_device_channel_bindings b 
                    JOIN device_channel_pool d ON b.channel_id = d.id
                    WHERE b.camera_id = ?
                ''', (event.camera_id,))
                row = cursor.fetchone()
                if row:
                    event_dict['channel_name'] = row[0]
        except Exception:
            pass
        return jsonify(event_dict)
    return jsonify({'error': '事件不存在'}), 404


@task_bp.route('/events/count', methods=['GET'])
def get_event_count():
    """获取事件数量"""
    task_id = request.args.get('task_id', type=int)
    camera_id = request.args.get('camera_id')
    algorithm = request.args.get('algorithm')
    
    task_manager = get_task_manager()
    count = task_manager.get_event_count(
        task_id=task_id, 
        camera_id=camera_id,
        algorithm=algorithm
    )
    
    return jsonify({'count': count})


@task_bp.route('/events/clear', methods=['POST'])
def clear_old_events():
    """清理旧事件"""
    days = request.args.get('days', 7, type=int)
    
    task_manager = get_task_manager()
    deleted = task_manager.clear_old_events(days=days)
    
    return jsonify({'deleted': deleted, 'message': f'清理了 {deleted} 条超过 {days} 天的事件'})


# ========================================
# 统计信息 API
# ========================================

@task_bp.route('/statistics', methods=['GET'])
def get_statistics():
    """获取系统统计信息"""
    task_manager = get_task_manager()
    stats = task_manager.get_statistics()
    
    return jsonify(stats)


# ========================================
# 系统状态 API
# ========================================

@task_bp.route('/status', methods=['GET'])
def get_status():
    """获取系统状态"""
    task_manager = get_task_manager()
    
    tasks = task_manager.get_all_tasks()
    running_tasks = [t for t in tasks if t.status == 'running']
    enabled_tasks = [t for t in tasks if t.enabled]
    
    algorithms = task_manager.get_algorithms()
    stats = task_manager.get_statistics()
    
    return jsonify({
        'total_tasks': stats['total_tasks'],
        'running_tasks': stats['running_tasks'],
        'enabled_tasks': stats['enabled_tasks'],
        'total_cameras': stats['total_cameras'],
        'enabled_cameras': stats['enabled_cameras'],
        'total_bindings': stats['total_bindings'],
        'total_events': stats['total_events'],
        'available_algorithms': len(algorithms),
        'running_tasks_detail': [t.to_dict() for t in running_tasks]
    })


# ========================================
# 健康检查
# ========================================

@task_bp.route('/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})


# ========================================
# 辅助函数
# ========================================

def register_task_blueprint(app, socketio=None):
    """注册任务 Blueprint 到 Flask 应用
    
    Args:
        app: Flask 应用实例
        socketio: SocketIO 实例（可选，用于实时推送）
    """
    app.register_blueprint(task_bp)
    
    # 设置 SocketIO 实例
    if socketio:
        set_socketio(socketio)
    
    # 初始化任务管理器（创建数据库表）
    from src.tasks.task_manager import get_task_manager
    manager = get_task_manager()
    
    print("[TaskAPI] 任务管理 Blueprint 已注册")
    print(f"[TaskAPI] 当前任务数量: {len(manager.get_all_tasks())}")