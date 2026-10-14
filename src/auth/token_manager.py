"""
API Token 管理模块

为移动端 App 提供 Token 鉴权：登录成功后签发 Token，后续请求通过
Authorization: Bearer <token> 携带。Token 持久化存储于 data/tasks.db，
默认有效期 30 天。
"""

import logging
import secrets
import sqlite3
import time
from typing import Optional, Dict

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # Token 有效期 30 天


class TokenManager:
    """API Token 管理器（单例）"""

    def __init__(self, db_path: str = "data/tasks.db"):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """确保 Token 表存在"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS api_tokens (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            ''')

    def issue_token(self, user_id: int, username: str, role: str) -> str:
        """为用户签发新 Token"""
        token = secrets.token_hex(32)
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            # 同一用户只保留最新 Token（旧 Token 立即失效）
            conn.execute('DELETE FROM api_tokens WHERE user_id = ?', (user_id,))
            conn.execute(
                'INSERT INTO api_tokens (token, user_id, username, role, created_at, expires_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (token, user_id, username, role, now, now + TOKEN_TTL_SECONDS)
            )
        return token

    def validate_token(self, token: str) -> Optional[Dict]:
        """校验 Token，有效时返回用户信息字典，无效返回 None"""
        if not token:
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    'SELECT token, user_id, username, role, expires_at FROM api_tokens WHERE token = ?',
                    (token,)
                ).fetchone()
            if row is None:
                return None
            if row['expires_at'] < time.time():
                self.revoke_token(token)
                return None
            return {
                'user_id': row['user_id'],
                'username': row['username'],
                'role': row['role']
            }
        except Exception as e:
            logger.error(f"[TokenManager] 校验 Token 失败: {e}")
            return None

    def revoke_token(self, token: str):
        """吊销 Token"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM api_tokens WHERE token = ?', (token,))
        except Exception as e:
            logger.error(f"[TokenManager] 吊销 Token 失败: {e}")

    def revoke_user_tokens(self, user_id: int):
        """吊销指定用户的全部 Token（登出/禁用用户时调用）"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM api_tokens WHERE user_id = ?', (user_id,))
        except Exception as e:
            logger.error(f"[TokenManager] 吊销用户 Token 失败: {e}")

    def cleanup_expired(self):
        """清理过期 Token"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM api_tokens WHERE expires_at < ?', (time.time(),))
        except Exception as e:
            logger.error(f"[TokenManager] 清理过期 Token 失败: {e}")


_token_manager: Optional[TokenManager] = None


def get_token_manager() -> TokenManager:
    """获取 TokenManager 单例"""
    global _token_manager
    if _token_manager is None:
        _token_manager = TokenManager()
    return _token_manager
