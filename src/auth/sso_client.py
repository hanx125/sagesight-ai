"""
SSO OAuth2 授权码流客户端

负责与云平台交互的 OAuth2 授权码流三个环节：
  1. 构建云平台授权页跳转 URL（可选 PKCE）
  2. 用授权码换取 token（access_token）
  3. 按配置获取云用户身份（用于本地会话创建）

仅承担"协议搬运"，不关心本地用户/Session 的逻辑；
本模块运行在服务端，client_secret 只在此保存，不落地前端。
"""
import base64
import hashlib
import logging
import secrets
import urllib.parse
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

# OAuth 令牌响应中的常用字段映射（兼容不同命名风格）
_ACCESS_TOKEN_KEYS = ('access_token', 'accessToken')
_EXPIRES_KEYS = ('expires_in', 'expiresIn')
_TOKEN_TYPE_KEYS = ('token_type', 'tokenType')
_TENANT_KEYS = ('tenant_id', 'tenantId')


def generate_state() -> str:
    """生成 32 字节随机 state，防 CSRF。"""
    return secrets.token_urlsafe(32)


def generate_code_verifier() -> str:
    """生成 PKCE code_verifier（RFC 7636：43~128 位 [A-Za-z0-9-._~]）。"""
    return secrets.token_urlsafe(64)


def generate_code_challenge(code_verifier: str) -> str:
    """生成 PKCE code_challenge（S256）。

    code_challenge = base64url(sha256(code_verifier 原始字节))，不携带填充。
    """
    digest = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('utf-8')


def build_authorize_url(cfg: Dict, state: str, code_verifier: Optional[str] = None) -> str:
    """构建云授权页跳转地址。

    GET {authorize_base_url}{authorize_url}
        ?response_type=code&client_id=&redirect_uri=&state=&scope=
        &code_challenge=&code_challenge_method=S256   # use_pkce 时

    授权页走云前端入口（authorize_base_url），缺省回退复用 cloud_base_url。
    """
    base = (cfg.get('authorize_base_url') or cfg.get('cloud_base_url', '')).rstrip('/')
    params = {
        'response_type': 'code',
        'client_id': cfg.get('client_id', ''),
        'redirect_uri': cfg.get('redirect_uri', ''),
        'state': state,
    }
    scope = (cfg.get('scope') or '').strip()
    if scope:
        params['scope'] = scope

    if cfg.get('use_pkce', True) and code_verifier:
        params['code_challenge'] = generate_code_challenge(code_verifier)
        params['code_challenge_method'] = 'S256'

    # 使用 quote 让 scope 中的空格编码为 %20（与云侧按空格切分保持一致）
    return f'{base}{cfg.get("authorize_url", "")}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}'


def exchange_code(cfg: Dict, code: str, state: str, code_verifier: Optional[str] = None) -> Dict:
    """用授权码换取 token。

    采用 Basic 认证携带 client_id / client_secret；走 PKCE 时 client_secret 可留空。
    云侧会比对授权时下发的 state，因此须原样回传同一 state。
    """
    base = cfg.get('cloud_base_url', '').rstrip('/')
    token_path = cfg.get('token_url') or '/system/oauth2/token'
    client_id = cfg.get('client_id', '')
    client_secret = cfg.get('client_secret', '')

    credentials = base64.b64encode(f'{client_id}:{client_secret}'.encode('utf-8')).decode('utf-8')
    headers = {'Authorization': f'Basic {credentials}'}

    # 云网关只转发 URL query 参数，form 表单体会在网关层丢失，必须用 params 传参
    params = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': cfg.get('redirect_uri', ''),
        'client_id': client_id,
        'state': state,
    }
    scope = (cfg.get('scope') or '').strip()
    if scope:
        params['scope'] = scope
    if code_verifier:
        params['code_verifier'] = code_verifier

    url = f'{base}{token_path}'
    logger.info(f'SSO 换取令牌: {url}, client_id={client_id}')

    resp = requests.post(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    if result.get('code') not in (0, '0', None):
        logger.error(f'SSO 换取令牌失败，云响应: code={result.get("code")} msg={result.get("msg")}')
        raise RuntimeError(f'云平台换取令牌失败: {result.get("msg", "未知错误")}')

    token_data = result.get('data') or {}

    def _first(mapping, keys):
        for k in keys:
            if mapping.get(k) is not None:
                return mapping[k]
        return None

    access_token = _first(token_data, _ACCESS_TOKEN_KEYS)
    if not access_token:
        raise RuntimeError('云平台令牌响应未包含 access_token')

    return {
        'access_token': access_token,
        'token_type': _first(token_data, _TOKEN_TYPE_KEYS) or 'Bearer',
        'expires_in': _first(token_data, _EXPIRES_KEYS) or 7200,
        'tenant_id': _first(token_data, _TENANT_KEYS),
    }


def fetch_user_identity(cfg: Dict, access_token: str) -> Dict:
    """获取云用户身份。

    端点返回通常为：{"code":0,"data":{"id":..,"username":..,"nickname":..,...}}
    """
    user_info_url = (cfg.get('user_info_url') or '').strip()
    if not user_info_url:
        raise RuntimeError('未配置用户信息端点（user_info_url）')

    base = cfg.get('cloud_base_url', '').rstrip('/')
    endpoint = user_info_url if user_info_url.startswith('http') else f'{base}{user_info_url}'
    token_type = cfg.get('token_type', 'Bearer')

    headers = {'Authorization': f'{token_type} {access_token}'}
    resp = requests.get(endpoint, headers=headers, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if result.get('code') not in (0, '0', None):
        raise RuntimeError(f'云平台用户信息获取失败: {result.get("msg", "未知错误")}')

    identity = result.get('data') or {}
    username = identity.get('username') or identity.get('userName')
    if not username:
        raise RuntimeError('云平台用户信息响应未包含 username')
    return {
        'username': str(username),
        'nickname': identity.get('nickname') or identity.get('nickName') or username,
        'id': identity.get('id'),
        'avatar': identity.get('avatar'),
    }


def cloud_scope_ready(cfg: Dict) -> bool:
    """自检：云侧关键配置是否已填。"""
    return bool(cfg and cfg.get('cloud_base_url') and cfg.get('client_id')
                and cfg.get('redirect_uri') and cfg.get('authorize_url'))
