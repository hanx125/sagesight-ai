"""
推送管理 API

提供移动端设备绑定与推送配置接口：
    POST /api/push/bind      绑定当前用户与设备 ClientId（App 登录后调用）
    POST /api/push/unbind    解绑设备
    GET  /api/push/status    推送开关状态（供 App「我的」页展示）
    GET  /api/push/config    读取推送配置（管理员）
    PUT  /api/push/config    更新推送配置（管理员）
"""

import logging

from flask import Blueprint, request, jsonify, session

logger = logging.getLogger(__name__)

push_bp = Blueprint('push', __name__, url_prefix='/api/push')

# 可写配置键（与 system_settings 表对应）
_CONFIG_KEYS = ('push_enabled', 'getui_app_id', 'getui_app_key',
                'getui_app_master_secret', 'push_min_confidence', 'push_class_filter')


def _get_request_user():
    """获取当前请求用户：优先 Bearer Token，其次 Session"""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        from src.auth.token_manager import get_token_manager
        info = get_token_manager().validate_token(auth_header[7:])
        if info:
            return info

    if 'user_id' in session:
        return {'user_id': session['user_id'], 'username': session.get('username', ''),
                'role': session.get('role', '')}
    return None


@push_bp.route('/bind', methods=['POST'])
def bind_client():
    """绑定当前用户与设备推送 ClientId"""
    user = _get_request_user()
    if not user:
        return jsonify({'error': '未登录', 'code': 'UNAUTHORIZED'}), 401

    data = request.get_json() or {}
    client_id = (data.get('client_id') or '').strip()
    if not client_id:
        return jsonify({'error': '缺少 client_id'}), 400

    device_type = (data.get('device_type') or 'android').strip()
    from src.push.push_service import get_push_service
    if get_push_service().bind_client(user['user_id'], client_id, device_type):
        return jsonify({'message': '绑定成功'}), 200
    return jsonify({'error': '绑定失败'}), 500


@push_bp.route('/unbind', methods=['POST'])
def unbind_client():
    """解绑设备"""
    user = _get_request_user()
    if not user:
        return jsonify({'error': '未登录', 'code': 'UNAUTHORIZED'}), 401

    data = request.get_json() or {}
    client_id = (data.get('client_id') or '').strip()
    if not client_id:
        return jsonify({'error': '缺少 client_id'}), 400

    from src.push.push_service import get_push_service
    if get_push_service().unbind_client(client_id):
        return jsonify({'message': '解绑成功'}), 200
    return jsonify({'error': '解绑失败'}), 500


@push_bp.route('/bind/status', methods=['GET'])
def bind_status():
    """查询当前设备是否已绑定（App 端展示用）"""
    from src.push.push_service import get_push_service
    client_id = (request.args.get('client_id') or '').strip()
    bound = get_push_service().is_client_bound(client_id) if client_id else False
    return jsonify({'bound': bound})


@push_bp.route('/status', methods=['GET'])
def push_status():
    """推送开关状态"""
    from src.push.push_service import get_push_service
    service = get_push_service()
    return jsonify({
        'enabled': service.is_enabled(),
        'configured': service._get_credentials() is not None
    })


@push_bp.route('/config', methods=['GET'])
def get_push_config():
    """读取推送配置（管理员），MasterSecret 脱敏返回"""
    user = _get_request_user()
    if not user or user.get('role') != 'admin':
        return jsonify({'error': '权限不足'}), 403

    from src.system.system_manager import get_system_manager
    manager = get_system_manager()
    config = {key: manager.get_setting(key, '') for key in _CONFIG_KEYS}
    if config.get('getui_app_master_secret'):
        config['getui_app_master_secret'] = '******'
    return jsonify({'config': config})


@push_bp.route('/config', methods=['PUT'])
def update_push_config():
    """更新推送配置（管理员），MasterSecret 传 '******' 表示不修改"""
    user = _get_request_user()
    if not user or user.get('role') != 'admin':
        return jsonify({'error': '权限不足'}), 403

    data = request.get_json() or {}
    config_in = data.get('config') or {}

    from src.system.system_manager import get_system_manager
    manager = get_system_manager()
    for key in _CONFIG_KEYS:
        if key not in config_in:
            continue
        value = str(config_in.get(key) or '').strip()
        if key == 'getui_app_master_secret' and value == '******':
            continue  # 脱敏占位值不覆盖真实密钥
        manager.set_setting(key, value)

    return jsonify({'message': '保存成功'})


def register_push_blueprint(app):
    """注册推送蓝图"""
    app.register_blueprint(push_bp)
    logger.info("推送蓝图已注册")
