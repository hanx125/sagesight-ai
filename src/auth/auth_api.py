"""
认证API模块
提供登录、登出、会话检查接口
"""

import logging
from functools import wraps
from flask import Blueprint, request, jsonify, session, redirect, url_for
from src.auth.auth_manager import get_auth_manager

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # 如果是API请求返回JSON，否则重定向到登录页
            if request.path.startswith('/api/'):
                return jsonify({'error': '未登录', 'code': 'UNAUTHORIZED'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function


def get_current_user():
    """获取当前登录用户信息"""
    if 'user_id' not in session:
        return None
    manager = get_auth_manager()
    user = manager.get_user(session['user_id'])
    if user and user.enabled == 1:
        return user
    session.clear()
    return None


@auth_bp.route('/login', methods=['POST'])
def login():
    """用户登录"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400
    
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400
    
    manager = get_auth_manager()
    user = manager.authenticate(username, password)
    
    if not user:
        logger.warning(f"登录失败: username={username}")
        return jsonify({'error': '用户名或密码错误'}), 401
    
    # 保存会话
    session['user_id'] = user.id
    session['username'] = user.username
    session['role'] = user.role
    session.permanent = True
    
    logger.info(f"用户登录成功: {user.username} (role={user.role})")
    
    return jsonify({
        'message': '登录成功',
        'user': user.to_dict()
    }), 200


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """用户登出"""
    username = session.get('username', 'unknown')
    session.clear()
    logger.info(f"用户登出: {username}")
    return jsonify({'message': '登出成功'}), 200


@auth_bp.route('/check', methods=['GET'])
def check_session():
    """检查会话状态"""
    user = get_current_user()
    if user:
        return jsonify({
            'logged_in': True,
            'user': user.to_dict()
        }), 200
    return jsonify({
        'logged_in': False
    }), 200


@auth_bp.route('/users', methods=['GET'])
def list_users():
    """获取用户列表"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'error': '权限不足'}), 403
    
    manager = get_auth_manager()
    users = manager.get_all_users()
    return jsonify({
        'users': [u.to_dict() for u in users]
    }), 200


@auth_bp.route('/users', methods=['POST'])
def create_user():
    """创建用户"""
    current = get_current_user()
    if not current or current.role != 'admin':
        return jsonify({'error': '权限不足'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400
    
    manager = get_auth_manager()
    try:
        user = manager.create_user(data)
        logger.info(f"创建用户: {user.username}")
        return jsonify({
            'message': '创建成功',
            'user': user.to_dict()
        }), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@auth_bp.route('/users/<int:user_id>', methods=['PUT'])
def update_user(user_id: int):
    """更新用户"""
    current = get_current_user()
    if not current or (current.role != 'admin' and current.id != user_id):
        return jsonify({'error': '权限不足'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400
    
    manager = get_auth_manager()
    try:
        user = manager.update_user(user_id, data)
        if user:
            return jsonify({
                'message': '更新成功',
                'user': user.to_dict()
            }), 200
        return jsonify({'error': '用户不存在'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@auth_bp.route('/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id: int):
    """删除用户"""
    current = get_current_user()
    if not current or current.role != 'admin':
        return jsonify({'error': '权限不足'}), 403
    
    manager = get_auth_manager()
    try:
        if manager.delete_user(user_id):
            return jsonify({'message': '删除成功'}), 200
        return jsonify({'error': '用户不存在'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


def register_auth_blueprint(app):
    """注册认证蓝图"""
    app.register_blueprint(auth_bp)
    logger.info("认证蓝图已注册")
