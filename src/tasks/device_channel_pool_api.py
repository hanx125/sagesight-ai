"""
设备通道检测池管理 REST API

提供设备通道检测池的 CRUD 操作接口。
"""

from flask import Blueprint, request, jsonify
from datetime import datetime
from typing import Dict, List
import logging

# 创建 Blueprint
device_channel_pool_bp = Blueprint('device_channel_pool', __name__, url_prefix='/api')

# 日志器
logger = logging.getLogger(__name__)

# 设备通道检测池管理器（延迟初始化）
_device_channel_pool_manager = None
_socketio = None


def get_device_channel_pool_manager():
    """获取设备通道检测池管理器实例"""
    global _device_channel_pool_manager
    if _device_channel_pool_manager is None:
        from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
        _device_channel_pool_manager = get_device_channel_pool_manager()
    return _device_channel_pool_manager


def set_socketio(socketio_instance):
    """设置 SocketIO 实例"""
    global _socketio
    _socketio = socketio_instance


def _emit_device_channel_event(event_type: str, channel_id: int = None, data: Dict = None):
    """发送设备通道WebSocket事件"""
    if _socketio:
        event_data = {'channel_id': channel_id} if channel_id else {}
        if data:
            event_data.update(data)
        _socketio.emit(event_type, event_data, namespace='/ws')
        logger.info(f"[DeviceChannelPoolAPI] WebSocket事件发送: {event_type}, channel_id={channel_id}")


# ========================================
# 设备通道检测池管理 API
# ========================================

@device_channel_pool_bp.route('/device-channel-pool', methods=['GET'])
def get_device_channels():
    """获取所有设备通道"""
    try:
        manager = get_device_channel_pool_manager()
        
        # 获取查询参数
        enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'
        channel_source = request.args.get('channel_source', None)
        search = request.args.get('search', None)
        
        # 获取设备通道列表
        channels = manager.get_all_device_channels(enabled_only=enabled_only, channel_source=channel_source)
        
        # 搜索过滤
        if search:
            search_lower = search.lower()
            channels = [ch for ch in channels 
                       if search_lower in ch.channel_name.lower() 
                       or search_lower in ch.rtsp_url.lower()]
        
        # 转换为字典列表
        result = [ch.to_dict() for ch in channels]
        
        logger.info(f"获取设备通道列表成功: 共{len(result)}个通道")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"获取设备通道列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/device-channel-pool/<int:channel_id>', methods=['GET'])
