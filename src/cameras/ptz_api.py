"""
云台控制API

提供摄像机云台控制的REST API接口。
"""

import json
import logging
from flask import Blueprint, request, jsonify

from src.cameras.camera_manager import get_camera_manager, PatrolPath
from src.cameras.onvif_client import OnvifCameraClient, ONVIF_AVAILABLE
from src.cameras.ptz_service import get_ptz_service_manager

logger = logging.getLogger(__name__)

# 创建Blueprint
ptz_bp = Blueprint('ptz', __name__, url_prefix='/api/ptz')


def get_ptz_controller_for_camera(camera_id: str):
    """
    获取摄像机的PTZ控制器
    
    Args:
        camera_id: 摄像机ID
        
    Returns:
        PTZ控制器对象，如果不支持PTZ则返回None
    """
    if not ONVIF_AVAILABLE:
        logger.warning("ONVIF库未安装，无法使用PTZ功能")
        return None
    
    # 获取摄像机信息
    manager = get_camera_manager()
    camera = manager.get_camera(camera_id)
    
    if not camera:
        logger.error(f"摄像机不存在: {camera_id}")
        return None
    
    # 检查摄像机是否有PTZ能力
    if not camera.support_move and not camera.support_zoom:
        logger.warning(f"摄像机 {camera.name} 不支持PTZ功能")
        return None
    
    # 创建ONVIF客户端并获取PTZ控制器
    try:
        client = OnvifCameraClient(
            ip=camera.ip,
            port=camera.port,
            username=camera.username,
            password=camera.password
        )
        
        # 获取PTZ控制器
        if hasattr(client, '_ptz_controller') and client._ptz_controller:
            return client._ptz_controller
        else:
            logger.warning(f"摄像机 {camera.name} PTZ控制器未初始化")
            return None
            
    except Exception as e:
        logger.error(f"连接摄像机 {camera.name} 失败: {e}")
        return None


# ========================================
# 云台方向控制API
# ========================================

