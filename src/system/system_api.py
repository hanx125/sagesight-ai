"""
SageSight AI - 系统管理 API 接口
提供流媒体服务器配置等系统设置的 REST API
"""

import logging
from flask import Blueprint, jsonify, request
from src.system.system_manager import get_system_manager, StreamingServer

logger = logging.getLogger(__name__)

# 创建 Blueprint
system_bp = Blueprint('system', __name__, url_prefix='/api/system')

# WebSocket 实例（可选）
_socketio = None


def register_system_blueprint(socketio=None):
    """注册系统管理 Blueprint"""
    global _socketio
    _socketio = socketio
    
    # 初始化系统管理器（创建数据库表和默认配置）
    from src.system.system_manager import get_system_manager
    manager = get_system_manager()
    logger.info(f"系统管理器已初始化，流媒体服务器数量: {len(manager.get_all_streaming_servers())}")
    
    return system_bp


# ========================================
# 流媒体服务器 API
# ========================================

@system_bp.route('/streaming-servers', methods=['GET'])
def get_streaming_servers():
    """获取所有流媒体服务器配置"""
    manager = get_system_manager()
    servers = manager.get_all_streaming_servers()
    return jsonify([server.to_dict() for server in servers])


@system_bp.route('/streaming-servers/<int:server_id>', methods=['GET'])
def get_streaming_server(server_id: int):
    """获取单个流媒体服务器配置"""
    manager = get_system_manager()
    server = manager.get_streaming_server(server_id)
    
    if server:
        return jsonify(server.to_dict())
    return jsonify({'error': '流媒体服务器不存在'}), 404


@system_bp.route('/streaming-servers/default', methods=['GET'])
def get_default_streaming_server():
    """获取默认流媒体服务器"""
    manager = get_system_manager()
    server = manager.get_default_streaming_server()
    
    if server:
        return jsonify(server.to_dict())
    return jsonify({'error': '未配置默认流媒体服务器'}), 404


@system_bp.route('/streaming-servers', methods=['POST'])
def create_streaming_server():
    """创建流媒体服务器配置"""
    data = request.get_json()
    
    if not data or not data.get('name'):
        return jsonify({'error': '缺少必要参数'}), 400
    
    manager = get_system_manager()
    
    server = StreamingServer(
        name=data['name'],
        server_type=data.get('server_type', 'zlmediakit'),
        host=data.get('host', 'localhost'),
        rtmp_port=data.get('rtmp_port', 1935),
        rtsp_port=data.get('rtsp_port', 554),
        http_port=data.get('http_port', 88),
        rtc_port=data.get('rtc_port', 8000),
        secret=data.get('secret', ''),
        media_server_id=data.get('media_server_id', ''),
        hook_url_prefix=data.get('hook_url_prefix', ''),
        protocol=data.get('protocol', 'rtmp'),
        enabled=data.get('enabled', True),
        is_default=data.get('is_default', False),
        description=data.get('description', '')
    )
    
    server_id = manager.create_streaming_server(server)
    
    if server_id:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('streaming_server_created', {'server_id': server_id})
        
        return jsonify({
            'message': '流媒体服务器创建成功',
            'server_id': server_id,
            'server': manager.get_streaming_server(server_id).to_dict()
        })
    
    return jsonify({'error': '创建失败'}), 500


@system_bp.route('/streaming-servers/<int:server_id>', methods=['PUT'])
def update_streaming_server(server_id: int):
    """更新流媒体服务器配置"""
    data = request.get_json()
    
    if not data:
        return jsonify({'error': '缺少更新数据'}), 400
    
    manager = get_system_manager()
    existing = manager.get_streaming_server(server_id)
    
    if not existing:
        return jsonify({'error': '流媒体服务器不存在'}), 404
    
    # 更新字段
    server = StreamingServer(
        id=server_id,
        name=data.get('name', existing.name),
        server_type=data.get('server_type', existing.server_type),
        host=data.get('host', existing.host),
        rtmp_port=data.get('rtmp_port', existing.rtmp_port),
        rtsp_port=data.get('rtsp_port', existing.rtsp_port),
        http_port=data.get('http_port', existing.http_port),
        rtc_port=data.get('rtc_port', existing.rtc_port),
        secret=data.get('secret', existing.secret),
        media_server_id=data.get('media_server_id', existing.media_server_id),
        hook_url_prefix=data.get('hook_url_prefix', existing.hook_url_prefix),
        protocol=data.get('protocol', existing.protocol),
        enabled=data.get('enabled', existing.enabled),
        is_default=data.get('is_default', existing.is_default),
        description=data.get('description', existing.description),
        status=existing.status
    )
    
    success = manager.update_streaming_server(server_id, server)
    
    if success:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('streaming_server_updated', {'server_id': server_id})
        
        return jsonify({
            'message': '流媒体服务器更新成功',
            'server': manager.get_streaming_server(server_id).to_dict()
        })
    
    return jsonify({'error': '更新失败'}), 500


@system_bp.route('/streaming-servers/<int:server_id>', methods=['DELETE'])
def delete_streaming_server(server_id: int):
    """删除流媒体服务器配置"""
    manager = get_system_manager()
    
    # 检查是否存在
    server = manager.get_streaming_server(server_id)
    if not server:
        return jsonify({'error': '流媒体服务器不存在'}), 404
    
    # 检查是否为默认服务器
    if server.is_default:
        return jsonify({'error': '无法删除默认流媒体服务器'}), 400
    
    success = manager.delete_streaming_server(server_id)
    
    if success:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('streaming_server_deleted', {'server_id': server_id})
        
        return jsonify({'message': '流媒体服务器删除成功'})
    
    return jsonify({'error': '删除失败'}), 500


@system_bp.route('/streaming-servers/<int:server_id>/status', methods=['GET'])
def check_streaming_server_status(server_id: int):
    """检查流媒体服务器状态"""
    manager = get_system_manager()
    status = manager.check_server_status(server_id)
    
    return jsonify({
        'server_id': server_id,
        'status': status
    })


@system_bp.route('/streaming-servers/<int:server_id>/set-default', methods=['POST'])
def set_default_streaming_server(server_id: int):
    """设置默认流媒体服务器"""
    manager = get_system_manager()
    
    server = manager.get_streaming_server(server_id)
    if not server:
        return jsonify({'error': '流媒体服务器不存在'}), 404
    
    # 更新为默认服务器
    server.is_default = True
    server.enabled = True
    success = manager.update_streaming_server(server_id, server)
    
    if success:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('streaming_server_updated', {'server_id': server_id})
        
        return jsonify({
            'message': '已设置为默认流媒体服务器',
            'server': manager.get_streaming_server(server_id).to_dict()
        })
    
    return jsonify({'error': '设置失败'}), 500


# ========================================
# 系统状态 API
# ========================================

@system_bp.route('/status', methods=['GET'])
def get_system_status():
    """获取系统整体状态"""
    manager = get_system_manager()
    servers = manager.get_all_streaming_servers()
    
    online_count = sum(1 for s in servers if s.status == 'online')
    offline_count = sum(1 for s in servers if s.status == 'offline')
    unknown_count = sum(1 for s in servers if s.status == 'unknown')
    
    return jsonify({
        'streaming_servers': {
            'total': len(servers),
            'online': online_count,
            'offline': offline_count,
            'unknown': unknown_count
        }
    })