def get_device_channel(channel_id: int):
    """获取单个设备通道"""
    try:
        manager = get_device_channel_pool_manager()
        channel = manager.get_device_channel(channel_id)
        
        if not channel:
            return jsonify({'error': '设备通道不存在'}), 404
        
        logger.info(f"获取设备通道成功: ID={channel_id}")
        return jsonify(channel.to_dict())
        
    except Exception as e:
        logger.error(f"获取设备通道失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/device-channel-pool', methods=['POST'])
def create_device_channel():
    """创建设备通道"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        # 验证必填字段
        channel_name = data.get('channel_name', '').strip()
        rtsp_url = data.get('rtsp_url', '').strip()
        
        if not channel_name:
            return jsonify({'error': '设备通道名称不能为空'}), 400
        if not rtsp_url:
            return jsonify({'error': 'RTSP URL不能为空'}), 400
        
        # 获取可选字段
        channel_source = data.get('channel_source', 'local_platform')
        enabled = data.get('enabled', True)

        # 创建设备通道对象
        from src.tasks.device_channel_pool_manager import DeviceChannelPool
        channel = DeviceChannelPool(
            channel_name=channel_name,
            rtsp_url=rtsp_url,
            channel_source=channel_source,
            enabled=enabled
        )

        # 添加到数据库
        manager = get_device_channel_pool_manager()
        channel_id = manager.add_device_channel(channel)

        # 发送WebSocket事件
        _emit_device_channel_event('device_channel_created', channel_id=channel_id)
        
        logger.info(f"创建设备通道成功: ID={channel_id}, name={channel_name}")
        return jsonify({'id': channel_id, 'message': '创建成功'}), 201
        
    except Exception as e:
        logger.error(f"创建设备通道失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/device-channel-pool/<int:channel_id>', methods=['PUT'])
def update_device_channel(channel_id: int):
    """更新设备通道"""
    try:
        manager = get_device_channel_pool_manager()
        channel = manager.get_device_channel(channel_id)
        
        if not channel:
            return jsonify({'error': '设备通道不存在'}), 404
        
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        # 更新字段
        update_data = {}
        
        if 'channel_name' in data:
            channel_name = data['channel_name'].strip()
            if not channel_name:
                return jsonify({'error': '设备通道名称不能为空'}), 400
            update_data['channel_name'] = channel_name
        
        if 'rtsp_url' in data:
            rtsp_url = data['rtsp_url'].strip()
            if not rtsp_url:
                return jsonify({'error': 'RTSP URL不能为空'}), 400
            update_data['rtsp_url'] = rtsp_url
        
        if 'channel_source' in data:
            update_data['channel_source'] = data['channel_source']
        
        if 'enabled' in data:
            update_data['enabled'] = data['enabled']
        
        # 更新数据库
        success = manager.update_device_channel(channel_id, update_data)
        
        if success:
            # 发送WebSocket事件
            _emit_device_channel_event('device_channel_updated', channel_id=channel_id)
            
            logger.info(f"更新设备通道成功: ID={channel_id}")
            return jsonify({'message': '更新成功'})
        else:
            return jsonify({'error': '更新失败'}), 500
        
    except Exception as e:
        logger.error(f"更新设备通道失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/device-channel-pool/<int:channel_id>', methods=['DELETE'])
def delete_device_channel(channel_id: int):
    """删除设备通道"""
    try:
        manager = get_device_channel_pool_manager()
        channel = manager.get_device_channel(channel_id)
        
        if not channel:
            return jsonify({'error': '设备通道不存在'}), 404
        
        # 检查是否绑定到任务
        tasks = manager.get_device_channel_tasks(channel_id)
        if tasks:
            return jsonify({'error': f'设备通道已绑定到{len(tasks)}个任务，请先解除绑定'}), 400
        
        # 检查是否绑定到摄像机
        from src.cameras.camera_manager import get_camera_manager
        camera_manager = get_camera_manager()
        cameras = camera_manager.get_channel_cameras(channel_id)
        if cameras:
            return jsonify({'error': f'设备通道已关联到{len(cameras)}个摄像机，请先解除关联'}), 400
        
        # 删除设备通道
        success = manager.delete_device_channel(channel_id)
        
        if success:
            # 发送WebSocket事件
            _emit_device_channel_event('device_channel_deleted', channel_id=channel_id)
            
            logger.info(f"删除设备通道成功: ID={channel_id}")
            return jsonify({'message': '删除成功'})
        else:
            return jsonify({'error': '删除失败'}), 500
        
    except Exception as e:
        logger.error(f"删除设备通道失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# 任务-设备通道关联 API
# ========================================

@device_channel_pool_bp.route('/tasks/<int:task_id>/channels', methods=['GET'])
def get_task_channels(task_id: int):
    """获取任务绑定的设备通道"""
    try:
        manager = get_device_channel_pool_manager()
        channels = manager.get_task_device_channels(task_id)
        
        result = [ch.to_dict() for ch in channels]
        
        logger.info(f"获取任务设备通道成功: task_id={task_id}, 共{len(result)}个通道")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"获取任务设备通道失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# 摄像机-设备通道池关联 API
# ========================================

@device_channel_pool_bp.route('/cameras/<camera_id>/channels', methods=['GET'])
def get_camera_channels(camera_id: str):
    """获取摄像机关联的设备通道池"""
    try:
        from src.cameras.camera_manager import get_camera_manager
        camera_manager = get_camera_manager()
        
        channels = camera_manager.get_camera_channels(camera_id)
        
        logger.info(f"获取摄像机设备通道成功: camera_id={camera_id}, 共{len(channels)}个通道")
        return jsonify(channels)
        
    except Exception as e:
        logger.error(f"获取摄像机设备通道失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/cameras/<camera_id>/channels', methods=['POST'])
def bind_camera_to_channel(camera_id: str):
    """将摄像机绑定到设备通道池"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        channel_id = data.get('channel_id')
        
        if not channel_id:
            return jsonify({'error': '设备通道池ID不能为空'}), 400
        
        # 检查摄像机是否存在
        from src.cameras.camera_manager import get_camera_manager
        camera_manager = get_camera_manager()
        camera = camera_manager.get_camera(camera_id)
        if not camera:
            return jsonify({'error': '摄像机不存在'}), 404
        
        # 检查设备通道池是否存在
        manager = get_device_channel_pool_manager()
        channel = manager.get_device_channel(channel_id)
        if not channel:
            return jsonify({'error': '设备通道池不存在'}), 404
        
        # 绑定摄像机到设备通道池
        binding_id = camera_manager.bind_camera_to_channel(camera_id, channel_id)
        
        if binding_id > 0:
            # 发送WebSocket事件
            _emit_device_channel_event('camera_channel_bound', channel_id=channel_id, data={'camera_id': camera_id})
            
            logger.info(f"绑定摄像机到设备通道池成功: camera_id={camera_id}, channel_id={channel_id}")
            return jsonify({'id': binding_id, 'message': '绑定成功'}), 201
        else:
            return jsonify({'message': '绑定关系已存在'}), 200
        
    except Exception as e:
        logger.error(f"绑定摄像机到设备通道池失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/cameras/<camera_id>/channels/<int:channel_id>', methods=['DELETE'])
def unbind_camera_from_channel(camera_id: str, channel_id: int):
    """解除摄像机与设备通道池的绑定"""
    try:
        from src.cameras.camera_manager import get_camera_manager
        camera_manager = get_camera_manager()
        
        # 解除绑定
        success = camera_manager.unbind_camera_from_channel(camera_id, channel_id)
        
        if success:
            # 发送WebSocket事件
            _emit_device_channel_event('camera_channel_unbound', channel_id=channel_id, data={'camera_id': camera_id})
            
            logger.info(f"解除摄像机绑定成功: camera_id={camera_id}, channel_id={channel_id}")
            return jsonify({'message': '解除绑定成功'})
        else:
            return jsonify({'error': '解除绑定失败'}), 500
        
    except Exception as e:
        logger.error(f"解除摄像机绑定失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/device-channel-pool/<int:channel_id>/cameras', methods=['GET'])
def get_channel_cameras(channel_id: int):
    """获取设备通道池关联的所有摄像机"""
    try:
        manager = get_device_channel_pool_manager()
        cameras = manager.get_channel_cameras(channel_id)
        
        logger.info(f"获取设备通道摄像机成功: channel_id={channel_id}, 共{len(cameras)}个摄像机")
        return jsonify(cameras)
        
    except Exception as e:
        logger.error(f"获取设备通道摄像机失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/tasks/<int:task_id>/channels', methods=['POST'])
def bind_channel_to_task(task_id: int):
    """绑定设备通道到任务"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        channel_id = data.get('channel_id')
        
        if not channel_id:
            return jsonify({'error': '设备通道ID不能为空'}), 400
        
        manager = get_device_channel_pool_manager()
        
        # 检查任务是否存在
        task = manager.get_task(task_id)
        if not task:
            return jsonify({'error': '任务不存在'}), 404
        
        # 检查设备通道是否存在
        channel = manager.get_device_channel(channel_id)
        if not channel:
            return jsonify({'error': '设备通道不存在'}), 404
        
        # 绑定设备通道到任务
        binding_id = manager.bind_device_channel_to_task(task_id, channel_id)
        
        # 发送WebSocket事件
        _emit_device_channel_event('device_channel_bound', channel_id=channel_id, data={'task_id': task_id})
        
        logger.info(f"绑定设备通道到任务成功: task_id={task_id}, channel_id={channel_id}")
        return jsonify({'id': binding_id, 'message': '绑定成功'}), 201
        
    except Exception as e:
        logger.error(f"绑定设备通道到任务失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/tasks/<int:task_id>/channels/<int:channel_id>', methods=['DELETE'])
def unbind_channel_from_task(task_id: int, channel_id: int):
    """解除设备通道与任务的绑定"""
    try:
        manager = get_device_channel_pool_manager()
        
        # 解除绑定
        success = manager.unbind_device_channel_from_task(task_id, channel_id)
        
        if success:
            # 发送WebSocket事件
            _emit_device_channel_event('device_channel_unbound', channel_id=channel_id, data={'task_id': task_id})
            
            logger.info(f"解除设备通道绑定成功: task_id={task_id}, channel_id={channel_id}")
            return jsonify({'message': '解除绑定成功'})
        else:
            return jsonify({'error': '解除绑定失败'}), 500
        
    except Exception as e:
        logger.error(f"解除设备通道绑定失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/device-channel-pool/<int:channel_id>/tasks', methods=['GET'])
def get_channel_tasks(channel_id: int):
    """获取设备通道绑定的所有任务"""
    try:
        manager = get_device_channel_pool_manager()
        tasks = manager.get_device_channel_tasks(channel_id)
        
        result = [task.to_dict() for task in tasks]
        
        logger.info(f"获取设备通道任务成功: channel_id={channel_id}, 共{len(result)}个任务")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"获取设备通道任务失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# 批量操作 API
# ========================================

@device_channel_pool_bp.route('/device-channel-pool/batch-create', methods=['POST'])
def batch_create_channels():
    """批量创建设备通道"""
    try:
        data = request.get_json()
        
        if not data or not isinstance(data.get('channels'), list):
            return jsonify({'error': '请求数据无效'}), 400
        
        manager = get_device_channel_pool_manager()
        results = []
        success_count = 0
        fail_count = 0

        from src.tasks.device_channel_pool_manager import DeviceChannelPool

        for channel_data in data['channels']:
            try:
                channel = DeviceChannelPool(
                    channel_name=channel_data.get('channel_name', '').strip(),
                    rtsp_url=channel_data.get('rtsp_url', '').strip(),
                    channel_source=channel_data.get('channel_source', 'local_platform'),
                    enabled=channel_data.get('enabled', True)
                )

                if not channel.channel_name or not channel.rtsp_url:
                    results.append({'error': '通道名称或RTSP URL为空'})
                    fail_count += 1
                    continue

                channel_id = manager.add_device_channel(channel)
                results.append({'id': channel_id, 'message': '创建成功'})
                success_count += 1
                
            except Exception as e:
                results.append({'error': str(e)})
                fail_count += 1
        
        # 发送WebSocket事件
        _emit_device_channel_event('device_channel_batch_created', data={'count': success_count})
        
        logger.info(f"批量创建设备通道完成: 成功{success_count}个, 失败{fail_count}个")
        return jsonify({
            'results': results,
            'success_count': success_count,
            'fail_count': fail_count
        })
        
    except Exception as e:
        logger.error(f"批量创建设备通道失败: {e}")
        return jsonify({'error': str(e)}), 500


@device_channel_pool_bp.route('/device-channel-pool/batch-delete', methods=['POST'])
def batch_delete_channels():
    """批量删除设备通道"""
    try:
        data = request.get_json()
        
        if not data or not isinstance(data.get('channel_ids'), list):
            return jsonify({'error': '请求数据无效'}), 400
        
        manager = get_device_channel_pool_manager()
        results = []
        success_count = 0
        fail_count = 0
        
        for channel_id in data['channel_ids']:
            try:
                channel = manager.get_device_channel(channel_id)
                
                if not channel:
                    results.append({'channel_id': channel_id, 'error': '设备通道不存在'})
                    fail_count += 1
                    continue
                
                # 检查是否绑定到任务
                tasks = manager.get_device_channel_tasks(channel_id)
                if tasks:
                    results.append({'channel_id': channel_id, 'error': f'已绑定到{len(tasks)}个任务'})
                    fail_count += 1
                    continue
                
                success = manager.delete_device_channel(channel_id)
                
                if success:
                    results.append({'channel_id': channel_id, 'message': '删除成功'})
                    success_count += 1
                else:
                    results.append({'channel_id': channel_id, 'error': '删除失败'})
                    fail_count += 1
                
            except Exception as e:
                results.append({'channel_id': channel_id, 'error': str(e)})
                fail_count += 1
        
        # 发送WebSocket事件
        _emit_device_channel_event('device_channel_batch_deleted', data={'count': success_count})
        
        logger.info(f"批量删除设备通道完成: 成功{success_count}个, 失败{fail_count}个")
        return jsonify({
            'results': results,
            'success_count': success_count,
            'fail_count': fail_count
        })
        
    except Exception as e:
        logger.error(f"批量删除设备通道失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# Blueprint 注册函数
# ========================================

def register_device_channel_pool_blueprint(app, socketio_instance):
    """注册设备通道检测池 Blueprint 到 Flask 应用
    
    Args:
        app: Flask 应用实例
        socketio_instance: SocketIO 实例
    """
    # 设置 SocketIO 实例
    set_socketio(socketio_instance)
    
    # 注册 Blueprint
    app.register_blueprint(device_channel_pool_bp)
    
    logger.info("[DeviceChannelPoolAPI] 设备通道检测池 Blueprint 已注册")