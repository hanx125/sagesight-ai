"""
算法管理器

管理算法配置的数据库存储、查询、更新和删除。
"""

import os
import json
import sqlite3
from typing import Dict, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class AlgorithmConfig:
    """算法配置类"""
    
    def __init__(
        self,
        id: int = None,
        name: str = '',
        type: str = 'detection',
        version: str = 'v1.0',
        status: str = 'active',
        description: str = '',
        model_path: str = '',
        model_type: str = 'yolov11n',  # 模型类型（yolov11n/yolov8n 等规格，人脸/车牌用专属值 face_det/plate_detect 以隔离检测进程池）
        model_format: str = 'onnx',
        input_size: str = '640x640',
        labels_file: str = '',  # 标签文件路径
        algorithm_image: str = '',  # 算法展示图片路径
        created_at: str = None,
        **kwargs
    ):
        self.id = id
        self.name = name
        self.type = type
        self.version = version
        self.status = status
        self.description = description
        self.model_path = model_path
        self.model_type = model_type
        self.model_format = model_format
        self.input_size = input_size
        self.labels_file = labels_file
        self.algorithm_image = algorithm_image
        self.created_at = created_at or datetime.now().isoformat()
        
        # 处理额外的字段（如废弃的target_classes字段，会被忽略）
        for key, value in kwargs.items():
            if key not in ['target_classes']:  # 忽略废弃字段
                setattr(self, key, value)

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'version': self.version,
            'status': self.status,
            'description': self.description,
            'model_path': self.model_path,
            'model_type': self.model_type,
            'model_format': self.model_format,
            'input_size': self.input_size,
            'labels_file': self.labels_file,
            'algorithm_image': self.algorithm_image,
            'created_at': self.created_at,
            'updated_at': getattr(self, 'updated_at', None)
        }
    
    def update(self, data: Dict):
        """更新配置"""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)


