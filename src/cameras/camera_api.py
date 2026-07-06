"""
摄像机管理API

提供摄像机管理的REST API接口。
"""

import logging
from flask import Blueprint, request, jsonify

from src.cameras.camera_manager import Camera, CameraDirectory, get_camera_manager
from src.cameras.onvif_client import discover_onvif_cameras, connect_onvif_camera, ONVIF_AVAILABLE
from src.cameras.stream_service import get_stream_service

logger = logging.getLogger(__name__)

# 创建Blueprint
camera_bp = Blueprint('camera', __name__, url_prefix='/api/cameras')

# SocketIO实例（在注册时设置）
_socketio = None


def _emit_camera_event(event_name, camera_id=None, camera_data=None):
    """发送摄像机相关WebSocket事件"""
    if _socketio:
        data = {'camera_id': camera_id}
        if camera_data:
            data.update(camera_data)
        _socketio.emit(event_name, data, namespace='/ws')


# ========================================
# 摄像机管理API
# ========================================

@camera_bp.route('', methods=['GET'])
def get_cameras():
    """获取摄像机列表（后端分页）"""
    try:
        manager = get_camera_manager()
        
        # 获取筛选参数
        status = request.args.get('status', '')
        enabled = request.args.get('enabled', '')
        search = request.args.get('search', '')
        directory_id = request.args.get('directory_id', '')
        
        # 获取分页参数
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        
        cameras = manager.get_all_cameras()
        
        # 应用筛选
        if status:
            cameras = [c for c in cameras if c.status == status]
        
        if enabled == 'true':
            cameras = [c for c in cameras if c.enabled]
        elif enabled == 'false':
            cameras = [c for c in cameras if not c.enabled]
        
        if search:
            search_lower = search.lower()
            cameras = [c for c in cameras if 
                       search_lower in c.name.lower() or 
                       search_lower in c.ip.lower()]
        
        if directory_id:
            cameras = [c for c in cameras if c.directory_id == int(directory_id)]
        
        # 后端分页
        total = len(cameras)
        total_pages = (total + per_page - 1) // per_page if per_page > 0 else 1
        start = (page - 1) * per_page
        end = start + per_page
        cameras_page = cameras[start:end]
        
        return jsonify({
            'cameras': [c.to_dict() for c in cameras_page],
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages
        })
        
    except Exception as e:
        logger.error(f"获取摄像机列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/statistics', methods=['GET'])
def get_statistics():
    """获取摄像机统计信息"""
    try:
        manager = get_camera_manager()
        stats = manager.get_statistics()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/<camera_id>', methods=['GET'])
def get_camera(camera_id):
    """获取单个摄像机详情"""
    try:
        manager = get_camera_manager()
        camera = manager.get_camera(camera_id)
        
        if not camera:
            return jsonify({'error': '摄像机不存在'}), 404
        
        return jsonify(camera.to_dict())
        
    except Exception as e:
        logger.error(f"获取摄像机详情失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('', methods=['POST'])
def create_camera():
    """创建摄像机（手动添加）"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        # 必填字段验证
        name = data.get('name', '').strip()
        source = data.get('source', '').strip()
        
        if not name:
            return jsonify({'error': '摄像机名称不能为空'}), 400
        
        if not source:
            return jsonify({'error': '取流地址不能为空'}), 400
        
        manager = get_camera_manager()
        
        # 创建摄像机对象
        camera = Camera(
            name=name,
            source=source,
            ip=data.get('ip', ''),
            port=data.get('port', 80),
            username=data.get('username', ''),
            password=data.get('password', ''),
            manufacturer=data.get('manufacturer', ''),
            model=data.get('model', ''),
            protocol=data.get('protocol', 'rtsp'),  # 手动添加，默认RTSP协议
            ai_detection=data.get('ai_detection', False),  # 默认未检测，只有添加到任务池才启用
            description=data.get('description', ''),
            enabled=data.get('enabled', True),
            directory_id=data.get('directory_id')
        )
        
        camera_id = manager.add_camera(camera)
        
        # 自动启动流，填充播放地址字段
        stream_started = False
        stream_error = None
        try:
            stream_service = get_stream_service()
            result = stream_service.start_stream(camera_id)
            stream_started = result.get('success', False)
            if stream_started:
                logger.info(f"摄像机流已自动启动: camera_id={camera_id}")
            else:
                stream_error = result.get('message', '未知错误')
                logger.warning(f"自动启动流失败: camera_id={camera_id}, error={stream_error}")
        except Exception as stream_err:
            stream_error = str(stream_err)
            logger.warning(f"自动启动流异常: camera_id={camera_id}, error={stream_error}")
        
        # 发送WebSocket事件通知前端刷新列表
        _emit_camera_event('camera_created', camera_id=camera_id)
        
        response_data = {'id': camera_id, 'message': '创建成功'}
        if not stream_started:
            response_data['stream_warning'] = f'流启动失败: {stream_error}'
        
        logger.info(f"创建摄像机成功: {camera_id}")
        return jsonify(response_data), 201
        
    except Exception as e:
        logger.error(f"创建摄像机失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/onvif/register', methods=['POST'])
def register_camera_by_onvif():
    """通过ONVIF注册摄像机"""
    try:
        if not ONVIF_AVAILABLE:
            return jsonify({'error': 'ONVIF库未安装，无法使用ONVIF功能'}), 400
        
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        # 必填参数
        ip = data.get('ip', '').strip()
        port = data.get('port', 80)
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not ip:
            return jsonify({'error': 'IP地址不能为空'}), 400
        
        # 连接ONVIF摄像机获取信息
        try:
            camera_info = connect_onvif_camera(ip, port, username, password, timeout=10)
        except RuntimeError as e:
            return jsonify({'error': str(e)}), 400
        
        manager = get_camera_manager()
        
        # 检查是否已存在（按MAC或IP）
        existing = None
        if camera_info.get('mac'):
            existing = manager.get_camera_by_mac(camera_info['mac'])
        if not existing:
            existing = manager.get_camera_by_ip(ip)
        
        if existing:
            # 更新现有摄像机
            update_data = {
                'name': data.get('name') or existing.name,
                'source': camera_info['source'],
                'manufacturer': camera_info['manufacturer'],
                'model': camera_info['model'],
                'firmware_version': camera_info['firmware_version'],
                'serial_number': camera_info['serial_number'],
                'hardware_id': camera_info['hardware_id'],
                'mac': camera_info['mac'],
                'support_move': camera_info['support_move'],
                'support_zoom': camera_info['support_zoom'],
                'ptz_address': camera_info['ptz_address'],
                'support_audio_talk': camera_info['support_audio_talk'],
                'protocol': 'onvif',  # ONVIF更新，接入协议为onvif
                'status': 'online',
                'enabled': True
            }
            manager.update_camera(existing.id, update_data)
            return jsonify({'id': existing.id, 'message': '摄像机已更新', 'info': camera_info})
        
        # 创建新摄像机
        camera = Camera(
            name=data.get('name', f"{camera_info['manufacturer']} {camera_info['model']}"),
            source=camera_info['source'],
            ip=camera_info['ip'],
            port=camera_info['port'],
            username=camera_info['username'],
            password=camera_info['password'],
            mac=camera_info['mac'],
            manufacturer=camera_info['manufacturer'],
            model=camera_info['model'],
            firmware_version=camera_info['firmware_version'],
            serial_number=camera_info['serial_number'],
            hardware_id=camera_info['hardware_id'],
            support_move=camera_info['support_move'],
            support_zoom=camera_info['support_zoom'],
            ptz_address=camera_info['ptz_address'],
            support_audio_talk=camera_info['support_audio_talk'],
            protocol='onvif',  # ONVIF注册，接入协议为onvif
            ai_detection=False,  # 默认未检测，只有添加到任务池才启用
            status='online',
            enabled=True,
            directory_id=data.get('directory_id')
        )
        
        camera_id = manager.add_camera(camera)
        
        # 自动启动流，填充播放地址字段
        try:
            stream_service = get_stream_service()
            stream_service.start_stream(camera_id)
            logger.info(f"摄像机流已自动启动: camera_id={camera_id}")
        except Exception as stream_err:
            logger.warning(f"自动启动流失败: {stream_err}")
        
        # 发送WebSocket事件通知前端刷新列表
        _emit_camera_event('camera_created', camera_id=camera_id)
        
        response_data = {'id': camera_id, 'message': '注册成功', 'info': camera_info}
        
        logger.info(f"通过ONVIF注册摄像机成功: {camera_id}")
        return jsonify(response_data), 201
        
    except Exception as e:
        logger.error(f"ONVIF注册失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/onvif/discover', methods=['GET'])
def discover_cameras():
    """发现网络中的ONVIF摄像机"""
    try:
        timeout = int(request.args.get('timeout', 5))
        cameras = discover_onvif_cameras(timeout=timeout)
        return jsonify(cameras)
    except Exception as e:
        logger.error(f"ONVIF发现失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/onvif/connect', methods=['POST'])
def test_onvif_connection():
    """测试ONVIF连接"""
    try:
        if not ONVIF_AVAILABLE:
            return jsonify({'error': 'ONVIF库未安装'}), 400
        
        data = request.get_json()
        
        ip = data.get('ip', '').strip()
        port = data.get('port', 80)
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not ip:
            return jsonify({'error': 'IP地址不能为空'}), 400
        
        try:
            camera_info = connect_onvif_camera(ip, port, username, password, timeout=10)
            return jsonify({'success': True, 'info': camera_info})
        except RuntimeError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
            
    except Exception as e:
        logger.error(f"测试ONVIF连接失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/network/scan', methods=['POST'])
def scan_network_ports():
    """扫描网络端口（支持跨网段）"""
    try:
        data = request.get_json()

        # 获取扫描参数
        ip_range = data.get('ip_range', '').strip()
        ports = data.get('ports', '80,8080').strip()
        check_onvif = data.get('check_onvif', True)
        timeout = data.get('timeout', 3)

        if not ip_range:
            return jsonify({'error': 'IP范围不能为空'}), 400

        # 导入网络扫描模块
        from src.cameras.network_scanner import scan_network_ports as do_scan

        # 开始扫描（不使用用户名和密码，扫描结果在注册时填写认证信息）
        devices = do_scan(
            ip_range=ip_range,
            ports=ports,
            username='',
            password='',
            check_onvif=check_onvif,
            timeout=timeout
        )

        logger.info(f"网络扫描完成: {ip_range}, 端口: {ports}, 发现设备: {len(devices)}")
        return jsonify({'devices': devices})

    except Exception as e:
        logger.error(f"网络扫描失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/<camera_id>', methods=['PUT'])
def update_camera(camera_id):
    """更新摄像机"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        manager = get_camera_manager()
        camera = manager.get_camera(camera_id)
        
        if not camera:
            return jsonify({'error': '摄像机不存在'}), 404
        
        # 更新数据
        manager.update_camera(camera_id, data)
        
        logger.info(f"更新摄像机成功: {camera_id}")
        return jsonify({'message': '更新成功'})
        
    except Exception as e:
        logger.error(f"更新摄像机失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/<camera_id>', methods=['DELETE'])
def delete_camera(camera_id):
    """删除摄像机"""
    try:
        manager = get_camera_manager()
        camera = manager.get_camera(camera_id)
        
        if not camera:
            return jsonify({'error': '摄像机不存在'}), 404
        
        # 先停止流，防止ZLM产生僵尸流
        try:
            stream_service = get_stream_service()
            stop_result = stream_service.stop_stream(camera_id)
            logger.info(f"[CameraAPI] 删除前停止流成功: camera_id={camera_id}")
        except Exception as stream_err:
            # 流停止失败不影响删除，记录日志即可
            logger.warning(f"[CameraAPI] 删除前停止流失败（不影响删除）: camera_id={camera_id}, error={stream_err}")
        
        # 删除摄像机数据库记录
        manager.delete_camera(camera_id)
        
        logger.info(f"删除摄像机成功: {camera_id}")
        return jsonify({'message': '删除成功'})
        
    except Exception as e:
        logger.error(f"删除摄像机失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/batch-update-directory', methods=['POST'])
def batch_update_camera_directory():
    """批量更新摄像机目录"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        camera_ids = data.get('camera_ids', [])
        directory_id = data.get('directory_id')
        
        if not camera_ids or len(camera_ids) == 0:
            return jsonify({'error': '摄像机ID列表不能为空'}), 400
        
        manager = get_camera_manager()
        
        # 批量更新摄像机目录
        success_count = 0
        for camera_id in camera_ids:
            try:
                manager.update_camera(camera_id, {'directory_id': directory_id})
                success_count += 1
            except Exception as e:
                logger.warning(f"更新摄像机 {camera_id} 目录失败: {e}")
        
        logger.info(f"批量更新摄像机目录成功: {success_count}/{len(camera_ids)}")
        
        return jsonify({
            'success': True,
            'message': f'成功移动 {success_count} 个摄像机',
            'success_count': success_count,
            'total_count': len(camera_ids)
        })
        
    except Exception as e:
        logger.error(f"批量更新摄像机目录失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ========================================
# 目录管理API
# ========================================

@camera_bp.route('/directories', methods=['GET'])
def get_directories():
    """获取摄像机目录列表"""
    try:
        manager = get_camera_manager()
        directories = manager.get_all_directories()
        return jsonify([d.to_dict() for d in directories])
    except Exception as e:
        logger.error(f"获取目录列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@camera_bp.route('/directories', methods=['POST'])
def create_directory():
    """创建摄像机目录"""
    try:
        data = request.get_json()
        
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'error': '目录名称不能为空'}), 400
        
        manager = get_camera_manager()
        
        directory = CameraDirectory(
            name=name,
            parent_id=data.get('parent_id'),
            description=data.get('description', ''),
            sort_order=data.get('sort_order', 0)
        )
        
        directory_id = manager.add_directory(directory)
        
        return jsonify({'id': directory_id, 'message': '创建成功'}), 201
        
    except Exception as e:
        logger.error(f"创建目录失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# Blueprint注册
# ========================================

def register_camera_blueprint(app, socketio=None):
    """注册摄像机管理Blueprint"""
    global _socketio
    _socketio = socketio
    
    app.register_blueprint(camera_bp)
    logger.info("摄像机管理Blueprint已注册")
    
    # 初始化摄像机管理器
    manager = get_camera_manager()
    logger.info(f"摄像机管理器已初始化，摄像机数量: {len(manager.get_all_cameras())}")
    
    return camera_bp