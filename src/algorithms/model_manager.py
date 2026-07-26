"""
模型文件管理器

管理模型文件的存储、查询、上传和删除。
"""

from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime
import logging
import werkzeug
import os

logger = logging.getLogger(__name__)


# 允许的模型文件扩展名
ALLOWED_EXTENSIONS = {'onnx', 'engine', 'pt', 'pth', 'bin', 'xml', 'json', 'rknn'}


class ModelFileInfo:
    """模型文件信息类"""
    
    def __init__(
        self,
        name: str,
        path: str,
        type: str,
        size: int,
        format: str,
        modified: str
    ):
        self.name = name
        self.path = path
        self.type = type
        self.size = size
        self.format = format
        self.modified = modified
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'name': self.name,
            'path': self.path,
            'type': self.type,
            'size': self.size,
            'format': self.format,
            'modified': self.modified
        }


class ModelManager:
    """模型文件管理器"""
    
    def __init__(self, upload_folder: str = 'models'):
        """初始化模型管理器
        
        Args:
            upload_folder: 模型文件存储目录
        """
        self.upload_folder = Path(upload_folder)
        self.max_file_size = 500 * 1024 * 1024  # 500MB
        
        # 创建上传目录
        self.upload_folder.mkdir(parents=True, exist_ok=True)
    
    def is_allowed_file(self, filename: str) -> bool:
        """检查文件扩展名是否允许
        
        Args:
            filename: 文件名
        
        Returns:
            是否允许
        """
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    
    def upload_model(
        self,
        file,
        algorithm_type: str = 'detection',
        model_name: Optional[str] = None
    ) -> Dict:
        """上传模型文件
        
        Args:
            file: 文件对象
            algorithm_type: 算法类型
            model_name: 模型名称（可选）
        
        Returns:
            上传结果信息
        """
        # 检查文件名
        if not file or file.filename == '':
            raise ValueError('没有选择文件')
        
        # 检查文件类型
        if not self.is_allowed_file(file.filename):
            raise ValueError('不支持的文件类型')
        
        # 检查文件大小
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > self.max_file_size:
            raise ValueError(f'文件大小超过 {self.max_file_size // (1024*1024)}MB 限制')
        
        # 创建类型子目录
        type_folder = self.upload_folder / algorithm_type
        type_folder.mkdir(parents=True, exist_ok=True)
        
        # 安全保存文件
        filename = werkzeug.utils.secure_filename(file.filename)
        file_path = type_folder / filename
        
        # 如果文件已存在，添加时间戳
        if file_path.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            name_parts = filename.rsplit('.', 1)
            filename = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
            file_path = type_folder / filename
        
        # 保存文件
        file.save(str(file_path))
        
        # 返回文件信息
        relative_path = f"models/{algorithm_type}/{filename}"
        
        logger.info(f"模型文件已上传: {relative_path}")
        
        return {
            'success': True,
            'filename': filename,
            'path': relative_path,
            'size': file_path.stat().st_size,
            'algorithm_type': algorithm_type,
            'model_name': model_name or filename
        }
    
    def list_models(self) -> List[ModelFileInfo]:
        """列出所有模型文件
        
        Returns:
            模型文件信息列表
        """
        if not self.upload_folder.exists():
            return []
        
        models = []
        
        # 遍历所有子目录
        for type_folder in self.upload_folder.iterdir():
            if type_folder.is_dir():
                for model_file in type_folder.iterdir():
                    if model_file.is_file() and self.is_allowed_file(model_file.name):
                        models.append(ModelFileInfo(
                            name=model_file.name,
                            path=f"models/{type_folder.name}/{model_file.name}",
                            type=type_folder.name,
                            size=model_file.stat().st_size,
                            format=model_file.suffix.lstrip('.'),
                            modified=datetime.fromtimestamp(
                                model_file.stat().st_mtime
                            ).isoformat()
                        ))
        
        # 按修改时间排序
        models.sort(key=lambda x: x.modified, reverse=True)
        
        return models
    
    def delete_model(self, model_path: str) -> bool:
        """删除模型文件
        
        Args:
            model_path: 模型文件相对路径
        
        Returns:
            是否成功删除
        """
        # 构建完整路径
        full_path = self.upload_folder / model_path
        
        # 检查文件是否存在
        if not full_path.exists():
            raise FileNotFoundError('文件不存在')
        
        # 检查是否在允许的目录内（安全检查）
        try:
            full_path.resolve().relative_to(self.upload_folder.resolve())
        except ValueError:
            raise ValueError('非法路径')
        
        # 删除文件
        full_path.unlink()
        
        logger.info(f"模型文件已删除: {model_path}")
        
        return True
    
    def check_model(self, model_path: str) -> Dict:
        """检查模型文件是否存在
        
        Args:
            model_path: 模型文件相对路径
        
        Returns:
            检查结果
        """
        full_path = self.upload_folder / model_path
        
        exists = full_path.exists()
        
        if exists:
            return {
                'exists': True,
                'size': full_path.stat().st_size,
                'modified': datetime.fromtimestamp(
                    full_path.stat().st_mtime
                ).isoformat()
            }
        
        return {'exists': False}
    
    def get_model_by_path(self, model_path: str) -> Optional[ModelFileInfo]:
        """根据路径获取模型文件信息
        
        Args:
            model_path: 模型文件相对路径
        
        Returns:
            模型文件信息
        """
        full_path = self.upload_folder / model_path
        
        if not full_path.exists():
            return None
        
        # 解析路径
        parts = Path(model_path).parts
        if len(parts) >= 3:
            algorithm_type = parts[1]
            filename = parts[2]
        else:
            return None
        
        return ModelFileInfo(
            name=filename,
            path=model_path,
            type=algorithm_type,
            size=full_path.stat().st_size,
            format=full_path.suffix.lstrip('.'),
            modified=datetime.fromtimestamp(
                full_path.stat().st_mtime
            ).isoformat()
        )


# 全局模型管理器实例
_model_manager = None


def get_model_manager(upload_folder: str = 'models') -> ModelManager:
    """获取模型管理器实例
    
    Args:
        upload_folder: 模型文件存储目录
    
    Returns:
        模型管理器实例
    """
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager(upload_folder)
    return _model_manager