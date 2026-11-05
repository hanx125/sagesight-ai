"""
认证API模块
提供登录、登出、会话检查接口
"""

import logging
import secrets
import time
import urllib.parse
from functools import wraps
from flask import Blueprint, request, jsonify, session, redirect, render_template, url_for
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
    runtime_mode = (data.get('runtime_mode') or '').strip()

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    # 运行模式随登录提交（登录页选择）；非法值直接拒绝，避免写入未知模式
    if runtime_mode:
        from src.system.runtime_mode import VALID_MODES
        if runtime_mode not in VALID_MODES:
            return jsonify({'error': '运行模式不合法'}), 400

    manager = get_auth_manager()
    user = manager.authenticate(username, password)
    
    if not user:
        logger.warning(f"登录失败: username={username}")
        return jsonify({'error': '用户名或密码错误'}), 401

    # 认证通过后才允许持久化运行模式（部署级全局配置）
    if runtime_mode:
        from src.system.runtime_mode import set_runtime_mode
        if not set_runtime_mode(runtime_mode):
            return jsonify({'error': '运行模式保存失败，请检查系统配置库'}), 500
        logger.info(f"运行模式已切换为: {runtime_mode}（操作人: {user.username}）")

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


@auth_bp.route('/app_login', methods=['POST'])
def app_login():
    """移动端登录：返回 Bearer Token（App 端无 Cookie 会话场景）"""
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
        logger.warning(f"移动端登录失败: username={username}")
        return jsonify({'error': '用户名或密码错误'}), 401
    if user.enabled != 1:
        return jsonify({'error': '账号已被禁用'}), 403

    from src.auth.token_manager import get_token_manager
    token = get_token_manager().issue_token(user.id, user.username, user.role)

    logger.info(f"移动端登录成功: {user.username} (role={user.role})")
    return jsonify({
        'message': '登录成功',
        'token': token,
        'user': user.to_dict()
    }), 200


@auth_bp.route('/app_logout', methods=['POST'])
def app_logout():
    """移动端登出：吊销当前 Token"""
    from src.auth.token_manager import get_token_manager
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        get_token_manager().revoke_token(auth_header[7:])
    return jsonify({'message': '登出成功'}), 200


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


# ========================================
# 云平台单点登录（SSO，OAuth2 授权码流）
# ========================================

# 云平台 OAuth2 固定端点（按云平台部署形态固定，无需在界面配置）
# 注意：授权确认页 /sso 是云前端的路由（默认 80 端口），与云 API 网关（48080）不同源，
# 授权页入口地址可配置（sso_authorize_base_url），缺省自动取 API 地址的主机部分（80 端口）。
_SSO_FIXED = {
    'authorize_url': '/sso',                              # 云授权确认页路径（云前端路由）
    'token_url': '/admin-api/system/oauth2/token',        # 云 token 端点（经网关带 /admin-api 前缀）
    'user_info_url': '/admin-api/system/oauth2/user/get',  # 云用户信息端点
}

# SSO 动态配置键（与 system_settings 表对应）
_SSO_KEYS = {
    'cloud_base_url': 'sso_cloud_base_url',
    'authorize_base_url': 'sso_authorize_base_url',
    'client_id': 'sso_client_id',
    'client_secret': 'sso_client_secret',
    'redirect_uri': 'sso_redirect_uri',
    'scope': 'sso_scope',
}


# SSO 授权上下文服务端兜底缓存：{state: {'code_verifier': .., 'exp': ..}}
# 标准流程 state 存放在浏览器会话 Cookie 中、回调时带回校验；但 Cookie 按主机名
# 隔离，浏览器访问登录页的主机名与 redirect_uri 不一致（如 localhost 与
# 192.168.x.x 混用）时，回调请求不带会话 Cookie，state 校验必失败。
# 此缓存作为兜底：state 仍由本服务随机签发、一次性消费、10 分钟有效，
# 伪造与重放依旧不可行，仅放宽"发起方=会话方"的绑定。
_SSO_PENDING = {}
_SSO_PENDING_TTL = 600


