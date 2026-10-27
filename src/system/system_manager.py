"""
SageSight AI - 系统管理模块
管理流媒体服务器配置等系统设置
"""

import os
import json
import sqlite3
import time
import yaml
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime
from pathlib import Path

import logging

logger = logging.getLogger(__name__)

# ========================================
# 数据模型
# ========================================

@dataclass
class StreamingServer:
    """流媒体服务器配置"""
    id: int = 0
    name: str = ''  # 服务器名称
    server_type: str = 'zlmediakit'  # 服务器类型: zlmediakit
    host: str = 'localhost'  # 主机地址
    
    # 端口配置
    rtmp_port: int = 1935  # RTMP端口
    rtsp_port: int = 554  # RTSP端口
    http_port: int = 88  # HTTP端口（API端口）
    rtc_port: int = 8000  # RTC端口（WebRTC）
    
    # API配置
    secret: str = ''  # API密钥
    media_server_id: str = ''  # ZLM服务器ID

    # 状态配置
    enabled: bool = True  # 是否启用
    is_default: bool = False  # 是否默认服务器
    description: str = ''  # 描述
    status: str = 'unknown'  # 状态: online, offline, unknown
    
    # 扩展配置
    config: Dict[str, Any] = field(default_factory=dict)  # 其他扩展配置
    
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        def format_datetime(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value)
        
        return {
            'id': self.id,
            'name': self.name,
            'server_type': self.server_type,
            'host': self.host,
            'rtmp_port': self.rtmp_port,
            'rtsp_port': self.rtsp_port,
            'http_port': self.http_port,
            'rtc_port': self.rtc_port,
            'secret': self.secret,
            'media_server_id': self.media_server_id,
            'enabled': self.enabled,
            'is_default': self.is_default,
            'description': self.description,
            'status': self.status,
            'config': self.config,
            'created_at': format_datetime(self.created_at),
            'updated_at': format_datetime(self.updated_at)
        }


