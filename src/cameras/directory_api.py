"""
目录管理API

提供摄像机目录管理的HTTP API接口。
"""

import logging
from flask import Blueprint, request, jsonify

from .directory_service import get_directory_service, CameraDirectory

logger = logging.getLogger(__name__)

# 创建Blueprint
directory_bp = Blueprint('directory', __name__, url_prefix='/api/directories')


# ========================================
# 目录管理API
# ========================================

@directory_bp.route('', methods=['GET'])
def get_directories():
    """
    获取所有目录
    
    Returns:
        目录列表
    """
    try:
        service = get_directory_service()
        directories = service.get_all_directories()
        
        # 为每个目录添加摄像机数量
        result = []
        for d in directories:
            dir_dict = d.to_dict()
            dir_dict['camera_count'] = service.get_camera_count_by_directory(d.id)
            result.append(dir_dict)
        
        return jsonify({
            'success': True,
            'directories': result
        })
        
    except Exception as e:
        logger.error(f"获取目录列表失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@directory_bp.route('/tree', methods=['GET'])
def get_directory_tree():
    """
    获取目录树结构
    
    Returns:
        目录树
    """
    try:
        service = get_directory_service()
        tree = service.get_directory_tree()
        
        return jsonify({
            'success': True,
            'tree': tree
        })
        
    except Exception as e:
        logger.error(f"获取目录树失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@directory_bp.route('/<int:directory_id>', methods=['GET'])
def get_directory(directory_id: int):
    """
    获取单个目录
    
    Args:
        directory_id: 目录ID
    
    Returns:
        目录信息
    """
    try:
        service = get_directory_service()
        directory = service.get_directory(directory_id)
        
        if not directory:
            return jsonify({'error': '目录不存在'}), 404
        
        # 获取目录下的摄像机数量
        camera_count = service.get_camera_count_by_directory(directory_id)
        
        result = directory.to_dict()
        result['camera_count'] = camera_count
        
        return jsonify({
            'success': True,
            'directory': result
        })
        
    except Exception as e:
        logger.error(f"获取目录失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@directory_bp.route('', methods=['POST'])
def create_directory():
    """
    创建目录
    
    Request Body:
        {
            "name": "目录名称",
            "parent_id": null,  // 可选
            "description": "描述",  // 可选
            "sort_order": 0  // 可选
        }
    
    Returns:
        创建结果
    """
    try:
        data = request.get_json()
        
        if not data or not data.get('name'):
            return jsonify({'error': '缺少目录名称'}), 400
        
        service = get_directory_service()
        
        directory = CameraDirectory(
            name=data['name'],
            parent_id=data.get('parent_id'),
            description=data.get('description', ''),
            sort_order=data.get('sort_order', 0)
        )
        
        directory_id = service.create_directory(directory)
        
        logger.info(f"创建目录成功: id={directory_id}, name={directory.name}")
        
        return jsonify({
            'success': True,
            'message': '目录创建成功',
            'directory_id': directory_id,
            'directory': service.get_directory(directory_id).to_dict()
        })
        
    except Exception as e:
        logger.error(f"创建目录失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@directory_bp.route('/<int:directory_id>', methods=['PUT'])
def update_directory(directory_id: int):
    """
    更新目录
    
    Args:
        directory_id: 目录ID
    
    Request Body:
        {
            "name": "新名称",  // 可选
            "parent_id": null,  // 可选
            "description": "新描述",  // 可选
            "sort_order": 1  // 可选
        }
    
    Returns:
        更新结果
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '缺少更新数据'}), 400
        
        service = get_directory_service()
        
        # 检查目录是否存在
        existing = service.get_directory(directory_id)
        if not existing:
            return jsonify({'error': '目录不存在'}), 404
        
        # 更新目录
        success = service.update_directory(directory_id, data)
        
        if success:
            logger.info(f"更新目录成功: id={directory_id}")
            
            return jsonify({
                'success': True,
                'message': '目录更新成功',
                'directory': service.get_directory(directory_id).to_dict()
            })
        
        return jsonify({'error': '更新失败'}), 500
        
    except Exception as e:
        logger.error(f"更新目录失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@directory_bp.route('/<int:directory_id>', methods=['DELETE'])
def delete_directory(directory_id: int):
    """
    删除目录
    
    Args:
        directory_id: 目录ID
    
    Returns:
        删除结果
    """
    try:
        service = get_directory_service()
        
        # 检查目录是否存在
        existing = service.get_directory(directory_id)
        if not existing:
            return jsonify({'error': '目录不存在'}), 404
        
        # 删除目录
        success = service.delete_directory(directory_id)
        
        if success:
            logger.info(f"删除目录成功: id={directory_id}")
            
            return jsonify({
                'success': True,
                'message': '目录删除成功'
            })
        
        return jsonify({'error': '删除失败，可能存在子目录或为默认目录'}), 400
        
    except Exception as e:
        logger.error(f"删除目录失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@directory_bp.route('/<int:directory_id>/cameras', methods=['GET'])
def get_directory_cameras(directory_id: int):
    """
    获取目录下的摄像机
    
    Args:
        directory_id: 目录ID
    
    Returns:
        摄像机列表
    """
    try:
        from .camera_manager import get_camera_manager
        
        manager = get_camera_manager()
        cameras = manager.get_cameras_by_directory(directory_id)
        
        return jsonify({
            'success': True,
            'directory_id': directory_id,
            'cameras': [c.to_dict() for c in cameras]
        })
        
    except Exception as e:
        logger.error(f"获取目录摄像机失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ========================================
# Blueprint注册函数
# ========================================

def register_directory_blueprint(app):
    """
    注册目录管理Blueprint到Flask应用
    
    Args:
        app: Flask应用实例
    """
    app.register_blueprint(directory_bp)
    logger.info("目录管理Blueprint已注册")