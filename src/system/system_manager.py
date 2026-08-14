"""
SageSight AI - 系统管理模块
管理流媒体服务器配置等系统设置
"""

import os
import json
import sqlite3
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
    
    # 回调配置
    hook_url_prefix: str = ''  # 回调URL前缀（如 http://192.168.5.2:5000）
    
    # 状态配置
    enabled: bool = True  # 是否启用
    is_default: bool = False  # 是否默认服务器
    protocol: str = 'rtmp'  # 协议: rtmp, rtsp, hls, webrtc
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
            'hook_url_prefix': self.hook_url_prefix,
            'enabled': self.enabled,
            'is_default': self.is_default,
            'protocol': self.protocol,
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
    enable_description: bool = True     # 启用事件描述
    enable_dedup: bool = True           # 启用语义去重
    dedup_window_seconds: int = 300     # 去重时间窗口（秒）
    dedup_iou_threshold: float = 0.3    # 规则层去重 IoU 阈值
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
            'enable_description': self.enable_description,
            'enable_dedup': self.enable_dedup,
            'dedup_window_seconds': self.dedup_window_seconds,
            'dedup_iou_threshold': self.dedup_iou_threshold,
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
        'hook_url_prefix': 'http://localhost:5000',
        'enabled': True,
        'is_default': True,
        'protocol': 'rtmp',
        'description': 'ZLMediaKit 流媒体服务器',
        'config': {}
    }
]