def _sso_pending_put(state: str, code_verifier):
    """登记一次待回调的授权上下文，并顺带清理过期项。"""
    now = time.time()
    for k in [k for k, v in _SSO_PENDING.items() if v['exp'] < now]:
        _SSO_PENDING.pop(k, None)
    _SSO_PENDING[state] = {'code_verifier': code_verifier, 'exp': now + _SSO_PENDING_TTL}


def _sso_pending_pop(state: str):
    """一次性取出授权上下文（不存在或已过期返回 None）。"""
    item = _SSO_PENDING.pop(state, None)
    if item and item['exp'] >= time.time():
        return item
    return None


def _is_sso_enabled() -> bool:
    from src.system.system_manager import get_system_manager
    return get_system_manager().get_setting('sso_enabled', '0') in ('1', 'true', 'True')


def _default_authorize_base(cloud_base_url: str) -> str:
    """授权页入口缺省值：取 API 地址的协议+主机（去掉端口即 80，云前端）。"""
    parsed = urllib.parse.urlsplit(cloud_base_url)
    if not parsed.scheme or not parsed.hostname:
        return cloud_base_url
    return f'{parsed.scheme}://{parsed.hostname}'


def _load_sso_config() -> dict:
    from src.system.system_manager import get_system_manager
    manager = get_system_manager()
    cfg = dict(_SSO_FIXED)
    cfg.update({name: (manager.get_setting(key, '') or '').strip() for name, key in _SSO_KEYS.items()})
    # 授权页入口未配置时，取 API 地址主机部分（80 端口云前端）
    if not cfg.get('authorize_base_url'):
        cfg['authorize_base_url'] = _default_authorize_base(cfg.get('cloud_base_url', ''))
    cfg['use_pkce'] = manager.get_setting('sso_use_pkce', '1') in ('1', 'true', 'True')
    return cfg


@auth_bp.route('/sso/status', methods=['GET'])
def sso_status():
    """SSO 状态接口（公开）：登录页据此显示/隐藏单点登录入口"""
    from src.auth.sso_client import cloud_scope_ready
    return jsonify({
        'enabled': _is_sso_enabled(),
        'configured': cloud_scope_ready(_load_sso_config()),
    })


@auth_bp.route('/sso/config', methods=['GET'])
def get_sso_config():
    """获取云平台 SSO 配置（仅管理员）"""
    current = get_current_user()
    if not current or current.role != 'admin':
        return jsonify({'error': '权限不足'}), 403
    cfg = _load_sso_config()
    cfg['enabled'] = _is_sso_enabled()
    return jsonify(cfg)


@auth_bp.route('/sso/config', methods=['PUT'])
def update_sso_config():
    """保存云平台 SSO 配置（仅管理员）"""
    current = get_current_user()
    if not current or current.role != 'admin':
        return jsonify({'error': '权限不足'}), 403

    data = request.get_json() or {}
    from src.system.system_manager import get_system_manager
    manager = get_system_manager()

    if 'enabled' in data:
        manager.set_setting('sso_enabled', '1' if bool(data['enabled']) else '0')
    if 'use_pkce' in data:
        manager.set_setting('sso_use_pkce', '1' if bool(data['use_pkce']) else '0')
    for name, key in _SSO_KEYS.items():
        if name in data:
            manager.set_setting(key, str(data[name] or '').strip())

    logger.info("云平台单点登录配置已更新")
    return jsonify({'message': '云平台配置已保存'})


@auth_bp.route('/sso/login', methods=['GET'])
def sso_login():
    """发起单点登录：返回云平台授权页跳转地址"""
    if not _is_sso_enabled():
        return jsonify({'error': '单点登录未启用'}), 400

    cfg = _load_sso_config()
    from src.auth.sso_client import build_authorize_url, cloud_scope_ready, generate_code_verifier, generate_state
    if not cloud_scope_ready(cfg):
        return jsonify({'error': 'SSO 云侧配置未就绪（云平台地址/客户端ID/回调地址）'}), 400

    state = generate_state()
    code_verifier = generate_code_verifier() if cfg.get('use_pkce', True) else None
    # 授权上下文优先存入 Flask 会话（同域回调自动携带），回调时校验防 CSRF；
    # 同时登记服务端一次性兜底缓存（浏览器访问主机与回调地址不同源时仍可完成）。
    session['sso_state'] = state
    session['sso_code_verifier'] = code_verifier
    _sso_pending_put(state, code_verifier)
    logger.info(f"SSO 发起登录: state={state[:8]}..., 浏览器Host={request.host}, redirect_uri={cfg.get('redirect_uri')}")

    return jsonify({'url': build_authorize_url(cfg, state, code_verifier)})