@ptz_bp.route('/control', methods=['POST'])
def ptz_control():
    """
    云台方向控制
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "direction": "方向 (up/down/left/right/left_up/right_up/left_down/right_down)",
            "speed": "云台速度 (1-5)",
            "action": "动作 (start/stop)"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        direction = data.get('direction')
        speed = data.get('speed', 3)
        action = data.get('action', 'start')
        
        if not camera_id:
            return jsonify({'error': '缺少摄像机ID'}), 400
        
        logger.info(f"云台控制请求: camera_id={camera_id}, direction={direction}, speed={speed}, action={action}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # 方向映射到移动向量
        direction_map = {
            'up': [0, speed * 0.2, 0],      # 向上移动
            'down': [0, -speed * 0.2, 0],   # 向下移动
            'left': [-speed * 0.2, 0, 0],   # 向左移动
            'right': [speed * 0.2, 0, 0],   # 向右移动
            'left_up': [-speed * 0.2, speed * 0.2, 0],   # 左上移动
            'right_up': [speed * 0.2, speed * 0.2, 0],   # 右上移动
            'left_down': [-speed * 0.2, -speed * 0.2, 0], # 左下移动
            'right_down': [speed * 0.2, -speed * 0.2, 0]  # 右下移动
        }
        
        if action == 'start':
            # 开始移动
            if direction and direction in direction_map:
                translation = direction_map[direction]
                ptz_controller.move(translation)
                return jsonify({
                    'success': True,
                    'message': f'云台开始移动: {direction}'
                })
            else:
                return jsonify({'error': '无效的方向参数'}), 400
        
        elif action == 'stop':
            # 停止移动
            ptz_controller.stop()
            return jsonify({
                'success': True,
                'message': '云台已停止'
            })
        
        else:
            return jsonify({'error': '无效的动作参数'}), 400
        
    except Exception as e:
        logger.error(f"云台控制失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/zoom', methods=['POST'])
def ptz_zoom():
    """
    云台变焦控制
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "action": "动作 (in/out)",
            "speed": "变焦速度 (0.1-1.0)"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        action = data.get('action')
        speed = data.get('speed', 0.3)
        
        if not camera_id or not action:
            return jsonify({'error': '缺少必要参数'}), 400
        
        logger.info(f"云台变焦请求: camera_id={camera_id}, action={action}, speed={speed}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # 检查是否支持变焦
        capabilities = ptz_controller.get_capabilities()
        if not capabilities.zoom:
            return jsonify({'error': '该摄像机不支持变焦功能'}), 400
        
        # 变焦控制
        zoom_value = speed if action == 'in' else -speed
        translation = [0, 0, zoom_value]
        ptz_controller.move(translation)
        
        return jsonify({
            'success': True,
            'message': f'变焦控制指令已发送: {action}'
        })
        
    except Exception as e:
        logger.error(f"云台变焦失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/focus', methods=['POST'])
def ptz_focus():
    """
    云台聚焦控制
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "action": "动作 (in/out)"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        action = data.get('action')
        
        if not camera_id or not action:
            return jsonify({'error': '缺少必要参数'}), 400
        
        logger.info(f"云台聚焦请求: camera_id={camera_id}, action={action}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # ONVIF标准中，聚焦通常不属于PTZ服务，属于成像服务
        # 这里暂时返回不支持
        return jsonify({
            'success': False,
            'message': '聚焦功能暂不支持，请使用摄像机自带聚焦功能'
        })
        
    except Exception as e:
        logger.error(f"云台聚焦失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/iris', methods=['POST'])
def ptz_iris():
    """
    云台光圈控制
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "action": "动作 (in/out)"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        action = data.get('action')
        
        if not camera_id or not action:
            return jsonify({'error': '缺少必要参数'}), 400
        
        logger.info(f"云台光圈请求: camera_id={camera_id}, action={action}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # ONVIF标准中，光圈通常不属于PTZ服务，属于成像服务
        # 这里暂时返回不支持
        return jsonify({
            'success': False,
            'message': '光圈功能暂不支持，请使用摄像机自带光圈调节功能'
        })
        
    except Exception as e:
        logger.error(f"云台光圈失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# 预置点管理API
# ========================================

@ptz_bp.route('/preset/goto', methods=['POST'])
def goto_preset():
    """
    调用预置点
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "preset_id": "预置点ID (token)"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        preset_id = data.get('preset_id')
        
        if not camera_id or not preset_id:
            return jsonify({'error': '缺少必要参数'}), 400
        
        logger.info(f"调用预置点: camera_id={camera_id}, preset_id={preset_id}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # 检查是否支持预置位
        capabilities = ptz_controller.get_capabilities()
        if not capabilities.preset_support:
            return jsonify({'error': '该摄像机不支持预置位功能'}), 400
        
        # 调用预置位
        ptz_controller.goto_preset(preset_id)
        
        return jsonify({
            'success': True,
            'message': f'预置点 {preset_id} 已调用'
        })
        
    except Exception as e:
        logger.error(f"调用预置点失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/preset/set', methods=['POST'])
def set_preset():
    """
    设置预置点 - 将当前位置保存为预置点
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "preset_id": "预置点ID (token，可选)",
            "name": "预置点名称"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        preset_id = data.get('preset_id')
        name = data.get('name', f'预置点 {preset_id}')
        
        if not camera_id:
            return jsonify({'error': '缺少摄像机ID'}), 400
        
        logger.info(f"设置预置点: camera_id={camera_id}, preset_id={preset_id}, name={name}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # 检查是否支持预置位
        capabilities = ptz_controller.get_capabilities()
        if not capabilities.preset_support:
            return jsonify({'error': '该摄像机不支持预置位功能'}), 400
        
        # 设置预置位
        if preset_id:
            # 更新现有预置位（先删除旧的，再创建新的）
            try:
                ptz_controller.remove_preset(preset_id)
            except Exception:
                pass
        
        # 创建新预置位
        new_preset_token = ptz_controller.save_preset(name)
        
        return jsonify({
            'success': True,
            'preset_id': new_preset_token,
            'name': name,
            'message': f'预置点 {new_preset_token} 已设置'
        })
        
    except Exception as e:
        logger.error(f"设置预置点失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/preset/add', methods=['POST'])
def add_preset():
    """
    添加新预置点 - 将当前位置保存为新预置点
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "name": "预置点名称"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        name = data.get('name')
        
        if not camera_id or not name:
            return jsonify({'error': '缺少必要参数'}), 400
        
        logger.info(f"添加预置点: camera_id={camera_id}, name={name}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # 检查是否支持预置位
        capabilities = ptz_controller.get_capabilities()
        if not capabilities.preset_support:
            return jsonify({'error': '该摄像机不支持预置位功能'}), 400
        
        # 设置新预置位（让摄像机自动分配token）
        new_preset_token = ptz_controller.save_preset(name)
        
        return jsonify({
            'success': True,
            'preset_id': new_preset_token,
            'name': name,
            'message': f'预置点已添加: {name}'
        })
        
    except Exception as e:
        logger.error(f"添加预置点失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/preset/delete', methods=['POST'])
def delete_preset():
    """
    删除预置点
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "preset_id": "预置点ID (token)"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        preset_id = data.get('preset_id')
        
        if not camera_id or not preset_id:
            return jsonify({'error': '缺少必要参数'}), 400
        
        logger.info(f"删除预置点: camera_id={camera_id}, preset_id={preset_id}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # 检查是否支持预置位
        capabilities = ptz_controller.get_capabilities()
        if not capabilities.preset_support:
            return jsonify({'error': '该摄像机不支持预置位功能'}), 400
        
        # 删除预置位
        ptz_controller.remove_preset(preset_id)
        
        return jsonify({
            'success': True,
            'message': f'预置点 {preset_id} 已删除'
        })
        
    except Exception as e:
        logger.error(f"删除预置点失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/preset/list', methods=['GET'])
def get_presets():
    """
    获取预置点列表
    
    Query Parameters:
        camera_id: 摄像机ID
    """
    try:
        camera_id = request.args.get('camera_id')
        
        if not camera_id:
            return jsonify({'error': '缺少摄像机ID'}), 400
        
        logger.info(f"获取预置点列表: camera_id={camera_id}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            # 不支持PTZ控制时返回空列表，而不是错误
            return jsonify({
                'success': True,
                'presets': [],
                'message': '该摄像机不支持PTZ控制'
            })
        
        # 检查是否支持预置位
        capabilities = ptz_controller.get_capabilities()
        if not capabilities.preset_support:
            # 不支持预置位时返回空列表和提示信息
            return jsonify({
                'success': True,
                'presets': [],
                'message': '该摄像机不支持预置位功能'
            })
        
        # 获取预置位列表
        presets = ptz_controller.get_presets()
        
        # 转换为前端需要的格式
        preset_list = []
        for preset in presets:
            preset_list.append({
                'id': preset.token,
                'name': preset.name,
                'position': preset.position
            })
        
        return jsonify({
            'success': True,
            'presets': preset_list
        })
        
    except Exception as e:
        logger.error(f"获取预置点列表失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# 巡航路径管理API
# ========================================

# 巡航运行状态（内存中保存，重启后清空）
_patrol_running = {}


@ptz_bp.route('/patrol/start', methods=['POST'])
def start_patrol():
    """
    开始巡航
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "patrol_id": "巡航路径ID"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        patrol_id = data.get('patrol_id')
        
        if not camera_id or not patrol_id:
            return jsonify({'error': '缺少必要参数'}), 400
        
        logger.info(f"开始巡航: camera_id={camera_id}, patrol_id={patrol_id}")
        
        # 获取PTZ控制器
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if not ptz_controller:
            return jsonify({'error': '该摄像机不支持PTZ控制'}), 400
        
        # 检查是否支持预置位
        capabilities = ptz_controller.get_capabilities()
        if not capabilities.preset_support:
            return jsonify({'error': '该摄像机不支持预置位功能'}), 400
        
        # 从数据库获取巡航路径配置
        manager = get_camera_manager()
        patrol = manager.get_patrol_path(int(patrol_id))
        
        if not patrol:
            return jsonify({'error': '巡航路径不存在'}), 404
        
        patrol_config = patrol.to_dict()
        
        # 标记巡航正在运行
        patrol_key = f"{camera_id}:{patrol_id}"
        _patrol_running[patrol_key] = True
        
        # TODO: 实现巡航执行逻辑
        # 这里应该启动一个后台任务，依次调用预置点
        # 可以使用线程或异步任务
        
        logger.info(f"巡航路径 {patrol_id} 已启动，包含 {len(patrol_config['presets'])} 个预置点")
        
        return jsonify({
            'success': True,
            'message': f'巡航路径 {patrol_id} 已启动'
        })
        
    except Exception as e:
        logger.error(f"开始巡航失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/patrol/stop', methods=['POST'])
def stop_patrol():
    """
    停止巡航
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "patrol_id": "巡航路径ID"
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        patrol_id = data.get('patrol_id')
        
        if not camera_id or not patrol_id:
            return jsonify({'error': '缺少必要参数'}), 400
        
        logger.info(f"停止巡航: camera_id={camera_id}, patrol_id={patrol_id}")
        
        # 获取巡航路径标识
        patrol_key = f"{camera_id}:{patrol_id}"
        
        # 标记巡航停止
        if patrol_key in _patrol_running:
            _patrol_running[patrol_key] = False
        
        # 停止云台移动
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if ptz_controller:
            ptz_controller.stop()
        
        return jsonify({
            'success': True,
            'message': f'巡航路径 {patrol_id} 已停止'
        })
        
    except Exception as e:
        logger.error(f"停止巡航失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/patrol/add', methods=['POST'])
def add_patrol():
    """
    添加巡航路径
    
    Request Body:
        {
            "camera_id": "摄像机ID",
            "name": "巡航路径名称",
            "presets": [预置点ID列表],
            "durations": [停留时间列表（秒）]
        }
    """
    try:
        data = request.get_json()
        
        camera_id = data.get('camera_id')
        name = data.get('name')
        presets = data.get('presets', [])
        durations = data.get('durations', [])
        
        if not camera_id or not name:
            return jsonify({'error': '缺少必要参数'}), 400
        
        # 检查摄像机是否支持预置位功能
        ptz_controller = get_ptz_controller_for_camera(camera_id)
        if ptz_controller:
            capabilities = ptz_controller.get_capabilities()
            if not capabilities.preset_support:
                return jsonify({'error': '该摄像机不支持预置位功能，无法创建巡航路径'}), 400
        
        # 允许创建空的巡航路径（后续可通过编辑添加预置点）
        # if len(presets) == 0:
        #     return jsonify({'error': '巡航路径至少需要一个预置点'}), 400
        
        logger.info(f"添加巡航路径: camera_id={camera_id}, name={name}, presets={presets}")
        
        # 创建PatrolPath对象
        patrol = PatrolPath(
            camera_id=camera_id,
            name=name,
            presets=json.dumps(presets),  # JSON格式存储
            durations=json.dumps(durations),  # JSON格式存储
            description='',
            enabled=True
        )
        
        # 保存到数据库
        manager = get_camera_manager()
        patrol_id = manager.add_patrol_path(patrol)
        
        return jsonify({
            'success': True,
            'patrol_id': patrol_id,
            'name': name,
            'points': len(presets),
            'message': f'巡航路径已添加: {name}'
        })
        
    except Exception as e:
        logger.error(f"添加巡航路径失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/patrol/edit', methods=['POST'])
def edit_patrol():
    """
    编辑巡航路径
    
    Request Body:
        {
            "patrol_id": "巡航路径ID",
            "name": "巡航路径名称",
            "presets": [预置点ID列表],
            "durations": [停留时间列表（秒）]
        }
    """
    try:
        data = request.get_json()
        
        patrol_id = data.get('patrol_id')
        name = data.get('name')
        presets = data.get('presets')
        durations = data.get('durations')
        
        if not patrol_id:
            return jsonify({'error': '缺少巡航路径ID'}), 400
        
        logger.info(f"编辑巡航路径: patrol_id={patrol_id}")
        
        # 更新巡航路径配置
        manager = get_camera_manager()
        
        updates = {}
        if name:
            updates['name'] = name
        if presets:
            updates['presets'] = json.dumps(presets)
        if durations:
            updates['durations'] = json.dumps(durations)
        
        if updates:
            success = manager.update_patrol_path(int(patrol_id), updates)
            
            if success:
                return jsonify({
                    'success': True,
                    'message': f'巡航路径 {patrol_id} 已更新'
                })
            else:
                return jsonify({'error': '更新失败'}), 500
        else:
            return jsonify({'error': '没有需要更新的内容'}), 400
        
    except Exception as e:
        logger.error(f"编辑巡航路径失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/patrol/delete', methods=['POST'])
def delete_patrol():
    """
    删除巡航路径
    
    Request Body:
        {
            "patrol_id": "巡航路径ID"
        }
    """
    try:
        data = request.get_json()
        
        patrol_id = data.get('patrol_id')
        
        if not patrol_id:
            return jsonify({'error': '缺少巡航路径ID'}), 400
        
        logger.info(f"删除巡航路径: patrol_id={patrol_id}")
        
        # 从数据库删除巡航路径
        manager = get_camera_manager()
        success = manager.delete_patrol_path(int(patrol_id))
        
        if success:
            return jsonify({
                'success': True,
                'message': f'巡航路径 {patrol_id} 已删除'
            })
        else:
            return jsonify({'error': '删除失败'}), 500
        
    except Exception as e:
        logger.error(f"删除巡航路径失败: {e}")
        return jsonify({'error': str(e)}), 500


@ptz_bp.route('/patrol/list', methods=['GET'])
def get_patrols():
    """
    获取巡航路径列表
    
    Query Parameters:
        camera_id: 摄像机ID
    """
    try:
        camera_id = request.args.get('camera_id')
        
        if not camera_id:
            return jsonify({'error': '缺少摄像机ID'}), 400
        
        logger.info(f"获取巡航路径列表: camera_id={camera_id}")
        
        # 从数据库获取巡航路径列表
        manager = get_camera_manager()
        patrols = manager.get_patrol_paths(camera_id)
        
        # 转换为前端需要的格式
        patrol_list = []
        for patrol in patrols:
            patrol_dict = patrol.to_dict()
            patrol_list.append({
                'id': patrol_dict['id'],
                'name': patrol_dict['name'],
                'points': len(patrol_dict['presets']),
                'presets': patrol_dict['presets'],
                'durations': patrol_dict['durations'],
                'running': False  # 运行状态需要从内存中获取
            })
        
        return jsonify({
            'success': True,
            'patrols': patrol_list
        })
        
    except Exception as e:
        logger.error(f"获取巡航路径列表失败: {e}")
        return jsonify({'error': str(e)}), 500


def register_ptz_api(app):
    """
    注册云台控制API到Flask应用
    
    Args:
        app: Flask应用实例
    """
    app.register_blueprint(ptz_bp)
    logger.info("云台控制API已注册")