def _get_web_base_url() -> str:
    """从 system_config.yaml 读取 Web 服务地址，构造 hook_url_prefix。"""
    from src.system.system_config_loader import get_webhook_base_url
    return get_webhook_base_url()


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
        self._init_database()
        logger.info(f"系统管理器已初始化，数据库路径: {self.db_path}")
    
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
                
                -- 回调配置
                hook_url_prefix TEXT DEFAULT '',
                
                -- 状态配置
                enabled INTEGER DEFAULT 1,
                is_default INTEGER DEFAULT 0,
                protocol TEXT DEFAULT 'rtmp',
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
                        secret, media_server_id, hook_url_prefix,
                        enabled, is_default, protocol, description, status, config
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    server.get('hook_url_prefix') or _get_web_base_url(),
                    server['enabled'],
                    server['is_default'],
                    server['protocol'],
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
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("大模型 API 配置表已初始化")

    # ========================================
    # 流媒体服务器 CRUD
    # ========================================

    def get_all_streaming_servers(self) -> List[StreamingServer]:
        """获取所有流媒体服务器配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM streaming_servers ORDER BY is_default DESC, name')
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_streaming_server(row) for row in rows]
    
    def get_streaming_server(self, server_id: int) -> Optional[StreamingServer]:
        """获取单个流媒体服务器配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM streaming_servers WHERE id = ?', (server_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return self._row_to_streaming_server(row)
        return None
    
    def get_default_streaming_server(self) -> Optional[StreamingServer]:
        """获取默认流媒体服务器"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM streaming_servers WHERE is_default = 1 AND enabled = 1 LIMIT 1')
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
                secret, media_server_id, hook_url_prefix,
                enabled, is_default, protocol, description, status, config
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            server.hook_url_prefix,
            server.enabled,
            server.is_default,
            server.protocol,
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
                secret = ?, media_server_id = ?, hook_url_prefix = ?,
                enabled = ?, is_default = ?, protocol = ?, description = ?,
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
            server.hook_url_prefix,
            server.enabled,
            server.is_default,
            server.protocol,
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
            hook_url_prefix=row[10] or '',
            enabled=row[11],
            is_default=row[12],
            protocol=row[13] or 'rtmp',
            description=row[14] or '',
            status=row[15] or 'unknown',
            config=safe_json_loads(row[16], {}),
            created_at=row[17],
            updated_at=row[18]
        )

    # ========================================
    # 大模型 API 配置 CRUD
    # ========================================

    def get_all_llm_configs(self) -> List[LLMConfig]:
        """获取所有大模型配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_config ORDER BY is_default DESC, name')
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_llm_config(row) for row in rows]

    def get_llm_config(self, config_id: int) -> Optional[LLMConfig]:
        """获取单个大模型配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_config WHERE id = ?', (config_id,))
        row = cursor.fetchone()
        conn.close()
        return self._row_to_llm_config(row) if row else None

    def get_default_llm_config(self) -> Optional[LLMConfig]:
        """获取默认的大模型配置（is_default=1 且 enabled=1）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_config WHERE is_default = 1 AND enabled = 1 LIMIT 1')
        row = cursor.fetchone()
        conn.close()
        return self._row_to_llm_config(row) if row else None

    def create_llm_config(self, config: LLMConfig) -> int:
        """创建大模型配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if config.is_default:
            cursor.execute('UPDATE llm_config SET is_default = 0')

        cursor.execute('''
            INSERT INTO llm_config (
                name, provider, base_url, api_key, model,
                temperature, max_tokens,
                enable_description, enable_dedup,
                dedup_window_seconds, dedup_iou_threshold,
                is_default, enabled, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            config.name, config.provider, config.base_url, config.api_key, config.model,
            config.temperature, config.max_tokens,
            int(config.enable_description), int(config.enable_dedup),
            config.dedup_window_seconds, config.dedup_iou_threshold,
            int(config.is_default), int(config.enabled), config.description
        ))
        conn.commit()
        config_id = cursor.lastrowid
        conn.close()
        return config_id

    def update_llm_config(self, config_id: int, config: LLMConfig) -> bool:
        """更新大模型配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if config.is_default:
            cursor.execute('UPDATE llm_config SET is_default = 0 WHERE id != ?', (config_id,))

        # api_key 为空时不更新（前端脱敏返回，不回传真实 key 时保留原值）
        if config.api_key:
            cursor.execute('''
                UPDATE llm_config SET
                    name = ?, provider = ?, base_url = ?, api_key = ?, model = ?,
                    temperature = ?, max_tokens = ?,
                    enable_description = ?, enable_dedup = ?,
                    dedup_window_seconds = ?, dedup_iou_threshold = ?,
                    is_default = ?, enabled = ?, description = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                config.name, config.provider, config.base_url, config.api_key, config.model,
                config.temperature, config.max_tokens,
                int(config.enable_description), int(config.enable_dedup),
                config.dedup_window_seconds, config.dedup_iou_threshold,
                int(config.is_default), int(config.enabled), config.description,
                config_id
            ))
        else:
            cursor.execute('''
                UPDATE llm_config SET
                    name = ?, provider = ?, base_url = ?, model = ?,
                    temperature = ?, max_tokens = ?,
                    enable_description = ?, enable_dedup = ?,
                    dedup_window_seconds = ?, dedup_iou_threshold = ?,
                    is_default = ?, enabled = ?, description = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                config.name, config.provider, config.base_url, config.model,
                config.temperature, config.max_tokens,
                int(config.enable_description), int(config.enable_dedup),
                config.dedup_window_seconds, config.dedup_iou_threshold,
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

    def _row_to_llm_config(self, row: tuple) -> LLMConfig:
        """将数据库行转换为 LLMConfig 对象"""
        return LLMConfig(
            id=row[0],
            name=row[1] or '',
            provider=row[2] or 'openai',
            base_url=row[3] or '',
            api_key=row[4] or '',
            model=row[5] or '',
            temperature=row[6] if row[6] is not None else 0.3,
            max_tokens=row[7] if row[7] is not None else 500,
            enable_description=bool(row[8]) if row[8] is not None else True,
            enable_dedup=bool(row[9]) if row[9] is not None else True,
            dedup_window_seconds=row[10] if row[10] is not None else 300,
            dedup_iou_threshold=row[11] if row[11] is not None else 0.3,
            is_default=bool(row[12]),
            enabled=bool(row[13]) if row[13] is not None else True,
            description=row[14] or '',
            created_at=row[15],
            updated_at=row[16]
        )


# ========================================
# 全局实例
# ========================================

def get_system_manager() -> SystemManager:
    """获取系统管理器实例"""
    return SystemManager()