"""
目录管理服务

提供摄像机目录分组的管理功能。
"""

import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

import sqlite3

logger = logging.getLogger(__name__)


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
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'parent_id': self.parent_id,
            'description': self.description,
            'sort_order': self.sort_order,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class DirectoryService:
    """目录管理服务"""
    
    def __init__(self, db_path: str = 'data/tasks.db'):
        """
        初始化目录管理服务
        
        Args:
            db_path: 数据库文件路径
        """
        self._db_path = db_path
        self._init_database()
    
    def _get_db_path(self) -> str:
        """获取数据库路径"""
        return self._db_path
    
    def _init_database(self):
        """初始化数据库表"""
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            # 创建目录表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS camera_directories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    parent_id INTEGER,
                    description TEXT,
                    sort_order INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY (parent_id) REFERENCES camera_directories(id)
                )
            ''')
            
            # 检查是否有默认目录
            cursor.execute('SELECT COUNT(*) FROM camera_directories')
            count = cursor.fetchone()[0]
            
            if count == 0:
                # 创建默认目录
                now = datetime.now().isoformat()
                cursor.execute('''
                    INSERT INTO camera_directories (name, parent_id, description, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', ('默认目录', None, '系统默认目录', 0, now, now))
                logger.info("创建默认目录")
            
            conn.commit()
    
    # ========================================
    # 目录管理
    # ========================================
    
    def create_directory(self, directory: CameraDirectory) -> int:
        """
        创建目录
        
        Args:
            directory: 目录对象
        
        Returns:
            创建的目录ID
        """
        now = datetime.now()
        directory.created_at = now
        directory.updated_at = now
        
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO camera_directories (name, parent_id, description, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (directory.name, directory.parent_id, directory.description, directory.sort_order, 
                  now.isoformat(), now.isoformat()))
            conn.commit()
            
            directory_id = cursor.lastrowid
            logger.info(f"创建目录成功: id={directory_id}, name={directory.name}")
            
            return directory_id
    
    def get_directory(self, directory_id: int) -> Optional[CameraDirectory]:
        """
        获取目录
        
        Args:
            directory_id: 目录ID
        
        Returns:
            目录对象或None
        """
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM camera_directories WHERE id = ?', (directory_id,))
            row = cursor.fetchone()
            
            if row:
                return self._row_to_directory(row)
            return None
    
    def get_all_directories(self) -> List[CameraDirectory]:
        """
        获取所有目录
        
        Returns:
            目录列表
        """
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM camera_directories ORDER BY sort_order, name')
            rows = cursor.fetchall()
            
            return [self._row_to_directory(row) for row in rows]
    
    def get_directories_by_parent(self, parent_id: Optional[int] = None) -> List[CameraDirectory]:
        """
        获取指定父目录下的子目录
        
        Args:
            parent_id: 父目录ID，None表示获取根目录
        
        Returns:
            目录列表
        """
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            if parent_id is None:
                cursor.execute('SELECT * FROM camera_directories WHERE parent_id IS NULL ORDER BY sort_order, name')
            else:
                cursor.execute('SELECT * FROM camera_directories WHERE parent_id = ? ORDER BY sort_order, name', (parent_id,))
            rows = cursor.fetchall()
            
            return [self._row_to_directory(row) for row in rows]
    
    def update_directory(self, directory_id: int, data: Dict[str, Any]) -> bool:
        """
        更新目录
        
        Args:
            directory_id: 目录ID
            data: 更新数据
        
        Returns:
            是否成功
        """
        now = datetime.now()
        
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            # 构建更新SQL
            update_fields = []
            update_values = []
            
            if 'name' in data:
                update_fields.append('name = ?')
                update_values.append(data['name'])
            
            if 'parent_id' in data:
                update_fields.append('parent_id = ?')
                update_values.append(data['parent_id'])
            
            if 'description' in data:
                update_fields.append('description = ?')
                update_values.append(data['description'])
            
            if 'sort_order' in data:
                update_fields.append('sort_order = ?')
                update_values.append(data['sort_order'])
            
            update_fields.append('updated_at = ?')
            update_values.append(now.isoformat())
            
            update_values.append(directory_id)
            
            sql = f"UPDATE camera_directories SET {', '.join(update_fields)} WHERE id = ?"
            cursor.execute(sql, update_values)
            conn.commit()
            
            success = cursor.rowcount > 0
            if success:
                logger.info(f"更新目录成功: id={directory_id}")
            else:
                logger.warning(f"更新目录失败: id={directory_id}")
            
            return success
    
    def delete_directory(self, directory_id: int) -> bool:
        """
        删除目录
        
        Args:
            directory_id: 目录ID
        
        Returns:
            是否成功
        """
        # 不允许删除默认目录（ID=1）
        if directory_id == 1:
            logger.warning("不允许删除默认目录")
            return False
        
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            # 检查是否有子目录
            cursor.execute('SELECT COUNT(*) FROM camera_directories WHERE parent_id = ?', (directory_id,))
            child_count = cursor.fetchone()[0]
            
            if child_count > 0:
                logger.warning(f"目录有子目录，无法删除: id={directory_id}")
                return False
            
            # 将该目录下的摄像机移动到默认目录
            cursor.execute('UPDATE cameras SET directory_id = 1 WHERE directory_id = ?', (directory_id,))
            
            # 删除目录
            cursor.execute('DELETE FROM camera_directories WHERE id = ?', (directory_id,))
            conn.commit()
            
            success = cursor.rowcount > 0
            if success:
                logger.info(f"删除目录成功: id={directory_id}")
            else:
                logger.warning(f"删除目录失败: id={directory_id}")
            
            return success
    
    def get_directory_tree(self) -> List[Dict[str, Any]]:
        """
        获取目录树结构
        
        Returns:
            目录树列表
        """
        directories = self.get_all_directories()
        
        # 构建树结构
        def build_tree(parent_id: Optional[int] = None) -> List[Dict[str, Any]]:
            children = [d for d in directories if d.parent_id == parent_id]
            tree = []
            for child in children:
                node = child.to_dict()
                node['children'] = build_tree(child.id)
                tree.append(node)
            return tree
        
        return build_tree(None)
    
    def get_camera_count_by_directory(self, directory_id: int) -> int:
        """
        获取目录下的摄像机数量（包含所有子目录）
        
        Args:
            directory_id: 目录ID
        
        Returns:
            摄像机数量
        """
        with sqlite3.connect(self._get_db_path()) as conn:
            cursor = conn.cursor()
            
            # 递归获取所有子目录ID
            def get_all_child_ids(parent_id: int) -> List[int]:
                cursor.execute('SELECT id FROM camera_directories WHERE parent_id = ?', (parent_id,))
                child_ids = [row[0] for row in cursor.fetchall()]
                all_ids = list(child_ids)
                for cid in child_ids:
                    all_ids.extend(get_all_child_ids(cid))
                return all_ids
            
            # 获取当前目录及所有子目录的摄像机数量
            all_ids = [directory_id] + get_all_child_ids(directory_id)
            placeholders = ','.join('?' for _ in all_ids)
            cursor.execute(f'SELECT COUNT(*) FROM cameras WHERE directory_id IN ({placeholders})', all_ids)
            return cursor.fetchone()[0]
    
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
# 单例模式
# ========================================

_directory_service: Optional[DirectoryService] = None


def get_directory_service() -> DirectoryService:
    """获取目录管理服务实例"""
    global _directory_service
    
    if _directory_service is None:
        _directory_service = DirectoryService()
    
    return _directory_service