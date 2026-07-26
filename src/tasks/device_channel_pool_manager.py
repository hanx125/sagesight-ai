"""
设备通道检测池管理器

管理设备通道检测池的数据库存储、查询和状态管理。
"""

import sqlite3
import os
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


def parse_datetime(value):
    """解析日期时间字符串"""
    if value is None or value == '':
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


# ========================================
# 数据类定义
# ========================================

@dataclass
class DeviceChannelPool:
    """设备通道检测池数据类"""
    id: Optional[int] = None
    channel_name: str = ""
    rtsp_url: str = ""
    channel_source: str = "local_platform"  # local_platform（本地平台）, edge_platform（边缘平台）
    camera_id: Optional[str] = None  # 关联的摄像机 ID（从 camera_device_channel_bindings 表查询）
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'id': self.id,
            'channel_name': self.channel_name,
            'rtsp_url': self.rtsp_url,
            'channel_source': self.channel_source,
            'camera_id': self.camera_id,
            'enabled': self.enabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'DeviceChannelPool':
        """从字典创建"""
        return cls(
            id=data.get('id'),
            channel_name=data.get('channel_name', ''),
            rtsp_url=data.get('rtsp_url', ''),
            channel_source=data.get('channel_source', 'local_platform'),
            enabled=data.get('enabled', True),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else None,
            updated_at=datetime.fromisoformat(data['updated_at']) if data.get('updated_at') else None
        )


# ========================================
# 设备通道检测池管理器
# ========================================