@auth_bp.route('/sso/callback', methods=['GET'])
def sso_callback():
    """单点登录回调（由云平台跳转触发）：换 token → 取用户身份 → 建立本地会话

    登录页以 popup 弹窗发起授权，本回调最终渲染 sso_callback 结果页：
    有 opener 时 postMessage 回传结果并自动关窗；无 opener（弹窗被拦截回退
    整页跳转）时直接跳转首页或登录页。
    """
    cfg = _load_sso_config()

    def _err(msg):
        return render_template('sso_callback.html', result_type='error', message=msg)

    if not _is_sso_enabled():
        return _err('单点登录未启用')

    error = request.args.get('error')
    if error:
        return _err(f'云平台授权失败: {error}')

    code = request.args.get('code')
    state = request.args.get('state')
    if not code or not state:
        return _err('回调缺少 code/state 参数')

    stored_state = session.pop('sso_state', None)
    code_verifier = session.pop('sso_code_verifier', None)
    if stored_state and state == stored_state:
        # 会话校验通过：消费服务端兜底记录，防同一 state 重放
        _sso_pending_pop(state)
    else:
        # 会话 Cookie 未随回调带回（访问主机与回调地址不同源、浏览器禁 Cookie、
        # 重复回调/并发发起等）：改用服务端一次性签发的 state 兜底校验。
        pending = _sso_pending_pop(state)
        if pending is None:
            if not stored_state:
                logger.warning(
                    f"SSO 回调未携带登录会话且无有效 state 记录: "
                    f"浏览器Host={request.host}, redirect_uri={cfg.get('redirect_uri')}"
                )
                return _err('登录会话未随回调带回（请用与回调地址相同的主机名访问本系统，'
                            '且浏览器未禁用 Cookie），请重新发起登录')
            logger.warning(
                f"SSO state 不一致（重复回调或并发发起多次登录）: "
                f"回调state={state[:8]}..., 会话state={stored_state[:8]}..."
            )
            return _err('state 校验失败（回调已被消费或并发发起多次登录），请重新发起登录')
        # 兜底命中：说明回调请求未携带会话 Cookie（常见于 localhost 与 IP 混用访问）
        logger.warning(
            f"SSO 回调未携带会话 Cookie，已用服务端一次性 state 兜底放行: "
            f"浏览器Host={request.host}, redirect_uri={cfg.get('redirect_uri')}"
        )
        code_verifier = pending['code_verifier']

    from src.auth.sso_client import exchange_code, fetch_user_identity
    try:
        token = exchange_code(cfg, code, state, code_verifier)
    except Exception as e:
        logger.error(f'SSO 换取令牌失败: {e}', exc_info=True)
        return _err(f'换取令牌失败: {e}')

    try:
        identity = fetch_user_identity(cfg, token['access_token'])
        username = identity.get('username')
        nickname = identity.get('nickname') or username
    except Exception as e:
        logger.error(f'SSO 获取云用户身份失败: {e}', exc_info=True)
        return _err(f'获取用户身份失败: {e}')

    # 匹配/创建本地用户（SSO 建号使用随机密码，仅云平台可登录）
    manager = get_auth_manager()
    user = manager.get_user_by_username(username)
    if user is None:
        try:
            user = manager.create_user({
                'username': username,
                'password': secrets.token_urlsafe(24),
                'role': 'user',
                'display_name': nickname,
            })
            logger.info(f'SSO 自动创建本地用户: {username}')
        except ValueError as e:
            return _err(f'创建本地用户失败: {e}')

    if user.enabled != 1:
        return _err('该用户已被禁用')

    session['user_id'] = user.id
    session['username'] = user.username
    session['role'] = user.role
    session.permanent = True
    logger.info(f"单点登录成功: {user.username}")

    return render_template('sso_callback.html', result_type='success', message=user.display_name or user.username)