@dataclass
class LLMConfig:
    """大语言模型 API 接入配置"""
    id: int = 0
    name: str = ''                      # 配置名称
    provider: str = 'openai'            # API 格式: openai / anthropic
    base_url: str = ''                  # API base URL
    api_key: str = ''                   # API 密钥
    model: str = ''                     # 模型名（如 gpt-4o, claude-3-5-sonnet-20241022）
    temperature: float = 0.3            # 温度参数
    max_tokens: int = 500               # 最大输出 token
    support_images: bool = True         # 模型是否支持图片输入（多模态），关闭时降级为纯文本
    enable_description: bool = True     # 启用事件描述
    enable_dedup: bool = True           # 启用语义去重（大模型对比快照；规则去重属事件管理）
    is_default: bool = False            # 是否默认
    enabled: bool = True                # 是否启用
    description: str = ''               # 描述
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（api_key 脱敏返回）"""
        def format_datetime(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value)

        return {
            'id': self.id,
            'name': self.name,
            'provider': self.provider,
            'base_url': self.base_url,
            'api_key': self.api_key[:8] + '***' if self.api_key else '',
            'api_key_configured': bool(self.api_key),
            'model': self.model,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'support_images': self.support_images,
            'enable_description': self.enable_description,
            'enable_dedup': self.enable_dedup,
            'is_default': self.is_default,
            'enabled': self.enabled,
            'description': self.description,
            'created_at': format_datetime(self.created_at),
            'updated_at': format_datetime(self.updated_at)
        }


# ========================================
# 默认配置
# ========================================

DEFAULT_STREAMING_SERVERS = [
    {
        'name': 'ZLMediaKit 流媒体服务器',
        'server_type': 'zlmediakit',
        'host': 'localhost',
        'rtmp_port': 1935,
        'rtsp_port': 554,
        'http_port': 88,
        'rtc_port': 8000,
        'secret': 'FpvUDVyWlqohv98EEE5Of3UcFhc18Ipt',
        'media_server_id': 'zlmedia-7d241d0e',
        'enabled': True,
        'is_default': True,
        'description': 'ZLMediaKit 流媒体服务器',
        'config': {}
    }
]


# ========================================
# 系统管理器
# ========================================

class SystemManager:
    """系统管理器 - 管理系统配置"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.db_path = self._get_db_path()
        self._enable_wal()
        self._init_database()
        logger.info(f"系统管理器已初始化，数据库路径: {self.db_path}")

    def _enable_wal(self) -> None:
        """开启 WAL + busy_timeout

        多进程（Web + 检测子进程）同时读写 tasks.db 时，默认的 rollback
        journal 会让读操作在写事务期间直接报 database is locked，
        导致「配置读不到 / 密钥读不到」这类看似配置丢失的间歇性故障。
        WAL 允许读写并发，busy_timeout 让偶发写锁自动等待而不是立即失败。
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=15)
            try:
                mode = conn.execute('PRAGMA journal_mode=WAL').fetchone()
                conn.execute('PRAGMA busy_timeout=15000')
                conn.commit()
                logger.info(f"数据库 journal_mode={mode[0] if mode else 'unknown'}, busy_timeout=15000")
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"设置 WAL 模式失败（不影响功能）: {e}")
    
    def _get_db_path(self) -> str:
        """获取数据库路径"""
        # 使用 tasks.db 作为统一数据库
        db_dir = Path(__file__).parent.parent.parent / 'data'
        db_dir.mkdir(parents=True, exist_ok=True)
        return str(db_dir / 'tasks.db')
    
    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建流媒体服务器配置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS streaming_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                server_type TEXT DEFAULT 'zlmediakit',
                host TEXT DEFAULT 'localhost',
                
                -- 端口配置
                rtmp_port INTEGER DEFAULT 1935,
                rtsp_port INTEGER DEFAULT 554,
                http_port INTEGER DEFAULT 88,
                rtc_port INTEGER DEFAULT 8000,
                
                -- API配置
                secret TEXT DEFAULT '',
                media_server_id TEXT DEFAULT '',

                -- 状态配置
                enabled INTEGER DEFAULT 1,
                is_default INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'unknown',
                
                -- 扩展配置
                config TEXT DEFAULT '{}',
                
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 检查是否有默认数据
        cursor.execute('SELECT COUNT(*) FROM streaming_servers')
        count = cursor.fetchone()[0]
        
        if count == 0:
            # 插入默认流媒体服务器配置
            for server in DEFAULT_STREAMING_SERVERS:
                cursor.execute('''
                    INSERT INTO streaming_servers (
                        name, server_type, host,
                        rtmp_port, rtsp_port, http_port, rtc_port,
                        secret, media_server_id,
                        enabled, is_default, description, status, config
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    server['name'],
                    server['server_type'],
                    server['host'],
                    server['rtmp_port'],
                    server['rtsp_port'],
                    server['http_port'],
                    server['rtc_port'],
                    server['secret'],
                    server['media_server_id'],
                    server['enabled'],
                    server['is_default'],
                    server['description'],
                    'unknown',
                    json.dumps(server['config'])
                ))
        
        conn.commit()
        logger.info("流媒体服务器配置表已初始化")

        # 创建大模型 API 配置表
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS llm_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                provider TEXT DEFAULT 'openai',
                base_url TEXT DEFAULT '',
                api_key TEXT DEFAULT '',
                model TEXT DEFAULT '',
                temperature REAL DEFAULT 0.3,
                max_tokens INTEGER DEFAULT 500,
                enable_description INTEGER DEFAULT 1,
                enable_dedup INTEGER DEFAULT 1,
                dedup_window_seconds INTEGER DEFAULT 300,
                dedup_iou_threshold REAL DEFAULT 0.3,
                is_default INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                description TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                support_images INTEGER DEFAULT 1
            )
        ''')
        # 旧库迁移：已有 llm_config 表时补充缺失列（追加在末尾，保持列顺序兼容）
        llm_columns = [col[1] for col in cursor.execute('PRAGMA table_info(llm_config)').fetchall()]
        if 'support_images' not in llm_columns:
            cursor.execute('ALTER TABLE llm_config ADD COLUMN support_images INTEGER DEFAULT 1')
            logger.info("llm_config 表已迁移：新增 support_images 列")
        if 'dedup_window_seconds' not in llm_columns:
            cursor.execute('ALTER TABLE llm_config ADD COLUMN dedup_window_seconds INTEGER DEFAULT 300')
            logger.info("llm_config 表已迁移：新增 dedup_window_seconds 列")
        if 'dedup_iou_threshold' not in llm_columns:
            cursor.execute('ALTER TABLE llm_config ADD COLUMN dedup_iou_threshold REAL DEFAULT 0.3')
            logger.info("llm_config 表已迁移：新增 dedup_iou_threshold 列")
        conn.commit()
        logger.info("大模型 API 配置表已初始化")

        # 创建系统全局设置表（key-value，单一真相源）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # 大模型总开关默认开启（INSERT OR IGNORE 保留已有设置）
        cursor.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('llm_enabled', '1')"
        )
        conn.commit()
        conn.close()
        logger.info("系统全局设置表已初始化")

    # ========================================
    # 流媒体服务器 CRUD
    # ========================================

    # 流媒体服务器查询列（显式列名，兼容旧库残留的 hook_url_prefix/protocol 列）
    _SERVER_COLUMNS = 'id, name, server_type, host, rtmp_port, rtsp_port, http_port, rtc_port, secret, media_server_id, enabled, is_default, description, status, config, created_at, updated_at'

    def get_all_streaming_servers(self) -> List[StreamingServer]:
        """获取所有流媒体服务器配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(f'SELECT {self._SERVER_COLUMNS} FROM streaming_servers ORDER BY is_default DESC, name')
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_streaming_server(row) for row in rows]

    def get_streaming_server(self, server_id: int) -> Optional[StreamingServer]:
        """获取单个流媒体服务器配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(f'SELECT {self._SERVER_COLUMNS} FROM streaming_servers WHERE id = ?', (server_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return self._row_to_streaming_server(row)
        return None

    def get_default_streaming_server(self) -> Optional[StreamingServer]:
        """获取默认流媒体服务器"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(f'SELECT {self._SERVER_COLUMNS} FROM streaming_servers WHERE is_default = 1 AND enabled = 1 LIMIT 1')
        row = cursor.fetchone()
        conn.close()

        if row:
            return self._row_to_streaming_server(row)
        return None
    
    def create_streaming_server(self, server: StreamingServer) -> int:
        """创建流媒体服务器配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 如果设置为默认，先清除其他默认设置
        if server.is_default:
            cursor.execute('UPDATE streaming_servers SET is_default = 0')
        
        cursor.execute('''
            INSERT INTO streaming_servers (
                name, server_type, host,
                rtmp_port, rtsp_port, http_port, rtc_port,
                secret, media_server_id,
                enabled, is_default, description, status, config
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            server.name,
            server.server_type,
            server.host,
            server.rtmp_port,
            server.rtsp_port,
            server.http_port,
            server.rtc_port,
            server.secret,
            server.media_server_id,
            server.enabled,
            server.is_default,
            server.description,
            'unknown',
            json.dumps(server.config)
        ))
        
        server_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        logger.info(f"创建流媒体服务器配置: {server.name}, ID: {server_id}")
        return server_id
    
    def update_streaming_server(self, server_id: int, server: StreamingServer) -> bool:
        """更新流媒体服务器配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 如果设置为默认，先清除其他默认设置
        if server.is_default:
            cursor.execute('UPDATE streaming_servers SET is_default = 0 WHERE id != ?', (server_id,))
        
        cursor.execute('''
            UPDATE streaming_servers SET
                name = ?, server_type = ?, host = ?,
                rtmp_port = ?, rtsp_port = ?, http_port = ?, rtc_port = ?,
                secret = ?, media_server_id = ?,
                enabled = ?, is_default = ?, description = ?,
                status = ?, config = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (
            server.name,
            server.server_type,
            server.host,
            server.rtmp_port,
            server.rtsp_port,
            server.http_port,
            server.rtc_port,
            server.secret,
            server.media_server_id,
            server.enabled,
            server.is_default,
            server.description,
            server.status,
            json.dumps(server.config),
            server_id
        ))
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if success:
            logger.info(f"更新流媒体服务器配置: {server.name}, ID: {server_id}")
        return success
    
    def delete_streaming_server(self, server_id: int) -> bool:
        """删除流媒体服务器配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取服务器信息
        cursor.execute('SELECT is_default FROM streaming_servers WHERE id = ?', (server_id,))
        row = cursor.fetchone()
        
        if row and row[0] == 1:
            # 如果是默认服务器，不允许删除
            conn.close()
            logger.warning(f"无法删除默认流媒体服务器: ID {server_id}")
            return False
        
        cursor.execute('DELETE FROM streaming_servers WHERE id = ?', (server_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if success:
            logger.info(f"删除流媒体服务器配置: ID {server_id}")
        return success
    
    
    def _row_to_streaming_server(self, row: tuple) -> StreamingServer:
        """将数据库行转换为 StreamingServer 对象"""
        def safe_json_loads(value, default):
            if not value:
                return default
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return default
        
        return StreamingServer(
            id=row[0],
            name=row[1] or '',
            server_type=row[2] or 'zlmediakit',
            host=row[3] or 'localhost',
            rtmp_port=row[4] or 1935,
            rtsp_port=row[5] or 554,
            http_port=row[6] or 88,
            rtc_port=row[7] or 8000,
            secret=row[8] or '',
            media_server_id=row[9] or '',
            enabled=row[10],
            is_default=row[11],
            description=row[12] or '',
            status=row[13] or 'unknown',
            config=safe_json_loads(row[14], {}),
            created_at=row[15],
            updated_at=row[16]
        )

    # ========================================
    # 大模型 API 配置 CRUD
    # ========================================

    # 显式列名（不用 SELECT *）：旧库增列顺序不一致时，位置映射会错位，
    # 导致 is_default / enabled / support_images 读串，进而出现
    # 「配置明明启用却判定未启用」「支持图片却当成纯文本」等诡异现象。
    _LLM_COLUMNS = (
        'id, name, provider, base_url, api_key, model, '
        'temperature, max_tokens, enable_description, enable_dedup, '
        'is_default, enabled, description, created_at, updated_at, support_images'
    )

    def get_all_llm_configs(self) -> List[LLMConfig]:
        """获取所有大模型配置"""
        conn = sqlite3.connect(self.db_path, timeout=15)
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT {self._LLM_COLUMNS} FROM llm_config ORDER BY is_default DESC, name'
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_llm_config(row) for row in rows]

    def get_llm_config(self, config_id: int) -> Optional[LLMConfig]:
        """获取单个大模型配置"""
        conn = sqlite3.connect(self.db_path, timeout=15)
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT {self._LLM_COLUMNS} FROM llm_config WHERE id = ?', (config_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return self._row_to_llm_config(row) if row else None

    def get_default_llm_config(self) -> Optional[LLMConfig]:
        """获取默认的大模型配置（is_default=1 且 enabled=1）"""
        conn = sqlite3.connect(self.db_path, timeout=15)
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT {self._LLM_COLUMNS} FROM llm_config '
            'WHERE is_default = 1 AND enabled = 1 LIMIT 1'
        )
        row = cursor.fetchone()
        conn.close()
        return self._row_to_llm_config(row) if row else None

    def create_llm_config(self, config: LLMConfig) -> int:
        """创建大模型配置"""
        conn = sqlite3.connect(self.db_path, timeout=15)
        cursor = conn.cursor()

        if config.is_default:
            cursor.execute('UPDATE llm_config SET is_default = 0')

        # 统一清洗：粘贴密钥常带换行/空格，直接入库会导致 HTTP 头非法
        api_key = (config.api_key or '').strip()

        cursor.execute('''
            INSERT INTO llm_config (
                name, provider, base_url, api_key, model,
                temperature, max_tokens,
                support_images,
                enable_description, enable_dedup,
                is_default, enabled, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            config.name, config.provider, (config.base_url or '').strip(), api_key, config.model,
            config.temperature, config.max_tokens,
            int(config.support_images),
            int(config.enable_description), int(config.enable_dedup),
            int(config.is_default), int(config.enabled), config.description
        ))
        conn.commit()
        config_id = cursor.lastrowid
        conn.close()
        return config_id

    def update_llm_config(self, config_id: int, config: LLMConfig) -> bool:
        """更新大模型配置"""
        conn = sqlite3.connect(self.db_path, timeout=15)
        cursor = conn.cursor()

        if config.is_default:
            cursor.execute('UPDATE llm_config SET is_default = 0 WHERE id != ?', (config_id,))

        api_key = (config.api_key or '').strip()
        base_url = (config.base_url or '').strip()

        # api_key 为空时不更新（前端脱敏返回，不回传真实 key 时保留原值）
        if api_key:
            cursor.execute('''
                UPDATE llm_config SET
                    name = ?, provider = ?, base_url = ?, api_key = ?, model = ?,
                    temperature = ?, max_tokens = ?,
                    support_images = ?,
                    enable_description = ?, enable_dedup = ?,
                    is_default = ?, enabled = ?, description = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                config.name, config.provider, base_url, api_key, config.model,
                config.temperature, config.max_tokens,
                int(config.support_images),
                int(config.enable_description), int(config.enable_dedup),
                int(config.is_default), int(config.enabled), config.description,
                config_id
            ))
        else:
            cursor.execute('''
                UPDATE llm_config SET
                    name = ?, provider = ?, base_url = ?, model = ?,
                    temperature = ?, max_tokens = ?,
                    support_images = ?,
                    enable_description = ?, enable_dedup = ?,
                    is_default = ?, enabled = ?, description = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                config.name, config.provider, base_url, config.model,
                config.temperature, config.max_tokens,
                int(config.support_images),
                int(config.enable_description), int(config.enable_dedup),
                int(config.is_default), int(config.enabled), config.description,
                config_id
            ))

        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success

    def delete_llm_config(self, config_id: int) -> bool:
        """删除大模型配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM llm_config WHERE id = ?', (config_id,))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success

    def set_default_llm_config(self, config_id: int) -> bool:
        """设置默认大模型配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE llm_config SET is_default = 0')
        cursor.execute('UPDATE llm_config SET is_default = 1 WHERE id = ?', (config_id,))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success

    # ========================================
    # 系统全局设置（key-value）
    # ========================================

    def get_setting(self, key: str, default: str = '') -> str:
        """读取系统全局设置

        带重试：大模型密钥等关键配置在 SQLite 短暂写锁期间读失败时，
        不能让上层误判为「未配置」（表现为「明明配了密钥却说没有」）。

        Args:
            key: 设置键名
            default: 不存在时的默认值

        Returns:
            设置值（字符串）
        """
        last_err = None
        for attempt in range(3):
            try:
                conn = sqlite3.connect(self.db_path, timeout=15)
                try:
                    row = conn.execute(
                        'SELECT value FROM system_settings WHERE key = ?', (key,)
                    ).fetchone()
                finally:
                    conn.close()
                return row[0] if row else default
            except sqlite3.OperationalError as e:
                last_err = e
                time.sleep(0.05 * (attempt + 1))
            except Exception as e:
                logger.warning(f"读取系统设置 {key} 失败: {e}")
                return default
        logger.warning(f"读取系统设置 {key} 失败（已重试）: {last_err}")
        return default

    def set_setting(self, key: str, value: str) -> bool:
        """写入系统全局设置（存在则更新）

        Args:
            key: 设置键名
            value: 设置值

        Returns:
            是否写入成功
        """
        conn = sqlite3.connect(self.db_path, timeout=15)
        try:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO system_settings (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value, updated_at = CURRENT_TIMESTAMP''',
                (key, value)
            )
            conn.commit()
            # 注意：INSERT ... ON CONFLICT 在「值未变化」时 rowcount 可能为 0，
            # 不能用 rowcount 判断成败，否则会误报「保存失败」。
            return True
        except Exception as e:
            logger.warning(f"写入系统设置 {key} 失败: {e}")
            return False
        finally:
            conn.close()

    def is_llm_enabled(self) -> bool:
        """大模型功能总开关：关闭时整个系统不调用大模型（防止 token 费用）"""
        return self.get_setting('llm_enabled', '1') in ('1', 'true', 'True')

    def set_llm_enabled(self, enabled: bool) -> bool:
        """设置大模型功能总开关"""
        return self.set_setting('llm_enabled', '1' if enabled else '0')

    def _row_to_llm_config(self, row: tuple) -> LLMConfig:
        """将数据库行转换为 LLMConfig 对象（列顺序见 _LLM_COLUMNS）"""
        return LLMConfig(
            id=row[0],
            name=row[1] or '',
            provider=row[2] or 'openai',
            base_url=row[3] or '',
            api_key=(row[4] or '').strip(),
            model=row[5] or '',
            temperature=row[6] if row[6] is not None else 0.3,
            max_tokens=row[7] if row[7] is not None else 500,
            enable_description=bool(row[8]) if row[8] is not None else True,
            enable_dedup=bool(row[9]) if row[9] is not None else True,
            is_default=bool(row[10]),
            enabled=bool(row[11]) if row[11] is not None else True,
            description=row[12] or '',
            created_at=row[13],
            updated_at=row[14],
            support_images=bool(row[15]) if row[15] is not None else True
        )


# ========================================
# 全局实例
# ========================================

def get_system_manager() -> SystemManager:
    """获取系统管理器实例"""
    return SystemManager()