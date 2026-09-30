"""
摄像机管理器

管理摄像机设备的数据库存储、查询和状态管理。
支持ONVIF协议发现和注册摄像机。
"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ========================================
# 数据类定义
# ========================================

@dataclass
class Camera:
    """摄像机数据类 - 字段参考 VIDEO 项目 Device 模型"""
    id: str = ""  # UUID格式
    name: str = ""  # 摄像机名称
    source: str = ""  # 取流地址（RTSP/RTMP）
    
    # ONVIF连接信息
    ip: str = ""  # IP地址
    port: int = 80  # ONVIF端口
    username: str = ""  # 用户名
    password: str = ""  # 密码
    
    # 设备信息（通过ONVIF获取）
    mac: str = ""  # MAC地址
    manufacturer: str = ""  # 制造商
    model: str = ""  # 型号
    firmware_version: str = ""  # 固件版本
    serial_number: str = ""  # 序列号
    hardware_id: str = ""  # 硬件ID
    
    # PTZ能力
    support_move: bool = False  # 是否支持移动
    support_zoom: bool = False  # 是否支持变焦
    ptz_address: str = ""  # PTZ服务地址
    
    # 音频能力
    support_audio_talk: bool = False  # 是否支持音频对讲
    
    # 接入协议和AI检测
    protocol: str = "rtsp"  # 接入协议（onvif、rtsp、GB28181）
    ai_detection: bool = True  # AI检测开关，默认true
    
    # 流媒体配置
    rtmp_stream: str = ""  # RTMP流地址（ZLM转换后）
    rtsp_stream: str = ""  # RTSP流地址（ZLM转换后）
    http_stream: str = ""  # HTTP-FLV流地址（ZLM转换后）
    ws_stream: str = ""  # WebSocket-FLV流地址（ZLM转换后）
    rtc_stream: str = ""  # WebRTC流地址（ZLM转换后）
    rtcs_stream: str = ""  # WebRTC HTTPS流地址（ZLM转换后）
    enable_forward: bool = False  # 是否启用推流转发
    
    # 状态
    status: str = "offline"  # online, offline, error
    connection_status: str = ""  # 连接状态描述
    
    # 目录分组
    directory_id: Optional[int] = None  # 所属目录ID
    
    # 物联网坐标（同步自边缘平台，AI 事件回传物联网解析依据）
    iot_product_key: str = ""  # 物联网产品标识 productKey
    iot_device_name: str = ""  # 物联网设备名称
    
    # 其他
    description: str = ""  # 描述
    enabled: bool = True  # 是否启用
    
    # 时间戳
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'source': self.source,
            'ip': self.ip,
            'port': self.port,
            'username': self.username,
            'password': self.password,
            'mac': self.mac,
            'manufacturer': self.manufacturer,
            'model': self.model,
            'firmware_version': self.firmware_version,
            'serial_number': self.serial_number,
            'hardware_id': self.hardware_id,
            'support_move': self.support_move,
            'support_zoom': self.support_zoom,
            'ptz_address': self.ptz_address,
            'support_audio_talk': self.support_audio_talk,
            'protocol': self.protocol,
            'ai_detection': self.ai_detection,
            'rtmp_stream': self.rtmp_stream,
            'rtsp_stream': self.rtsp_stream,
            'http_stream': self.http_stream,
            'ws_stream': self.ws_stream,
            'rtc_stream': self.rtc_stream,
            'rtcs_stream': self.rtcs_stream,
            'enable_forward': self.enable_forward,
            'status': self.status,
            'connection_status': self.connection_status,
            'directory_id': self.directory_id,
            'description': self.description,
            'iot_product_key': self.iot_product_key,
            'iot_device_name': self.iot_device_name,
            'enabled': self.enabled,
            'online': self.status == 'online',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Camera':
        """从字典创建"""
        def parse_datetime(value):
            if value is None or value == '':
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except:
                    return None
            return None
        
        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            source=data.get('source', ''),
            ip=data.get('ip', ''),
            port=data.get('port', 80),
            username=data.get('username', ''),
            password=data.get('password', ''),
            mac=data.get('mac', ''),
            manufacturer=data.get('manufacturer', ''),
            model=data.get('model', ''),
            firmware_version=data.get('firmware_version', ''),
            serial_number=data.get('serial_number', ''),
            hardware_id=data.get('hardware_id', ''),
            support_move=data.get('support_move', False),
            support_zoom=data.get('support_zoom', False),
            ptz_address=data.get('ptz_address', ''),
            support_audio_talk=data.get('support_audio_talk', False),
            protocol=data.get('protocol', 'rtsp'),
            ai_detection=data.get('ai_detection', True),
            rtmp_stream=data.get('rtmp_stream', ''),
            rtsp_stream=data.get('rtsp_stream', ''),
            http_stream=data.get('http_stream', ''),
            enable_forward=data.get('enable_forward', False),
            status=data.get('status', 'offline'),
            connection_status=data.get('connection_status', ''),
            directory_id=data.get('directory_id'),
            description=data.get('description', ''),
            iot_product_key=data.get('iot_product_key', ''),
            iot_device_name=data.get('iot_device_name', ''),
            enabled=data.get('enabled', True),
            created_at=parse_datetime(data.get('created_at')),
            updated_at=parse_datetime(data.get('updated_at'))
        )


@dataclass
class PatrolPath:
    """巡航路径数据类"""
    id: int = 0  # 路径ID（自增）
    camera_id: str = ""  # 所属摄像机ID
    name: str = ""  # 路径名称
    presets: str = ""  # 预置点列表（JSON格式存储）
    durations: str = ""  # 停留时间列表（JSON格式存储）
    description: str = ""  # 描述
    enabled: bool = True  # 是否启用
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'id': self.id,
            'camera_id': self.camera_id,
            'name': self.name,
            'presets': json.loads(self.presets) if self.presets else [],
            'durations': json.loads(self.durations) if self.durations else [],
            'description': self.description,
            'enabled': self.enabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'PatrolPath':
        """从字典创建"""
        def parse_datetime(value):
            if value is None or value == '':
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except:
                    return None
            return None
        
        return cls(
            id=data.get('id', 0),
            camera_id=data.get('camera_id', ''),
            name=data.get('name', ''),
            presets=data.get('presets', ''),
            durations=data.get('durations', ''),
            description=data.get('description', ''),
            enabled=data.get('enabled', True),
            created_at=parse_datetime(data.get('created_at')),
            updated_at=parse_datetime(data.get('updated_at'))
        )


@dataclass
class CameraDirectory:
    """摄像机目录分组"""
    id: Optional[int] = None
    name: str = ""
    parent_id: Optional[int] = None
    description: str = ""
    sort_order: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'name': self.name,
            'parent_id': self.parent_id,
            'description': self.description,
            'sort_order': self.sort_order,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


# ========================================
# 摄像机管理器
# ========================================

class CameraManager:
    """摄像机管理器"""
    
    def __init__(self, db_path: str = "data/tasks.db"):
        """
        初始化摄像机管理器
        
        Args:
            db_path: 数据库文件路径（与其他模块共用）
        """
        self.db_path = db_path
        self._init_database()
    
    def _get_db_path(self) -> str:
        """获取数据库路径"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        return self.db_path
    
    def _init_database(self):
        """初始化数据库表"""
        db_path = self._get_db_path()
        
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # ========================================
            # 摄像机表
            # ========================================
            # 检查表是否存在以及结构是否匹配
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cameras'")
            table_exists = cursor.fetchone() is not None
            
            if table_exists:
                # 检查表结构是否包含必要的字段
                cursor.execute("PRAGMA table_info(cameras)")
                columns = [row[1] for row in cursor.fetchall()]
                
                # 如果缺少必要的字段，删除旧表
                required_columns = ['id', 'name', 'source', 'ip', 'port', 'username', 'password']
                if not all(col in columns for col in required_columns):
                    logger.warning("[CameraManager] 摄像机表结构不匹配，删除旧表并重新创建")
                    cursor.execute('DROP TABLE cameras')
                    table_exists = False
                else:
                    # 迁移：添加缺失的字段
                    if 'rtsp_stream' not in columns:
                        logger.info("[CameraManager] 迁移：添加 rtsp_stream 字段")
                        cursor.execute("ALTER TABLE cameras ADD COLUMN rtsp_stream TEXT DEFAULT ''")
                    if 'protocol' not in columns:
                        logger.info("[CameraManager] 迁移：添加 protocol 字段")
                        cursor.execute("ALTER TABLE cameras ADD COLUMN protocol TEXT DEFAULT 'rtsp'")
                        # 自动识别并更新ONVIF摄像机的protocol字段
                        cursor.execute("UPDATE cameras SET protocol = 'onvif' WHERE ptz_address != '' AND ptz_address IS NOT NULL")
                        logger.info("[CameraManager] 自动识别ONVIF摄像机并更新protocol字段")
                    if 'ai_detection' not in columns:
                        logger.info("[CameraManager] 迁移：添加 ai_detection 字段")
                        cursor.execute("ALTER TABLE cameras ADD COLUMN ai_detection INTEGER DEFAULT 1")
                    if 'iot_product_key' not in columns:
                        logger.info("[CameraManager] 迁移：添加 iot_product_key 字段")
                        cursor.execute("ALTER TABLE cameras ADD COLUMN iot_product_key TEXT DEFAULT ''")
                    if 'iot_device_name' not in columns:
                        logger.info("[CameraManager] 迁移：添加 iot_device_name 字段")
                        cursor.execute("ALTER TABLE cameras ADD COLUMN iot_device_name TEXT DEFAULT ''")
            
            if not table_exists:
                cursor.execute('''
                    CREATE TABLE cameras (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        source TEXT NOT NULL,
                        
                        -- ONVIF连接信息
                        ip TEXT DEFAULT '',
                        port INTEGER DEFAULT 80,
                        username TEXT DEFAULT '',
                        password TEXT DEFAULT '',
                        
                        -- 设备信息
                        mac TEXT DEFAULT '',
                        manufacturer TEXT DEFAULT '',
                        model TEXT DEFAULT '',
                        firmware_version TEXT DEFAULT '',
                        serial_number TEXT DEFAULT '',
                        hardware_id TEXT DEFAULT '',
                        
                        -- PTZ能力
                        support_move INTEGER DEFAULT 0,
                        support_zoom INTEGER DEFAULT 0,
                        ptz_address TEXT DEFAULT '',
                        
                        -- 音频能力
                        support_audio_talk INTEGER DEFAULT 0,
                        
                        -- 接入协议和AI检测
                        protocol TEXT DEFAULT 'rtsp',
                        ai_detection INTEGER DEFAULT 1,
                        
                        -- 流媒体配置
                        rtmp_stream TEXT DEFAULT '',
                        rtsp_stream TEXT DEFAULT '',
                        http_stream TEXT DEFAULT '',
                        ws_stream TEXT DEFAULT '',
                        rtc_stream TEXT DEFAULT '',
                        rtcs_stream TEXT DEFAULT '',
                        enable_forward INTEGER DEFAULT 0,
                        
                        -- 状态
                        status TEXT DEFAULT 'offline',
                        connection_status TEXT DEFAULT '',
                        
                        -- 目录分组
                        directory_id INTEGER,
                        
                        -- 物联网坐标
                        iot_product_key TEXT DEFAULT '',
                        iot_device_name TEXT DEFAULT '',

                        -- 其他
                        description TEXT DEFAULT '',
                        enabled INTEGER DEFAULT 1,
                        
                        -- 时间戳
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
            # ========================================
            # 摄像机目录表
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS camera_directories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    parent_id INTEGER,
                    description TEXT DEFAULT '',
                    sort_order INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (parent_id) REFERENCES camera_directories(id)
                )
            ''')
            
            # ========================================
            # 巡航路径表
            # ========================================
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS patrol_paths (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    presets TEXT DEFAULT '',  -- JSON格式存储预置点列表
                    durations TEXT DEFAULT '',  -- JSON格式存储停留时间列表
                    description TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (camera_id) REFERENCES cameras(id)
                )
            ''')
            
            # ========================================
            # 创建索引
            # ========================================
            # 检查表结构是否包含必要的字段
            cursor.execute("PRAGMA table_info(cameras)")
            columns = [row[1] for row in cursor.fetchall()]
            
            # 只为存在的字段创建索引
            if 'ip' in columns:
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_cameras_ip ON cameras(ip)')
            if 'status' in columns:
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_cameras_status ON cameras(status)')
            if 'enabled' in columns:
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_cameras_enabled ON cameras(enabled)')
            if 'directory_id' in columns:
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_cameras_directory_id ON cameras(directory_id)')
            
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_camera_directories_parent_id ON camera_directories(parent_id)')
            
            # 数据库迁移：添加 ws_stream 字段
            if 'ws_stream' not in columns:
                cursor.execute('ALTER TABLE cameras ADD COLUMN ws_stream TEXT DEFAULT ""')
                logger.info("[CameraManager] 数据库迁移：添加 ws_stream 字段")
            
            # 数据库迁移：添加 rtc_stream 和 rtcs_stream 字段
            if 'rtc_stream' not in columns:
                cursor.execute('ALTER TABLE cameras ADD COLUMN rtc_stream TEXT DEFAULT ""')
                logger.info("[CameraManager] 数据库迁移：添加 rtc_stream 字段")
            
            if 'rtcs_stream' not in columns:
                cursor.execute('ALTER TABLE cameras ADD COLUMN rtcs_stream TEXT DEFAULT ""')
                logger.info("[CameraManager] 数据库迁移：添加 rtcs_stream 字段")
            
            # 数据库迁移：添加 source 字段
            if 'source' not in columns:
                cursor.execute('ALTER TABLE cameras ADD COLUMN source TEXT DEFAULT ""')
                logger.info("[CameraManager] 数据库迁移：添加 source 字段")
            
            # 创建默认目录
            self._init_default_directory(cursor)
            
            conn.commit()
            logger.info(f"[CameraManager] 数据库初始化完成: {db_path}")
    
    def _init_default_directory(self, cursor):
        """初始化默认目录"""
        cursor.execute('''
            INSERT OR IGNORE INTO camera_directories (id, name, parent_id, description, sort_order)
            VALUES (1, '默认分组', NULL, '未分组的摄像机', -1000)
        ''')
    
    # ========================================
    # 摄像机管理
    # ========================================
    
    def add_camera(self, camera: Camera) -> str:
        """添加摄像机
        
        Args:
            camera: 摄像机对象
        
        Returns:
            摄像机 ID
        """
        if not camera.id:
            camera.id = str(uuid.uuid4())
        
        now = datetime.now()
        camera.created_at = now
        camera.updated_at = now
        
        # 如果没有指定目录，使用默认目录
        if camera.directory_id is None:
            camera.directory_id = 1
        
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO cameras (
                    id, name, source, ip, port, username, password,
                    mac, manufacturer, model, firmware_version, serial_number, hardware_id,
                    support_move, support_zoom, ptz_address, support_audio_talk,
                    protocol, ai_detection,
                    rtmp_stream, rtsp_stream, http_stream, ws_stream, enable_forward,
                    status, connection_status, directory_id, description, enabled, created_at, updated_at,
                    rtc_stream, rtcs_stream,
                    iot_product_key, iot_device_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                camera.id, camera.name, camera.source, camera.ip, camera.port, camera.username, camera.password,
                camera.mac, camera.manufacturer, camera.model, camera.firmware_version, camera.serial_number, camera.hardware_id,
                camera.support_move, camera.support_zoom, camera.ptz_address, camera.support_audio_talk,
                camera.protocol, camera.ai_detection,
                camera.rtmp_stream, camera.rtsp_stream, camera.http_stream, camera.ws_stream, camera.enable_forward,
                camera.status, camera.connection_status, camera.directory_id, camera.description, camera.enabled, now.isoformat(), now.isoformat(),
                camera.rtc_stream, camera.rtcs_stream,
                camera.iot_product_key, camera.iot_device_name
            ))
            
            conn.commit()
            logger.info(f"[CameraManager] 添加摄像机成功: ID={camera.id}, name={camera.name}")
            return camera.id
    
    def get_camera(self, camera_id: str) -> Optional[Camera]:
        """获取单个摄像机"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM cameras WHERE id = ?', (camera_id,))
            row = cursor.fetchone()
            
            if row:
                return self._row_to_camera(row, self._columns(cursor))
            return None
    
    def get_camera_by_ip(self, ip: str) -> Optional[Camera]:
        """根据IP获取摄像机"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM cameras WHERE ip = ?', (ip,))
            row = cursor.fetchone()
            
            if row:
                return self._row_to_camera(row, self._columns(cursor))
            return None
    
    def get_camera_by_mac(self, mac: str) -> Optional[Camera]:
        """根据MAC地址获取摄像机"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM cameras WHERE mac = ?', (mac,))
            row = cursor.fetchone()
            
            if row:
                return self._row_to_camera(row, self._columns(cursor))
            return None
    
    def get_all_cameras(self) -> List[Camera]:
        """获取所有摄像机"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM cameras ORDER BY created_at DESC')
            rows = cursor.fetchall()
            columns = self._columns(cursor)
            
            return [self._row_to_camera(row, columns) for row in rows]
    
    def get_cameras_by_directory(self, directory_id: int) -> List[Camera]:
        """获取指定目录下的摄像机"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM cameras WHERE directory_id = ? ORDER BY name', (directory_id,))
            rows = cursor.fetchall()
            columns = self._columns(cursor)
            
            return [self._row_to_camera(row, columns) for row in rows]
    
    def update_camera(self, camera_id: str, update_data: Dict) -> bool:
        """更新摄像机
        
        Args:
            camera_id: 摄像机ID
            update_data: 更新数据字典
        
        Returns:
            是否成功
        """
        if not update_data:
            return False
        
        update_data['updated_at'] = datetime.now().isoformat()
        
        # 构建更新SQL
        fields = []
        values = []
        for key, value in update_data.items():
            if key == 'id' or key == 'created_at':
                continue
            fields.append(f'{key} = ?')
            values.append(value)
        
        values.append(camera_id)
        
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute(f'UPDATE cameras SET {", ".join(fields)} WHERE id = ?', values)
            conn.commit()
            
            logger.info(f"[CameraManager] 更新摄像机成功: ID={camera_id}")
            
            return True
    
    def delete_camera(self, camera_id: str) -> bool:
        """删除摄像机"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM cameras WHERE id = ?', (camera_id,))
            conn.commit()
            
            logger.info(f"[CameraManager] 删除摄像机成功: ID={camera_id}")
            return True
    
    @staticmethod
    def _columns(cursor) -> List[str]:
        """取本次查询结果的列名列表（供 _row_to_camera 按列名解析）"""
        return [col[0] for col in cursor.description]

    def _row_to_camera(self, row, columns=None) -> Camera:
        """数据库行转换为Camera对象

        按列名取值，不依赖列顺序：cameras 表的物理列顺序会随历史
        ALTER TABLE 追加列而改变（如 iot_product_key / iot_device_name
        在早期创建的库中位于表末尾），按下标取值会导致整段字段错位。
        columns 为 cursor.description 中的列名列表。
        """
        data = dict(zip(columns, row)) if columns else {}

        def val(name, default=None):
            """按列名取值，列缺失或值为 NULL 时返回默认值"""
            value = data.get(name)
            return default if value is None else value

        def parse_datetime(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except Exception:
                return None

        return Camera(
            id=val('id', ''),
            name=val('name', ''),
            source=val('source', ''),
            ip=val('ip', ''),
            port=val('port', 80),
            username=val('username', ''),
            password=val('password', ''),
            mac=val('mac', ''),
            manufacturer=val('manufacturer', ''),
            model=val('model', ''),
            firmware_version=val('firmware_version', ''),
            serial_number=val('serial_number', ''),
            hardware_id=val('hardware_id', ''),
            support_move=bool(val('support_move', False)),
            support_zoom=bool(val('support_zoom', False)),
            ptz_address=val('ptz_address', ''),
            support_audio_talk=bool(val('support_audio_talk', False)),
            protocol=val('protocol', 'rtsp'),
            ai_detection=bool(val('ai_detection', True)),
            rtmp_stream=val('rtmp_stream', ''),
            rtsp_stream=val('rtsp_stream', ''),
            http_stream=val('http_stream', ''),
            ws_stream=val('ws_stream', ''),
            rtc_stream=val('rtc_stream', ''),
            rtcs_stream=val('rtcs_stream', ''),
            enable_forward=bool(val('enable_forward', False)),
            status=val('status', 'offline'),
            connection_status=val('connection_status', ''),
            directory_id=val('directory_id'),
            iot_product_key=val('iot_product_key', ''),
            iot_device_name=val('iot_device_name', ''),
            description=val('description', ''),
            enabled=bool(val('enabled', True)),
            created_at=parse_datetime(val('created_at')),
            updated_at=parse_datetime(val('updated_at'))
        )
    
    # ========================================
    # 摄像机-设备通道池关联管理
    # ========================================
    
    def bind_camera_to_channel(self, camera_id: str, channel_id: int) -> int:
        """将摄像机绑定到设备通道池
        
        Args:
            camera_id: 摄像机 ID
            channel_id: 设备通道池 ID
        
        Returns:
            绑定关系 ID，-1 表示已存在
        """
        # 数据库路径（tasks.db）
        tasks_db_path = os.path.join('data', 'tasks.db')
        
        with sqlite3.connect(tasks_db_path) as conn:
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT INTO camera_device_channel_bindings 
                    (camera_id, channel_id, enabled, created_at)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ''', (camera_id, channel_id))
                
                binding_id = cursor.lastrowid
                conn.commit()
                
                logger.info(f"[CameraManager] 绑定摄像机到设备通道成功: camera_id={camera_id}, channel_id={channel_id}")
                return binding_id
                
            except sqlite3.IntegrityError:
                logger.warning(f"[CameraManager] 绑定关系已存在: camera_id={camera_id}, channel_id={channel_id}")
                return -1
    
    def unbind_camera_from_channel(self, camera_id: str, channel_id: int) -> bool:
        """解除摄像机与设备通道池的绑定
        
        Args:
            camera_id: 摄像机 ID
            channel_id: 设备通道池 ID
        
        Returns:
            是否成功
        """
        tasks_db_path = os.path.join('data', 'tasks.db')
        
        with sqlite3.connect(tasks_db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM camera_device_channel_bindings 
                WHERE camera_id = ? AND channel_id = ?
            ''', (camera_id, channel_id))
            
            conn.commit()
            
            success = cursor.rowcount > 0
            logger.info(f"[CameraManager] 解除摄像机绑定成功: camera_id={camera_id}, channel_id={channel_id}")
            return success
    
    def get_camera_channels(self, camera_id: str) -> List[Dict]:
        """获取摄像机关联的所有设备通道池
        
        Args:
            camera_id: 摄像机 ID
        
        Returns:
            设备通道池列表
        """
        tasks_db_path = os.path.join('data', 'tasks.db')
        
        with sqlite3.connect(tasks_db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT dc.id, dc.channel_name, dc.rtsp_url, dc.channel_source, dc.enabled, dc.created_at, dc.updated_at
                FROM device_channel_pool dc
                INNER JOIN camera_device_channel_bindings cb ON dc.id = cb.channel_id
                WHERE cb.camera_id = ? AND cb.enabled = 1
                ORDER BY cb.created_at DESC
            ''', (camera_id,))
            
            rows = cursor.fetchall()
            channels = [
                {
                    'id': row[0],
                    'channel_name': row[1],
                    'rtsp_url': row[2],
                    'channel_source': row[3],
                    'enabled': bool(row[4]),
                    'created_at': row[5],
                    'updated_at': row[6]
                }
                for row in rows
            ]
            
            logger.info(f"[CameraManager] 获取摄像机设备通道: camera_id={camera_id}, 共{len(channels)}个通道")
            return channels
    
    def get_channel_cameras(self, channel_id: int) -> List[Dict]:
        """获取设备通道池关联的所有摄像机
        
        Args:
            channel_id: 设备通道池 ID
        
        Returns:
            摄像机列表
        """
        tasks_db_path = os.path.join('data', 'tasks.db')
        
        with sqlite3.connect(tasks_db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT c.id, c.name, c.ip, c.rtsp_url, c.protocol, c.enabled, c.created_at, c.updated_at
                FROM cameras c
                INNER JOIN camera_device_channel_bindings cb ON c.id = cb.camera_id
                WHERE cb.channel_id = ? AND cb.enabled = 1
                ORDER BY cb.created_at DESC
            ''', (channel_id,))
            
            rows = cursor.fetchall()
            cameras = [
                {
                    'id': row[0],
                    'name': row[1],
                    'ip': row[2],
                    'rtsp_url': row[3],
                    'protocol': row[4],
                    'enabled': bool(row[5]),
                    'created_at': row[6],
                    'updated_at': row[7]
                }
                for row in rows
            ]
            
            logger.info(f"[CameraManager] 获取设备通道摄像机: channel_id={channel_id}, 共{len(cameras)}个摄像机")
            return cameras
    
    def is_camera_bound_to_channel(self, camera_id: str, channel_id: int) -> bool:
        """检查摄像机是否已绑定到设备通道池
        
        Args:
            camera_id: 摄像机 ID
            channel_id: 设备通道池 ID
        
        Returns:
            是否已绑定
        """
        tasks_db_path = os.path.join('data', 'tasks.db')
        
        with sqlite3.connect(tasks_db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT COUNT(*) FROM camera_device_channel_bindings 
                WHERE camera_id = ? AND channel_id = ? AND enabled = 1
            ''', (camera_id, channel_id))
            
            count = cursor.fetchone()[0]
            return count > 0
    
    # ========================================
    # 目录管理
    # ========================================
    
    def add_directory(self, directory: CameraDirectory) -> int:
        """添加目录"""
        now = datetime.now()
        directory.created_at = now
        directory.updated_at = now
        
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO camera_directories (name, parent_id, description, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (directory.name, directory.parent_id, directory.description, directory.sort_order, now.isoformat(), now.isoformat()))
            conn.commit()
            
            return cursor.lastrowid
    
    def get_all_directories(self) -> List[CameraDirectory]:
        """获取所有目录"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM camera_directories ORDER BY sort_order, name')
            rows = cursor.fetchall()
            
            return [self._row_to_directory(row) for row in rows]
    
    def _row_to_directory(self, row) -> CameraDirectory:
        """数据库行转换为CameraDirectory对象"""
        def parse_datetime(value):
            if value is None or value == '':
                return None
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except:
                return None
        
        return CameraDirectory(
            id=row[0],
            name=row[1] or '',
            parent_id=row[2],
            description=row[3] or '',
            sort_order=row[4] or 0,
            created_at=parse_datetime(row[5]),
            updated_at=parse_datetime(row[6])
        )
    
    # ========================================
    # 统计
    # ========================================
    
    def get_statistics(self) -> Dict:
        """获取摄像机统计信息"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM cameras')
            total = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM cameras WHERE status = "online"')
            online = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM cameras WHERE status = "offline"')
            offline = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM cameras WHERE enabled = 1')
            enabled = cursor.fetchone()[0]
            
            return {
                'total': total,
                'online': online,
                'offline': offline,
                'enabled': enabled
            }
    
    # ========================================
    # 巡航路径管理
    # ========================================
    
    def add_patrol_path(self, patrol: PatrolPath) -> int:
        """添加巡航路径"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO patrol_paths (camera_id, name, presets, durations, description, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ''', (patrol.camera_id, patrol.name, patrol.presets, patrol.durations, patrol.description, patrol.enabled))
            
            patrol_id = cursor.lastrowid
            conn.commit()
            
            logger.info(f"[CameraManager] 添加巡航路径成功: ID={patrol_id}, camera_id={patrol.camera_id}")
            return patrol_id
    
    def get_patrol_paths(self, camera_id: str) -> List[PatrolPath]:
        """获取指定摄像机的所有巡航路径"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM patrol_paths WHERE camera_id = ? AND enabled = 1 ORDER BY created_at', (camera_id,))
            rows = cursor.fetchall()
            
            return [self._row_to_patrol_path(row) for row in rows]
    
    def get_patrol_path(self, patrol_id: int) -> Optional[PatrolPath]:
        """获取单个巡航路径"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM patrol_paths WHERE id = ?', (patrol_id,))
            row = cursor.fetchone()
            
            if row:
                return self._row_to_patrol_path(row)
            return None
    
    def update_patrol_path(self, patrol_id: int, updates: Dict) -> bool:
        """更新巡航路径"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            # 构建更新语句
            update_fields = []
            update_values = []
            
            for key, value in updates.items():
                if key in ['name', 'presets', 'durations', 'description', 'enabled']:
                    update_fields.append(f"{key} = ?")
                    update_values.append(value)
            
            if update_fields:
                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                update_values.append(patrol_id)
                
                sql = f"UPDATE patrol_paths SET {', '.join(update_fields)} WHERE id = ?"
                cursor.execute(sql, update_values)
                conn.commit()
                
                logger.info(f"[CameraManager] 更新巡航路径成功: ID={patrol_id}")
                return True
            
            return False
    
    def delete_patrol_path(self, patrol_id: int) -> bool:
        """删除巡航路径"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM patrol_paths WHERE id = ?', (patrol_id,))
            conn.commit()
            
            logger.info(f"[CameraManager] 删除巡航路径成功: ID={patrol_id}")
            return True
    
    def _row_to_patrol_path(self, row) -> PatrolPath:
        """数据库行转换为PatrolPath对象"""
        def parse_datetime(value):
            if value is None or value == '':
                return None
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except:
                return None
        
        return PatrolPath(
            id=row[0],
            camera_id=row[1] or '',
            name=row[2] or '',
            presets=row[3] or '',
            durations=row[4] or '',
            description=row[5] or '',
            enabled=bool(row[6]),
            created_at=parse_datetime(row[7]),
            updated_at=parse_datetime(row[8])
        )


# ========================================
# 全局单例
# ========================================

_camera_manager: Optional[CameraManager] = None


def get_camera_manager() -> CameraManager:
    """获取摄像机管理器单例"""
    global _camera_manager
    if _camera_manager is None:
        _camera_manager = CameraManager()
    return _camera_manager