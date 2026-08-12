"""
算法库 REST API

提供算法配置和模型文件的创建、查询、更新、删除等操作接口。
"""

import os
import logging
from flask import Blueprint, request, jsonify
from flask_socketio import SocketIO
from typing import Dict

logger = logging.getLogger(__name__)

# 创建 Blueprint
algorithm_bp = Blueprint('algorithm', __name__, url_prefix='/api')

# SocketIO 实例（将在注册时设置）
_socketio = None


def set_socketio(socketio_instance):
    """设置 SocketIO 实例"""
    global _socketio
    _socketio = socketio_instance


# ========================================
# 标签文件解析API（新增）
# ========================================

@algorithm_bp.route('/algorithms/<int:algo_id>/classes', methods=['GET'])
def get_algorithm_classes(algo_id):
    """获取算法标签文件中的所有类别列表
    
    Args:
        algo_id: 算法ID
    
    Returns:
        classes: 类别列表（从labels_file解析）
    """
    try:
        from src.algorithms.algorithm_manager import AlgorithmManager
        manager = AlgorithmManager()
        
        algo = manager.get_algorithm(algo_id)
        if not algo:
            return jsonify({'error': '算法不存在'}), 404
        
        if not algo.labels_file:
            return jsonify({'error': '标签文件未设置'}), 404
        
        # 检查文件是否存在
        if not os.path.exists(algo.labels_file):
            return jsonify({'error': f'标签文件不存在: {algo.labels_file}'}), 404
        
        # 解析标签文件
        with open(algo.labels_file, 'r', encoding='utf-8') as f:
            classes = [line.strip() for line in f.readlines() if line.strip()]
        
        logger.info(f"解析标签文件: {algo.labels_file}, 共{len(classes)}个类别")
        
        return jsonify({
            'algorithm_id': algo_id,
            'algorithm_name': algo.name,
            'labels_file': algo.labels_file,
            'classes': classes,
            'total': len(classes)
        })
    
    except Exception as e:
        logger.error(f"解析标签文件失败: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ========================================
# 算法配置 API
# ========================================

@algorithm_bp.route('/algorithms', methods=['GET'])
def get_algorithms():
    """获取所有算法配置"""
    from src.algorithms.algorithm_manager import get_algorithm_manager
    
    # 获取筛选参数
    type_filter = request.args.get('type')
    status_filter = request.args.get('status')
    
    # 获取算法管理器
    manager = get_algorithm_manager()
    
    # 获取算法列表
    algorithms = manager.get_all_algorithms(
        type_filter=type_filter,
        status_filter=status_filter
    )
    
    # 转换为字典列表
    return jsonify([algo.to_dict() for algo in algorithms])


@algorithm_bp.route('/algorithms/<int:algorithm_id>', methods=['GET'])
def get_algorithm(algorithm_id: int):
    """获取单个算法配置"""
    from src.algorithms.algorithm_manager import get_algorithm_manager
    
    manager = get_algorithm_manager()
    algorithm = manager.get_algorithm(algorithm_id)
    
    if algorithm:
        return jsonify(algorithm.to_dict())
    
    return jsonify({'error': '算法不存在'}), 404


@algorithm_bp.route('/algorithms', methods=['POST'])
def create_algorithm():
    """创建算法配置"""
    from src.algorithms.algorithm_manager import get_algorithm_manager
    
    data = request.get_json()
    
    if not data:
        return jsonify({'error': '请求数据无效'}), 400
    
    # 必填字段检查
    if not data.get('name'):
        return jsonify({'error': '缺少必填字段: name'}), 400
    
    # 获取算法管理器
    manager = get_algorithm_manager()
    
    # 创建算法（名称唯一性检查已在 manager 中处理）
    try:
        algorithm = manager.create_algorithm(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    
    logger.info(f"创建算法配置: {algorithm.name}")
    
    # 发送 WebSocket 事件
    if _socketio:
        _socketio.emit('algorithm_created', {'algorithm_id': algorithm.id, 'name': algorithm.name}, namespace='/ws')
    
    return jsonify(algorithm.to_dict()), 201


@algorithm_bp.route('/algorithms/<int:algorithm_id>', methods=['PUT'])
def update_algorithm(algorithm_id: int):
    """更新算法配置"""
    from src.algorithms.algorithm_manager import get_algorithm_manager
    
    data = request.get_json()
    
    if not data:
        return jsonify({'error': '请求数据无效'}), 400
    
    # 获取算法管理器
    manager = get_algorithm_manager()
    
    # 更新算法
    try:
        algorithm = manager.update_algorithm(algorithm_id, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    
    if algorithm:
        logger.info(f"更新算法配置: {algorithm.name} (ID: {algorithm_id})")
        
        # 发送 WebSocket 事件
        if _socketio:
            _socketio.emit('algorithm_updated', {'algorithm_id': algorithm_id, 'name': algorithm.name}, namespace='/ws')
        
        return jsonify(algorithm.to_dict())
    
    return jsonify({'error': '算法不存在'}), 404


@algorithm_bp.route('/algorithms/<int:algorithm_id>', methods=['DELETE'])
def delete_algorithm(algorithm_id: int):
    """删除算法配置"""
    from src.algorithms.algorithm_manager import get_algorithm_manager
    
    # 获取算法管理器
    manager = get_algorithm_manager()
    
    # 获取算法名称（用于日志和事件）
    algorithm = manager.get_algorithm(algorithm_id)
    algorithm_name = algorithm.name if algorithm else ''
    
    # 删除算法
    success = manager.delete_algorithm(algorithm_id)
    
    if success:
        logger.info(f"删除算法配置: ID {algorithm_id}")
        
        # 发送 WebSocket 事件
        if _socketio:
            _socketio.emit('algorithm_deleted', {'algorithm_id': algorithm_id, 'name': algorithm_name}, namespace='/ws')
        
        return jsonify({'success': True})
    
    return jsonify({'error': '算法不存在'}), 404


@algorithm_bp.route('/algorithms/statistics', methods=['GET'])
def get_algorithm_statistics():
    """获取算法统计信息"""
    from src.algorithms.algorithm_manager import get_algorithm_manager
    
    manager = get_algorithm_manager()
    statistics = manager.get_statistics()
    
    return jsonify(statistics)


# ========================================
# 文件上传 API
# ========================================

@algorithm_bp.route('/labels/upload', methods=['POST'])
def upload_labels():
    """上传标签文件"""
    # 检查是否有文件
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400
    
    # 检查文件类型
    if not file.filename.endswith('.txt'):
        return jsonify({'error': '标签文件必须是.txt格式'}), 400
    
    # 获取算法ID（可选，但强烈建议传入以确保文件名唯一）
    algorithm_id = request.form.get('algorithm_id')

    # 标签文件目录
    labels_dir = 'models/labels'
    os.makedirs(labels_dir, exist_ok=True)

    # 生成唯一文件名，避免不同算法上传同名文件时互相覆盖
    # 格式：algo_{algorithm_id}_{原始文件名}（编辑模式）或 algo_{时间戳}_{原始文件名}（新建模式）
    original_name = file.filename
    if algorithm_id:
        filename = f"algo_{algorithm_id}_{original_name}"
    else:
        import time as _time
        filename = f"algo_{int(_time.time())}_{original_name}"
    filepath = os.path.join(labels_dir, filename)
    
    try:
        # 保存文件
        file.save(filepath)
        
        # 如果指定了算法ID，更新算法配置
        if algorithm_id:
            manager = get_algorithm_manager()
            algorithm = manager.get_algorithm(int(algorithm_id))
            
            if algorithm:
                # 更新算法的标签文件路径
                manager.update_algorithm(int(algorithm_id), {
                    'labels_file': filepath
                })
                
                logger.info(f"标签文件已上传并关联到算法 {algorithm_id}: {filepath}")
                # target_classes已废弃，不再自动更新（改为任务管理时从labels_file动态获取）
        
        logger.info(f"标签文件已上传: {filepath}")
        
        return jsonify({
            'path': filepath,
            'filename': filename,
            'algorithm_id': algorithm_id
        }), 200
    
    except Exception as e:
        logger.error(f"标签文件上传失败: {str(e)}")
        return jsonify({'error': str(e)}), 500


@algorithm_bp.route('/models/upload', methods=['POST'])
def upload_model():
    """上传模型文件"""
    from src.algorithms.model_manager import get_model_manager
    
    # 检查是否有文件
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400
    
    file = request.files['file']
    
    # 获取上传参数
    algorithm_type = request.form.get('algorithm_type', 'detection')
    model_name = request.form.get('model_name', file.filename)
    
    # 获取模型管理器
    manager = get_model_manager()
    
    try:
        # 上传文件
        result = manager.upload_model(
            file=file,
            algorithm_type=algorithm_type,
            model_name=model_name
        )
        
        logger.info(f"模型文件已上传: {result['path']}")
        
        return jsonify(result), 200
    
    except ValueError as e:
        logger.error(f"上传失败: {str(e)}")
        return jsonify({'error': str(e)}), 400
    
    except Exception as e:
        logger.error(f"上传失败: {str(e)}")
        return jsonify({'error': '上传失败'}), 500


# ========================================
# 算法图片上传 API
# ========================================

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}


@algorithm_bp.route('/algorithms/<int:algorithm_id>/image', methods=['POST'])
def upload_algorithm_image(algorithm_id: int):
    """上传算法展示图片"""
    from src.algorithms.algorithm_manager import get_algorithm_manager
    
    manager = get_algorithm_manager()
    algo = manager.get_algorithm(algorithm_id)
    if not algo:
        return jsonify({'error': '算法不存在'}), 404
    
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': '文件名为空'}), 400
    
    # 检查文件扩展名
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({'error': f'不支持的图片格式，允许: {", ".join(ALLOWED_IMAGE_EXTENSIONS)}'}), 400
    
    # 存储目录
    upload_dir = os.path.join('static', 'images', 'algorithms')
    os.makedirs(upload_dir, exist_ok=True)
    
    # 文件名：算法ID + 时间戳 + 扩展名
    from datetime import datetime
    filename = f"algo_{algorithm_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    filepath = os.path.join(upload_dir, filename)
    
    # 如果有旧图片，删除
    if algo.algorithm_image:
        old_path = algo.algorithm_image.lstrip('/')
        if os.path.exists(old_path):
            os.remove(old_path)
    
    file.save(filepath)
    image_url = f"/static/images/algorithms/{filename}"
    
    # 更新数据库
    manager.update_algorithm(algorithm_id, {'algorithm_image': image_url})
    
    logger.info(f"算法图片已上传: algorithm_id={algorithm_id}, path={image_url}")
    
    return jsonify({
        'algorithm_id': algorithm_id,
        'algorithm_image': image_url
    }), 200


@algorithm_bp.route('/models', methods=['GET'])
def list_models():
    """列出所有模型文件"""
    from src.algorithms.model_manager import get_model_manager
    
    manager = get_model_manager()
    models = manager.list_models()
    
    return jsonify([model.to_dict() for model in models])


@algorithm_bp.route('/models/<path:model_path>', methods=['DELETE'])
def delete_model(model_path: str):
    """删除模型文件"""
    from src.algorithms.model_manager import get_model_manager
    
    manager = get_model_manager()
    
    try:
        success = manager.delete_model(model_path)
        
        if success:
            logger.info(f"模型文件已删除: {model_path}")
            return jsonify({'success': True})
        
    except FileNotFoundError:
        return jsonify({'error': '文件不存在'}), 404
    
    except ValueError as e:
        logger.error(f"删除失败: {str(e)}")
        return jsonify({'error': str(e)}), 403
    
    except Exception as e:
        logger.error(f"删除失败: {str(e)}")
        return jsonify({'error': '删除失败'}), 500
    
    return jsonify({'error': '删除失败'}), 500


@algorithm_bp.route('/models/check/<path:model_path>', methods=['GET'])
def check_model(model_path: str):
    """检查模型文件是否存在"""
    from src.algorithms.model_manager import get_model_manager
    
    manager = get_model_manager()
    result = manager.check_model(model_path)
    
    return jsonify(result)


@algorithm_bp.route('/models/<path:model_path>', methods=['GET'])
def get_model(model_path: str):
    """获取模型文件信息"""
    from src.algorithms.model_manager import get_model_manager
    
    manager = get_model_manager()
    model = manager.get_model_by_path(model_path)
    
    if model:
        return jsonify(model.to_dict())
    
    return jsonify({'error': '文件不存在'}), 404


# ========================================
# 辅助函数
# ========================================

def register_algorithm_blueprint(app, socketio=None):
    """注册算法 Blueprint 到 Flask 应用
    
    Args:
        app: Flask 应用实例
        socketio: SocketIO 实例（可选，用于实时推送）
    """
    app.register_blueprint(algorithm_bp)
    
    # 设置 SocketIO 实例
    if socketio:
        set_socketio(socketio)
    
    # 初始化算法管理器（创建数据库表和默认算法）
    from src.algorithms.algorithm_manager import get_algorithm_manager
    manager = get_algorithm_manager()
    logger.info(f"算法管理器已初始化，当前算法数量: {len(manager.get_all_algorithms())}")
    
    logger.info("算法库 Blueprint 已注册")