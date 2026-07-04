"""
用户认证管理模块
提供用户表的CRUD操作和密码验证功能
"""

import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)


class UserConfig:
    """用户配置数据类"""
    
    def __init__(self, id: int = None, username: str = '', password_hash: str = '',
                 role: str = 'user', display_name: str = '', email: str = '',
                 enabled: int = 1, created_at: str = None, **kwargs):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.display_name = display_name
        self.email = email
        self.enabled = enabled
        self.created_at = created_at

    def to_dict(self, include_password: bool = False) -> Dict:
        """转换为字典"""
        data = {
            'id': self.id,
            'username': self.username,
            'role': self.role,
            'display_name': self.display_name,
            'email': self.email,
            'enabled': self.enabled,
            'created_at': self.created_at
        }
        if include_password:
            data['password_hash'] = self.password_hash
        return data


class AuthManager:
    """用户认证管理器"""
    
    def __init__(self, db_path: str = "data/tasks.db"):
        self.db_path = db_path
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    
    def _init_database(self):
        """初始化用户表"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username VARCHAR(50) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) DEFAULT 'user',
                    display_name VARCHAR(50) DEFAULT '',
                    email VARCHAR(100) DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'email' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN email VARCHAR(100) DEFAULT ''")
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)
            ''')
            
            # 初始化默认管理员账户
            cursor.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            
            if count == 0:
                default_password = generate_password_hash('admin123', method='pbkdf2:sha256')
                cursor.execute('''
                    INSERT INTO users (username, password_hash, role, display_name, enabled)
                    VALUES (?, ?, 'admin', '系统管理员', 1)
                ''', ('admin', default_password))
                logger.info(f"已创建默认管理员账户: admin / admin123, hash={default_password[:30]}...")
            
            conn.commit()
    
    def _row_to_user(self, row: tuple) -> UserConfig:
        """将数据库行转换为UserConfig对象"""
        return UserConfig(
            id=row[0],
            username=row[1],
            password_hash=row[2],
            role=row[3],
            display_name=row[4] or '',
            email=row[5] if len(row) > 5 and row[5] else '',
            enabled=row[6] if len(row) > 6 else 1,
            created_at=row[7] if len(row) > 7 else None
        )
    
    def authenticate(self, username: str, password: str) -> Optional[UserConfig]:
        """验证用户名和密码"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, role, display_name, email, enabled, created_at
                FROM users WHERE username = ?
            ''', (username,))
            row = cursor.fetchone()
            
            if not row:
                logger.debug(f"认证失败: 用户不存在 username={username}")
                return None
            
            user = self._row_to_user(row)
            
            if user.enabled != 1:
                logger.debug(f"认证失败: 用户已禁用 username={username}, enabled={user.enabled}")
                return None
            
            if not check_password_hash(user.password_hash, password):
                logger.debug(f"认证失败: 密码不匹配 username={username}, hash_len={len(user.password_hash)}")
                return None
            
            return user
    
    def get_user(self, user_id: int) -> Optional[UserConfig]:
        """根据ID获取用户"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, role, display_name, email, enabled, created_at
                FROM users WHERE id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            return self._row_to_user(row) if row else None
    
    def get_user_by_username(self, username: str) -> Optional[UserConfig]:
        """根据用户名获取用户"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, role, display_name, email, enabled, created_at
                FROM users WHERE username = ?
            ''', (username,))
            row = cursor.fetchone()
            return self._row_to_user(row) if row else None
    
    def get_all_users(self) -> List[UserConfig]:
        """获取所有用户"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, role, display_name, email, enabled, created_at
                FROM users ORDER BY created_at ASC
            ''')
            rows = cursor.fetchall()
            return [self._row_to_user(row) for row in rows]
    
    def create_user(self, data: Dict) -> UserConfig:
        """创建用户"""
        username = data.get('username')
        if not username:
            raise ValueError('用户名不能为空')
        
        if self.get_user_by_username(username):
            raise ValueError('用户名已存在')
        
        password = data.get('password', '')
        if not password:
            raise ValueError('密码不能为空')
        
        password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute('''
                INSERT INTO users (username, password_hash, role, display_name, email, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                username,
                password_hash,
                data.get('role', 'user'),
                data.get('display_name', ''),
                data.get('email', ''),
                data.get('enabled', 1),
                now,
                now
            ))
            user_id = cursor.lastrowid
            conn.commit()
            
            return self.get_user(user_id)
    
    def update_user(self, user_id: int, data: Dict) -> Optional[UserConfig]:
        """更新用户"""
        user = self.get_user(user_id)
        if not user:
            return None
        
        updatable_fields = {
            'role': 'role',
            'display_name': 'display_name',
            'email': 'email',
            'enabled': 'enabled'
        }
        
        set_clauses = []
        params = []
        
        for field, col in updatable_fields.items():
            if field in data:
                set_clauses.append(f"{col} = ?")
                params.append(data[field])
        
        # 密码单独处理
        if data.get('password'):
            set_clauses.append("password_hash = ?")
            params.append(generate_password_hash(data['password'], method='pbkdf2:sha256'))
        
        if not set_clauses:
            return user
        
        set_clauses.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(user_id)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE users SET {', '.join(set_clauses)} WHERE id = ?",
                params
            )
            conn.commit()
            
            return self.get_user(user_id)
    
    def delete_user(self, user_id: int) -> bool:
        """删除用户"""
        user = self.get_user(user_id)
        if not user:
            return False
        
        # 不允许删除最后一个管理员
        if user.role == 'admin':
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1")
                admin_count = cursor.fetchone()[0]
                if admin_count <= 1:
                    raise ValueError('不允许删除最后一个管理员账户')
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0


# 单例
_auth_manager = None


def get_auth_manager() -> AuthManager:
    """获取全局AuthManager实例"""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