class AlgorithmManager:
    """算法管理器 - 使用数据库存储"""
    
    def __init__(self, db_path: str = "data/tasks.db"):
        """初始化算法管理器
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        # 确保目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 创建算法配置表（target_classes已删除，改为从labels_file动态获取）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS algorithm_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    type VARCHAR(50) DEFAULT 'detection',
                    version VARCHAR(20) DEFAULT 'v1.0',
                    status VARCHAR(20) DEFAULT 'active',
                    description TEXT,
                    model_path VARCHAR(200),
                    model_type VARCHAR(50) DEFAULT 'yolov11',
                    model_format VARCHAR(20) DEFAULT 'onnx',
                    input_size VARCHAR(20) DEFAULT '640x640',
                    labels_file VARCHAR(200) DEFAULT '',
                    algorithm_image VARCHAR(200) DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 数据库迁移：添加缺失的列
            cursor.execute("PRAGMA table_info(algorithm_configs)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'model_type' not in columns:
                cursor.execute("ALTER TABLE algorithm_configs ADD COLUMN model_type VARCHAR(50) DEFAULT 'yolov11'")
                logger.info("数据库迁移: 添加model_type字段")
            
            if 'labels_file' not in columns:
                cursor.execute("ALTER TABLE algorithm_configs ADD COLUMN labels_file VARCHAR(200) DEFAULT ''")
                logger.info("数据库迁移: 添加labels_file字段")

            if 'algorithm_image' not in columns:
                cursor.execute("ALTER TABLE algorithm_configs ADD COLUMN algorithm_image VARCHAR(200) DEFAULT ''")
                logger.info("数据库迁移: 添加algorithm_image字段")
            
            # target_classes字段已废弃（改为从labels_file动态获取），自动删除
            if 'target_classes' in columns:
                logger.info("数据库迁移: 检测到废弃字段target_classes，启动时会自动忽略")
                # SQLite不支持DROP COLUMN，但查询时会忽略该字段
            
            # 创建索引
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_algo_configs_type ON algorithm_configs(type)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_algo_configs_status ON algorithm_configs(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_algo_configs_name ON algorithm_configs(name)')
            
            conn.commit()
            
            # ✅ 移除自动初始化默认算法的逻辑，改为用户手动管理
            
            logger.info(f"算法管理器数据库初始化完成: {self.db_path}")
    
    def _row_to_config(self, row: tuple) -> AlgorithmConfig:
        """将数据库行转换为 AlgorithmConfig 对象"""
        return AlgorithmConfig(
            id=row[0],
            name=row[1],
            type=row[2],
            version=row[3],
            status=row[4],
            description=row[5] or '',
            model_path=row[6] or '',
            model_type=row[7] if len(row) > 7 and row[7] else 'yolov11n',
            model_format=row[8] if len(row) > 8 else 'onnx',
            input_size=row[9] if len(row) > 9 else '640x640',
            labels_file=row[10] if len(row) > 10 and row[10] else '',
            algorithm_image=row[11] if len(row) > 11 and row[11] else '',
            created_at=row[12] if len(row) > 12 else None,
            updated_at=row[13] if len(row) > 13 else None
        )
    
    def create_algorithm(self, data: Dict) -> AlgorithmConfig:
        """创建算法配置
        
        Args:
            data: 算法配置数据
        
        Returns:
            创建的 AlgorithmConfig 对象
        
        Raises:
            ValueError: 如果算法名称已存在
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 检查名称唯一性
            cursor.execute('SELECT id FROM algorithm_configs WHERE name = ?', (data.get('name'),))
            if cursor.fetchone():
                raise ValueError(f"算法名称已存在: {data.get('name')}")
            
            now = datetime.now()
            
            cursor.execute('''
                INSERT INTO algorithm_configs 
                (name, type, version, status, description, model_path, model_type, model_format, 
                 input_size, labels_file, algorithm_image, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('name'),
                data.get('type', 'detection'),
                data.get('version', 'v1.0'),
                data.get('status', 'active'),
                data.get('description', ''),
                data.get('model_path', ''),
                data.get('model_type', 'yolov11n'),
                data.get('model_format', 'onnx'),
                data.get('input_size', '640x640'),
                data.get('labels_file', ''),
                data.get('algorithm_image', ''),
                now,
                now
            ))
            
            conn.commit()
            algorithm_id = cursor.lastrowid
            
            logger.info(f"创建算法配置: {data.get('name')} (ID: {algorithm_id})")
            
            return self.get_algorithm(algorithm_id)
    
    def get_algorithm(self, algorithm_id: int) -> Optional[AlgorithmConfig]:
        """获取单个算法配置
        
        Args:
            algorithm_id: 算法 ID
        
        Returns:
            AlgorithmConfig 对象或 None
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, name, type, version, status, description, model_path,
                       model_type, model_format, input_size, labels_file,
                       algorithm_image, created_at, updated_at
                FROM algorithm_configs
                WHERE id = ?
            ''', (algorithm_id,))
            row = cursor.fetchone()
            
            if row:
                return self._row_to_config(row)
            return None
    
    def get_algorithm_by_name(self, name: str) -> Optional[AlgorithmConfig]:
        """通过名称获取算法配置
        
        Args:
            name: 算法名称
        
        Returns:
            AlgorithmConfig 对象或 None
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 明确指定字段顺序
            cursor.execute('''
                SELECT id, name, type, version, status, description, model_path,
                       model_type, model_format, input_size, labels_file,
                       algorithm_image, created_at, updated_at
                FROM algorithm_configs WHERE name = ?
            ''', (name,))
            row = cursor.fetchone()
            
            if row:
                return self._row_to_config(row)
            return None
    
    def get_all_algorithms(
        self,
        type_filter: Optional[str] = None,
        status_filter: Optional[str] = None
    ) -> List[AlgorithmConfig]:
        """获取所有算法配置
        
        Args:
            type_filter: 类型筛选
            status_filter: 状态筛选
        
        Returns:
            算法配置列表
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            query = '''
                SELECT id, name, type, version, status, description, model_path,
                       model_type, model_format, input_size, labels_file,
                       algorithm_image, created_at, updated_at
                FROM algorithm_configs
            '''
            params = []
            
            # 添加筛选条件（需要先添加WHERE，然后再添加AND）
            conditions = []
            
            if type_filter:
                conditions.append('type = ?')
                params.append(type_filter)
            
            if status_filter:
                conditions.append('status = ?')
                params.append(status_filter)
            
            # 如果有筛选条件，添加WHERE子句
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)
            
            query += ' ORDER BY created_at DESC'
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            return [self._row_to_config(row) for row in rows]
    
    def update_algorithm(self, algorithm_id: int, data: Dict) -> Optional[AlgorithmConfig]:
        """更新算法配置
        
        Args:
            algorithm_id: 算法 ID
            data: 更新数据
        
        Returns:
            更新后的 AlgorithmConfig 对象或 None
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 检查算法是否存在
            cursor.execute('SELECT id FROM algorithm_configs WHERE id = ?', (algorithm_id,))
            if cursor.fetchone() is None:
                return None
            
            # 如果更新名称，检查唯一性
            if 'name' in data:
                cursor.execute(
                    'SELECT id FROM algorithm_configs WHERE name = ? AND id != ?',
                    (data['name'], algorithm_id)
                )
                if cursor.fetchone():
                    raise ValueError(f"算法名称已存在: {data['name']}")
            
            # 构建更新语句
            updatable_fields = {
                'name': 'name',
                'type': 'type',
                'version': 'version',
                'status': 'status',
                'description': 'description',
                'model_path': 'model_path',
                'model_type': 'model_type',
                'model_format': 'model_format',
                'input_size': 'input_size',
                'labels_file': 'labels_file',
                'algorithm_image': 'algorithm_image'
            }
            
            json_fields = []  # 不再有JSON字段
            
            set_clause = []
            params = []
            
            for field, column in updatable_fields.items():
                if field in data:
                    set_clause.append(f'{column} = ?')
                    if field in json_fields:
                        params.append(json.dumps(data[field]))
                    else:
                        params.append(data[field])
            
            if not set_clause:
                return self.get_algorithm(algorithm_id)
            
            # 添加更新时间
            set_clause.append('updated_at = ?')
            params.append(datetime.now())
            params.append(algorithm_id)
            
            cursor.execute(
                f'UPDATE algorithm_configs SET {", ".join(set_clause)} WHERE id = ?',
                params
            )
            
            conn.commit()
            
            logger.info(f"更新算法配置: ID={algorithm_id}")
            
            return self.get_algorithm(algorithm_id)
    
    def delete_algorithm(self, algorithm_id: int) -> bool:
        """删除算法配置
        
        Args:
            algorithm_id: 算法 ID
        
        Returns:
            是否删除成功
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 获取算法名称用于日志
            cursor.execute('SELECT name FROM algorithm_configs WHERE id = ?', (algorithm_id,))
            row = cursor.fetchone()
            
            if row is None:
                return False
            
            algorithm_name = row[0]
            
            cursor.execute('DELETE FROM algorithm_configs WHERE id = ?', (algorithm_id,))
            conn.commit()
            
            logger.info(f"删除算法配置: {algorithm_name} (ID: {algorithm_id})")
            
            return True
    
    def get_statistics(self) -> Dict:
        """获取算法统计信息
        
        Returns:
            统计信息字典
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 总数
            cursor.execute('SELECT COUNT(*) FROM algorithm_configs')
            total = cursor.fetchone()[0]
            
            # 活跃数
            cursor.execute('SELECT COUNT(*) FROM algorithm_configs WHERE status = "active"')
            active = cursor.fetchone()[0]
            
            # 不可用数
            cursor.execute('SELECT COUNT(*) FROM algorithm_configs WHERE status = "inactive"')
            inactive = cursor.fetchone()[0]
            
            # 有模型文件的数
            cursor.execute('SELECT COUNT(*) FROM algorithm_configs WHERE model_path IS NOT NULL AND model_path != ""')
            model_files = cursor.fetchone()[0]
            
            return {
                'total': total,
                'active': active,
                'inactive': inactive,
                'model_files': model_files
            }


# 全局算法管理器实例
_algorithm_manager = None


def get_algorithm_manager() -> AlgorithmManager:
    """获取算法管理器实例"""
    global _algorithm_manager
    if _algorithm_manager is None:
        _algorithm_manager = AlgorithmManager()
    return _algorithm_manager