class DeviceChannelPoolManager:
    """设备通道检测池管理器 - 管理设备通道的数据库操作"""
    
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
        
        # 数据库路径
        self.db_path = os.path.join('data', 'tasks.db')
        
        # 初始化数据库
        self._init_database()
        
        logger.info(f"[DeviceChannelPoolManager] 初始化完成: db_path={self.db_path}")
    
    def _init_database(self):
        """初始化数据库"""
        # 确保data目录存在
        os.makedirs('data', exist_ok=True)

        # 注意：device_channel_pool 和 task_device_channel_bindings 表已在 TaskManager 中创建
        # 这里不需要重复创建，只需确保数据目录存在
        logger.info(f"[DeviceChannelPoolManager] 数据库初始化完成: db_path={self.db_path}")
    
    # ========================================
    # 设备通道检测池管理方法
    # ========================================
    
    def add_device_channel(self, channel: DeviceChannelPool) -> int:
        """添加设备通道
        
        Args:
            channel: 设备通道对象
        
        Returns:
            设备通道 ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO device_channel_pool 
                (channel_name, rtsp_url, channel_source, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ''', (
                channel.channel_name,
                channel.rtsp_url,
                channel.channel_source,
                channel.enabled
            ))
            
            channel_id = cursor.lastrowid
            conn.commit()
            
            logger.info(f"[DeviceChannelPoolManager] 添加设备通道成功: ID={channel_id}, name={channel.channel_name}")
            return channel_id
    
    def get_device_channel(self, channel_id: int) -> Optional[DeviceChannelPool]:
        """获取单个设备通道
        
        Args:
            channel_id: 设备通道 ID
        
        Returns:
            设备通道对象，不存在则返回 None
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM device_channel_pool WHERE id = ?', (channel_id,))
            row = cursor.fetchone()
            
            if row:
                channel = self._row_to_channel(row)
                return channel
            return None
    
    def get_all_device_channels(self, enabled_only: bool = False, channel_source: str = None) -> List[DeviceChannelPool]:
        """获取所有设备通道
        
        Args:
            enabled_only: 只获取启用的设备通道
            channel_source: 按通道来源筛选
        
        Returns:
            设备通道列表
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            query = 'SELECT * FROM device_channel_pool WHERE 1=1'
            params = []
            
            if enabled_only:
                query += ' AND enabled = 1'
            if channel_source:
                query += ' AND channel_source = ?'
                params.append(channel_source)
            
            query += ' ORDER BY created_at DESC'
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            channels = [self._row_to_channel(row) for row in rows]
            logger.info(f"[DeviceChannelPoolManager] 获取设备通道列表: 共{len(channels)}个通道")
            return channels
    
    def update_device_channel(self, channel_id: int, update_data: Dict) -> bool:
        """更新设备通道
        
        Args:
            channel_id: 设备通道 ID
            update_data: 更新数据字典
        
        Returns:
            是否成功
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 构建更新SQL
            fields = []
            values = []
            
            for key, value in update_data.items():
                if key in ['channel_name', 'rtsp_url', 'channel_source', 'enabled']:
                    fields.append(f'{key} = ?')
                    values.append(value)
            
            if not fields:
                logger.warning(f"[DeviceChannelPoolManager] 更新设备通道失败: 无有效字段")
                return False
            
            fields.append('updated_at = CURRENT_TIMESTAMP')
            values.append(channel_id)
            
            sql = f"UPDATE device_channel_pool SET {', '.join(fields)} WHERE id = ?"
            cursor.execute(sql, values)
            conn.commit()
            
            success = cursor.rowcount > 0
            logger.info(f"[DeviceChannelPoolManager] 更新设备通道成功: ID={channel_id}")
            return success
    
    def delete_device_channel(self, channel_id: int) -> bool:
        """删除设备通道
        
        Args:
            channel_id: 设备通道 ID
        
        Returns:
            是否成功
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 先删除任务关联关系
            cursor.execute('DELETE FROM task_device_channel_bindings WHERE channel_id = ?', (channel_id,))
            
            # 再删除摄像机关联关系
            cursor.execute('DELETE FROM camera_device_channel_bindings WHERE channel_id = ?', (channel_id,))
            
            # 删除设备通道
            cursor.execute('DELETE FROM device_channel_pool WHERE id = ?', (channel_id,))
            conn.commit()
            
            success = cursor.rowcount > 0
            logger.info(f"[DeviceChannelPoolManager] 删除设备通道成功: ID={channel_id}")
            return success
    
    def _row_to_channel(self, row: tuple) -> DeviceChannelPool:
        """将数据库行转换为 DeviceChannelPool 对象
        
        字段顺序：
        0-6: device_channel_pool 表字段
        7: camera_id（LEFT JOIN 查询结果，可能为 None）
        """
        return DeviceChannelPool(
            id=row[0],
            channel_name=row[1] or '',
            rtsp_url=row[2] or '',
            channel_source=row[3] if len(row) > 3 else 'local_platform',
            enabled=bool(row[4]) if len(row) > 4 else True,
            created_at=parse_datetime(row[5] if len(row) > 5 else None),
            updated_at=parse_datetime(row[6] if len(row) > 6 else None),
            camera_id=row[7] if len(row) > 7 and row[7] is not None else None
        )
    
    # ========================================
    # 任务-设备通道检测池关联管理
    # ========================================
    
    def bind_device_channel_to_task(self, task_id: int, channel_id: int) -> int:
        """将设备通道绑定到任务
        
        Args:
            task_id: 任务 ID
            channel_id: 设备通道 ID
        
        Returns:
            绑定关系 ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT INTO task_device_channel_bindings 
                    (task_id, channel_id, enabled, status, created_at)
                    VALUES (?, ?, 1, 'stopped', CURRENT_TIMESTAMP)
                ''', (task_id, channel_id))
                
                binding_id = cursor.lastrowid
                conn.commit()
                
                logger.info(f"[DeviceChannelPoolManager] 绑定设备通道到任务成功: task_id={task_id}, channel_id={channel_id}")
                return binding_id
                
            except sqlite3.IntegrityError:
                logger.warning(f"[DeviceChannelPoolManager] 绑定关系已存在: task_id={task_id}, channel_id={channel_id}")
                return -1
    
    def unbind_device_channel_from_task(self, task_id: int, channel_id: int) -> bool:
        """解除设备通道与任务的绑定
        
        Args:
            task_id: 任务 ID
            channel_id: 设备通道 ID
        
        Returns:
            是否成功
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM task_device_channel_bindings 
                WHERE task_id = ? AND channel_id = ?
            ''', (task_id, channel_id))
            
            conn.commit()
            
            success = cursor.rowcount > 0
            logger.info(f"[DeviceChannelPoolManager] 解除设备通道绑定成功: task_id={task_id}, channel_id={channel_id}")
            return success
    
    def get_task_device_channels(self, task_id: int) -> List[DeviceChannelPool]:
        """获取任务关联的所有设备通道（包含关联的摄像机 ID）
        
        Args:
            task_id: 任务 ID
        
        Returns:
            设备通道列表
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # JOIN camera_device_channel_bindings 获取关联的摄像机 ID
            cursor.execute('''
                SELECT dc.*, ccb.camera_id 
                FROM device_channel_pool dc
                INNER JOIN task_device_channel_bindings tcb ON dc.id = tcb.channel_id
                LEFT JOIN camera_device_channel_bindings ccb ON dc.id = ccb.channel_id AND ccb.enabled = 1
                WHERE tcb.task_id = ? AND tcb.enabled = 1
            ''', (task_id,))
            
            rows = cursor.fetchall()
            channels = [self._row_to_channel(row) for row in rows]
            
            # ✅ 日志已注释（避免频繁输出）
            # logger.info(f"[DeviceChannelPoolManager] 获取任务设备通道: task_id={task_id}, 共{len(channels)}个通道")
            
            return channels
    
    def get_device_channel_tasks(self, channel_id: int) -> List[int]:
        """获取设备通道绑定的所有任务ID
        
        Args:
            channel_id: 设备通道 ID
        
        Returns:
            任务ID列表
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT task_id FROM task_device_channel_bindings 
                WHERE channel_id = ? AND enabled = 1
            ''', (channel_id,))
            
            task_ids = [row[0] for row in cursor.fetchall()]
            
            logger.info(f"[DeviceChannelPoolManager] 获取设备通道任务: channel_id={channel_id}, 共{len(task_ids)}个任务")
            return task_ids
    
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
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT INTO camera_device_channel_bindings 
                    (camera_id, channel_id, enabled, created_at)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ''', (camera_id, channel_id))
                
                binding_id = cursor.lastrowid
                conn.commit()
                
                logger.info(f"[DeviceChannelPoolManager] 绑定摄像机到设备通道成功: camera_id={camera_id}, channel_id={channel_id}")
                return binding_id
                
            except sqlite3.IntegrityError:
                logger.warning(f"[DeviceChannelPoolManager] 绑定关系已存在: camera_id={camera_id}, channel_id={channel_id}")
                return -1
    
    def unbind_camera_from_channel(self, camera_id: str, channel_id: int) -> bool:
        """解除摄像机与设备通道池的绑定
        
        Args:
            camera_id: 摄像机 ID
            channel_id: 设备通道池 ID
        
        Returns:
            是否成功
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM camera_device_channel_bindings 
                WHERE camera_id = ? AND channel_id = ?
            ''', (camera_id, channel_id))
            
            conn.commit()
            
            success = cursor.rowcount > 0
            logger.info(f"[DeviceChannelPoolManager] 解除摄像机绑定成功: camera_id={camera_id}, channel_id={channel_id}")
            return success
    
    def get_channel_cameras(self, channel_id: int) -> List[Dict]:
        """获取设备通道池关联的所有摄像机
        
        Args:
            channel_id: 设备通道池 ID
        
        Returns:
            摄像机列表
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT c.id, c.name, c.ip, c.protocol, c.enabled, c.created_at, c.updated_at
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
                    'protocol': row[3],
                    'enabled': bool(row[4]),
                    'created_at': row[5],
                    'updated_at': row[6]
                }
                for row in rows
            ]
            
            logger.info(f"[DeviceChannelPoolManager] 获取设备通道摄像机: channel_id={channel_id}, 共{len(cameras)}个摄像机")
            return cameras
    
    def is_camera_bound_to_channel(self, camera_id: str, channel_id: int) -> bool:
        """检查摄像机是否已绑定到设备通道池
        
        Args:
            camera_id: 摄像机 ID
            channel_id: 设备通道池 ID
        
        Returns:
            是否已绑定
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT COUNT(*) FROM camera_device_channel_bindings 
                WHERE camera_id = ? AND channel_id = ? AND enabled = 1
            ''', (camera_id, channel_id))
            
            count = cursor.fetchone()[0]
            return count > 0


# ========================================
# 单例实例
# ========================================

_device_channel_pool_manager = None


def get_device_channel_pool_manager() -> DeviceChannelPoolManager:
    """获取设备通道检测池管理器实例"""
    global _device_channel_pool_manager
    if _device_channel_pool_manager is None:
        _device_channel_pool_manager = DeviceChannelPoolManager()
    return _device_channel_pool_manager