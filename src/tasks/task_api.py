"""
任务管理 REST API

提供任务、摄像机、任务-摄像机关联的 CRUD 操作接口。
支持多摄像机多任务架构。
"""

from flask import Blueprint, request, jsonify, send_file
from datetime import datetime
from typing import Dict, List
import sqlite3
import logging

# 创建 Blueprint
task_bp = Blueprint('tasks', __name__, url_prefix='/api')

# 日志器
logger = logging.getLogger(__name__)

# 任务管理器和进程管理器（延迟初始化）
_task_manager = None
_process_manager = None
_socketio = None

def _normalize_detector_gpu_mem_limit(detector_config: dict) -> dict:
    """归一化 detector_config.gpu_mem_limit 为 int（统一字节单位，入库前调用）。

    单位契约（统一传字节）：所有调用方——AI 前端 config_modal.js 与边缘平台——
    提交时均已将 MB 乘以 1024*1024 转成字节，因此这里不再做单位猜测：
      - > 0 的值视为已是字节，保持原值（仅转 int）；
      - 0 或 None 表示不限制，保持不变。
    禁止再做"< 1GB 视为 MB 再乘"的启发式，否则会把已转字节的小值
    （如 512MB=536870912）误判为 MB 二次放大。
    """
    if not isinstance(detector_config, dict):
        return detector_config
    if 'gpu_mem_limit' not in detector_config:
        return detector_config
    val = detector_config['gpu_mem_limit']
    if val is None:
        return detector_config
    try:
        detector_config['gpu_mem_limit'] = int(val)
    except (TypeError, ValueError):
        pass
    return detector_config


def get_task_manager():
    """获取任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        from src.tasks.task_manager import TaskManager
        _task_manager = TaskManager()

    return _task_manager


def get_process_manager():
    """获取进程管理器实例（从 run.py 全局变量获取）"""
    global _process_manager
    if _process_manager is None:
        # ✅ 方法1：尝试从 run.py 获取全局 pm（如果已初始化）
        try:
            import run
            if hasattr(run, 'pm') and run.pm is not None:
                _process_manager = run.pm
            elif hasattr(run, 'process_manager') and run.process_manager is not None:
                # ✅ 兼容旧版本
                _process_manager = run.process_manager
            elif hasattr(run, 'get_process_manager'):
                # ✅ 方法2：调用 run.py 的 get_process_manager() 函数
                _process_manager = run.get_process_manager()
            else:
                # ✅ 方法3：创建临时实例（兜底）
                logger.warning("[get_process_manager] run.pm 未初始化，创建临时实例")
                from run import TaskProcessManager
                _process_manager = TaskProcessManager()
        except Exception as e:
            logger.error(f"[get_process_manager] 获取进程管理器失败: {e}")
            # ✅ 方法4：创建一个新的实例（最后手段）
            from run import TaskProcessManager
            _process_manager = TaskProcessManager()

    return _process_manager


def set_socketio(socketio_instance):
    """设置 SocketIO 实例"""
    global _socketio
    _socketio = socketio_instance


# ========================================
# 摄像机管理 API
# ========================================

# ========================================
# 任务管理 API
# ========================================

@task_bp.route('/tasks', methods=['GET'])
def get_tasks():
    """获取所有任务（后端分页）"""
    enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'
    status = request.args.get('status')
    search = request.args.get('search', '')
    
    # 获取分页参数
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    
    task_manager = get_task_manager()
    tasks = task_manager.get_all_tasks(enabled_only=enabled_only, status=status)
    
    # 获取每个任务关联的设备通道数量
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    channel_pool_manager = get_device_channel_pool_manager()
    
    task_list = []
    for task in tasks:
        task_dict = task.to_dict()
        # 统计device_channel_pool表中该任务关联的设备通道数量
        channels = channel_pool_manager.get_task_device_channels(task.id)
        task_dict['channel_count'] = len(channels)
        task_list.append(task_dict)
    
    # 应用搜索筛选
    if search:
        search_lower = search.lower()
        task_list = [t for t in task_list if 
                     search_lower in t.get('task_name', '').lower()]
    
    # 后端分页
    total = len(task_list)
    total_pages = (total + per_page - 1) // per_page if per_page > 0 else 1
    start = (page - 1) * per_page
    end = start + per_page
    tasks_page = task_list[start:end]
    
    return jsonify({
        'tasks': tasks_page,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages
    })


@task_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id: int):
    """获取单个任务"""
    task_manager = get_task_manager()
    task = task_manager.get_task(task_id)
    
    if task:
        from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
        channel_pool_manager = get_device_channel_pool_manager()
        channels = channel_pool_manager.get_task_device_channels(task_id)
        
        task_dict = task.to_dict()
        task_dict['channels'] = [ch.to_dict() for ch in channels]
        
        return jsonify(task_dict)
    return jsonify({'error': '任务不存在'}), 404


@task_bp.route('/tasks', methods=['POST'])
def create_task():
    """创建任务"""
    data = request.json

    if not data:
        return jsonify({'error': '请求数据无效'}), 400

    # 必填字段检查
    if not data.get('task_name'):
        return jsonify({'error': '缺少必填字段: task_name'}), 400

    from src.tasks.task_manager import DetectionTask
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager, DeviceChannelPool
    from src.cameras.camera_manager import get_camera_manager

    # ========================================
    # 配置来源（config/*.yaml 为单一真相源）
    # ========================================
    # 任务表仅保存"按任务动态调整"的少量参数，其余配置一律由 config/*.yaml 提供：
    #   detector_config -> conf_threshold（另由算法库派生 input_width/input_height）
    #   event_config    -> enabled
    #   decoder/tracker/motion/display_config -> 不再写入任务表
    # TaskManager._row_to_task 读取时会合并 YAML 默认值，因此这里无需补齐默认值，
    # 避免把 YAML 快照固化到任务表、造成同一参数存在多个来源。

    # gpu_mem_limit 入库前 MB→字节（前端表单单位为 MB，后端统一转字节存储）
    detector_config = _normalize_detector_gpu_mem_limit(dict(data.get('detector_config') or {}))
    event_config = dict(data.get('event_config') or {})
    decoder_config = data.get('decoder_config') or {}
    tracker_config = data.get('tracker_config') or {}
    motion_config = data.get('motion_config') or {}
    display_config = data.get('display_config') or {}

    # 区域配置唯一来源为通道表（device_channel_pool.zones_config），
    # 任务表不再保存区域配置：剥离 event_config.capture.required_zones，防止重复来源
    if isinstance(event_config, dict):
        capture_cfg = event_config.get('capture', {})
        if isinstance(capture_cfg, dict) and 'required_zones' in capture_cfg:
            del capture_cfg['required_zones']
            logger.info("[TaskApi] 创建任务：已剥离 event_config.capture.required_zones（区域配置仅存于通道表）")

    # target_classes：空数组表示不限制检测类别（检测所有类别）
    target_classes = data.get('target_classes', [])

    # ========================================
    # 从算法库获取标签路径和模型输入尺寸（算法库为单一真相源）
    # ========================================
    algorithm_name = data.get('algorithm', 'yolov11')
    labels_file = ''
    try:
        from src.algorithms.algorithm_manager import get_algorithm_manager
        algo_config = get_algorithm_manager().get_algorithm_by_name(algorithm_name)
        if algo_config:
            # 标签路径：从算法库 labels_file 拷贝到任务表（独立字段）
            labels_file = algo_config.labels_file or ''
            # 模型输入尺寸：解析算法库 input_size（如 '640x640'）写入 detector_config
            # 算法库为模型尺寸单一真相源，覆盖 merge_config 中的默认 640
            input_size_str = algo_config.input_size or '640x640'
            try:
                # 兼容 '640x640' 和 '640*640' 两种分隔符写法
                parts = input_size_str.lower().replace('*', 'x').split('x')
                if len(parts) == 2:
                    detector_config['input_width'] = int(parts[0])
                    detector_config['input_height'] = int(parts[1])
            except Exception as e:
                logger.warning(f"解析算法库 input_size 失败: {input_size_str}, 错误: {e}")
        else:
            logger.warning(f"算法库中未找到算法: {algorithm_name}, labels_file/模型尺寸使用默认值")
    except Exception as e:
        logger.error(f"查询算法库失败: {e}, labels_file/模型尺寸使用默认值")

    # 语义检测类型（事件类型）：人员统计任务未显式指定时自动联动为 people_counting
    event_type = data.get('event_type', '') or \
        ('people_counting' if data.get('detection_type') == 'people_counting' else 'tracking')

    task = DetectionTask(
        task_name=data.get('task_name', ''),
        enabled=data.get('enabled', True),
        detection_type=data.get('detection_type', 'realtime'),
        event_type=event_type,
        polling_interval_frames=data.get('polling_interval_frames', 5),
        motion_gate_fallback_interval=data.get('motion_gate_fallback_interval', 30),
        schedule_cron=data.get('schedule_cron', ''),
        schedule_source=data.get('schedule_source', ''),
        file_paths=data.get('file_paths', []),
        file_loop=data.get('file_loop', False),
        voice_enabled=bool(data.get('voice_enabled', False)),  # 报警语音播报开关
        voice_mp3=data.get('voice_mp3', ''),  # 报警语音 MP3 预置文件名
        voice_loop_count=int(data.get('voice_loop_count', 1)),  # 播放循环次数
        voice_interval_ms=int(data.get('voice_interval_ms', 1000)),  # 循环播放时间间隙（毫秒）
        voice_volume=float(data.get('voice_volume', 1.0)),  # 播放音量
        count_alarm_threshold=int(data.get('count_alarm_threshold', 0) or 0),  # 区域人数报警阈值（仅人员统计）
        algorithm=data.get('algorithm', 'yolov11'),
        model_path=data.get('model_path', 'models/yolov11n.onnx'),
        model_type=data.get('model_type', 'yolov11n'),  # 模型类型字段
        labels_file=labels_file,  # 标签文件路径（从算法库拷贝）
        target_classes=target_classes,
        motion_detection_enabled=data.get('motion_detection_enabled', True),
        tracking_enabled=data.get('tracking_enabled', True),
        decoder_config=decoder_config,
        detector_config=detector_config,
        tracker_config=tracker_config,
        motion_config=motion_config,
        event_config=event_config,
        display_config=display_config
    )

    task_manager = get_task_manager()
    task_id = task_manager.add_task(task)

    # 处理临时通道（批量入库）
    temp_channels = data.get('temp_channels', [])
    existing_channel_ids = data.get('existing_channel_ids', [])

    if temp_channels or existing_channel_ids:
        channel_pool_manager = get_device_channel_pool_manager()
        camera_manager = get_camera_manager()

        # 1. 批量入库临时通道到 device_channel_pool 表
        channel_ids = []
        for temp_channel in temp_channels:
            channel = DeviceChannelPool(
                channel_name=temp_channel.get('channel_name', ''),
                rtsp_url=temp_channel.get('rtsp_url', ''),
                channel_source=temp_channel.get('channel_source', 'local_platform'),
                enabled=temp_channel.get('enabled', True)
            )
            channel_id = channel_pool_manager.add_device_channel(channel)
            channel_ids.append(channel_id)

            # 2. 更新摄像机表的 ai_detection 状态
            camera_id = temp_channel.get('camera_id')
            if camera_id:
                camera_manager.update_camera(camera_id, {'ai_detection': True})
                # 3. 创建摄像机与设备通道池的关联
                try:
                    channel_pool_manager.bind_camera_to_channel(camera_id, channel_id)
                    logger.info(f"摄像机与通道已关联: camera_id={camera_id}, channel_id={channel_id}")
                except Exception as bind_err:
                    logger.warning(f"关联摄像机与通道失败: camera_id={camera_id}, channel_id={channel_id}, error={bind_err}")

        # 4. 合并已存在的通道ID
        channel_ids.extend([int(id) for id in existing_channel_ids])

        # 5. 将通道绑定到任务
        for channel_id in channel_ids:
            channel_pool_manager.bind_device_channel_to_task(task_id, channel_id)

    # WebSocket 通知
    if _socketio:
        _socketio.emit('task_created', {'task_id': task_id})

    result_task = task_manager.get_task(task_id)

    return jsonify({
        'task_id': task_id,
        'message': '任务创建成功',
        'task': result_task.to_dict()
    }), 201


@task_bp.route('/tasks/<int:task_id>', methods=['PUT'])
def update_task(task_id: int):
    """更新任务

    更新任务时会：
    1. 更新任务本身的配置（支持配置合并）
    2. 处理通道数据的更新（新增临时通道、更新已有通道绑定）
    3. 更新摄像机信息（如果更换了摄像机）
    """
    data = request.json

    if not data:
        return jsonify({'error': '请求数据无效'}), 400

    task_manager = get_task_manager()

    # 检查任务是否存在
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    # ========================================
    # 配置合并逻辑（更新任务时）
    # ========================================
    # 规则：
    # 1. 读取数据库中的现有配置（如果有）
    # 2. 如果前端传了配置，合并前端配置（前端值覆盖现有值）
    # 3. 如果前端未传配置，保持数据库中的现有配置（不修改）
    # 4. 重要：保留现有配置中前端未包含的字段（防止配置丢失）

    def merge_config_for_update(existing_config: dict, frontend_config: dict) -> dict:
        """
        更新时合并配置（前端值覆盖现有值，保留未提交的字段）

        Args:
            existing_config: 数据库中的现有配置
            frontend_config: 前端传入值（可能为None或空字典）

        Returns:
            合并后的完整配置
        """
        if not frontend_config or not isinstance(frontend_config, dict) or len(frontend_config) == 0:
            return existing_config

        # 深拷贝现有配置，避免修改原数据
        merged = {}
        
        # 1. 先复制现有配置中的所有字段（保留未提交的字段）
        for key, value in existing_config.items():
            if isinstance(value, dict):
                # 字典类型：深拷贝
                merged[key] = value.copy()
            elif isinstance(value, list):
                # 列表类型：复制列表
                merged[key] = value.copy()
            else:
                # 其他类型：直接复制
                merged[key] = value
        
        # 2. 合并前端配置（前端值覆盖现有值）
        for key, value in frontend_config.items():
            if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                # 如果都是字典，递归合并
                merged[key].update(value)
            else:
                # 否则直接覆盖
                merged[key] = value
        
        return merged

    # 配置字段列表
    config_fields = ['decoder_config', 'detector_config', 'tracker_config',
                     'motion_config', 'event_config', 'display_config']

    # 准备更新数据（深拷贝避免修改原始数据）
    update_data = data.copy()

    # 处理配置字段的合并
    # 任务表只保存"按任务动态调整"的覆盖值（YAML 提供其余配置），
    # 因此以库中原始覆盖值为基准合并，避免把 YAML 快照写回任务表
    raw_overrides = task_manager.get_config_overrides(task_id, config_fields)
    for config_field in config_fields:
        if config_field in update_data:
            existing_config = raw_overrides.get(config_field, {})
            # 合并前端配置
            merged_config = merge_config_for_update(existing_config, update_data[config_field])
            # 更新到待更新数据
            update_data[config_field] = merged_config

    # gpu_mem_limit 入库前 MB→字节（前端表单单位为 MB，后端统一转字节存储）
    if 'detector_config' in update_data:
        update_data['detector_config'] = _normalize_detector_gpu_mem_limit(update_data['detector_config'])

    # 区域配置唯一来源为通道表（device_channel_pool.zones_config），
    # 任务表不再保存区域配置：剥离 event_config.capture.required_zones，防止重复来源
    merged_event_config = update_data.get('event_config')
    if isinstance(merged_event_config, dict):
        capture_cfg = merged_event_config.get('capture', {})
        if isinstance(capture_cfg, dict) and 'required_zones' in capture_cfg:
            del capture_cfg['required_zones']
            logger.info(f"[TaskApi] 更新任务 {task_id}：已剥离 event_config.capture.required_zones（区域配置仅存于通道表）")

    # 1. 更新任务本身的配置
    success = task_manager.update_task(task_id, update_data)

    if success:
        # 2. 处理通道数据的更新
        temp_channels = data.get('temp_channels', [])
        existing_channel_ids = data.get('existing_channel_ids', [])

        if temp_channels or existing_channel_ids:
            from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager, DeviceChannelPool
            from src.cameras.camera_manager import get_camera_manager

            channel_pool_manager = get_device_channel_pool_manager()
            camera_manager = get_camera_manager()

            # 获取任务现有的通道绑定
            existing_task_channels = channel_pool_manager.get_task_device_channels(task_id)
            existing_task_channel_ids = [ch.id for ch in existing_task_channels]

            # 2.1 批量入库临时通道到 device_channel_pool 表
            new_channel_ids = []
            for temp_channel in temp_channels:
                channel = DeviceChannelPool(
                    channel_name=temp_channel.get('channel_name', ''),
                    rtsp_url=temp_channel.get('rtsp_url', ''),
                    channel_source=temp_channel.get('channel_source', 'local_platform'),
                    enabled=temp_channel.get('enabled', True)
                )
                channel_id = channel_pool_manager.add_device_channel(channel)
                new_channel_ids.append(channel_id)

                # 更新摄像机表的 ai_detection 状态
                camera_id = temp_channel.get('camera_id')
                if camera_id:
                    camera_manager.update_camera(camera_id, {'ai_detection': True})
                    # 创建摄像机与设备通道池的关联
                    try:
                        channel_pool_manager.bind_camera_to_channel(camera_id, channel_id)
                        logger.info(f"摄像机与通道已关联: camera_id={camera_id}, channel_id={channel_id}")
                    except Exception as bind_err:
                        logger.warning(f"关联摄像机与通道失败: camera_id={camera_id}, channel_id={channel_id}, error={bind_err}")

            # 2.2 合并已存在的通道ID
            all_channel_ids = new_channel_ids + [int(id) for id in existing_channel_ids]

            # 2.3 解绑不在新列表中的通道，并删除不再使用的通道池记录
            for old_channel_id in existing_task_channel_ids:
                if old_channel_id not in all_channel_ids:
                    # 解绑通道与任务
                    channel_pool_manager.unbind_device_channel_from_task(task_id, old_channel_id)
                    logger.info(f"解绑旧通道: task_id={task_id}, channel_id={old_channel_id}")

                    # 检查该通道是否还被其他任务使用
                    other_task_ids = channel_pool_manager.get_device_channel_tasks(old_channel_id)

                    # 如果该通道不再被任何任务使用，删除通道池记录
                    if len(other_task_ids) == 0:
                        # 获取通道信息，用于更新摄像机ai_detection状态
                        old_channel = channel_pool_manager.get_device_channel(old_channel_id)
                        if old_channel:
                            # 删除通道池记录
                            channel_pool_manager.delete_device_channel(old_channel_id)
                            logger.info(f"删除不再使用的通道池记录: channel_id={old_channel_id}")

                            # 更新camera.ai_detection状态（根据rtsp_url匹配）
                            try:
                                all_cameras = camera_manager.get_all_cameras()
                                matching_camera = None

                                # 尝试通过rtsp_url匹配
                                for camera in all_cameras:
                                    if camera.rtsp_stream == old_channel.rtsp_url or camera.source == old_channel.rtsp_url:
                                        matching_camera = camera
                                        break

                                if matching_camera:
                                    # 检查该摄像机是否还被其他通道使用
                                    other_channels = channel_pool_manager.get_camera_channels(matching_camera.id)
                                    if len(other_channels) == 0:
                                        # 该摄像机不再被任何通道使用，更新ai_detection状态
                                        camera_manager.update_camera(matching_camera.id, {'ai_detection': False})
                                        logger.info(f"更新摄像机ai_detection状态为False: camera_id={matching_camera.id}")
                            except Exception as camera_err:
                                logger.warning(f"更新摄像机ai_detection状态失败: {camera_err}")

            # 2.4 绑定新的通道（确保所有通道都绑定）
            for channel_id in all_channel_ids:
                if channel_id not in existing_task_channel_ids:
                    channel_pool_manager.bind_device_channel_to_task(task_id, channel_id)
                    logger.info(f"绑定新通道: task_id={task_id}, channel_id={channel_id}")

        task = task_manager.get_task(task_id)

        # WebSocket 通知
        if _socketio:
            _socketio.emit('task_updated', {'task_id': task_id})

        return jsonify({
            'message': '任务更新成功',
            'task': task.to_dict()
        })

    return jsonify({'error': '任务不存在'}), 404


@task_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id: int):
    """删除任务
    
    删除任务时会：
    1. 删除任务关联的所有设备通道（device_channel_pool）
    2. 删除任务本身
    3. 更新camera.ai_detection状态（如果设备不再被其他任务使用）
    """
    task_manager = get_task_manager()

    # 检查任务是否存在
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    from src.cameras.camera_manager import get_camera_manager

    channel_pool_manager = get_device_channel_pool_manager()
    camera_manager = get_camera_manager()

    # 1. 获取任务关联的所有设备通道
    channels = channel_pool_manager.get_task_device_channels(task_id)

    # 2. 删除每个设备通道
    for channel in channels:
        channel_id = channel.id

        # 检查该设备通道是否还被其他任务使用
        other_task_ids = channel_pool_manager.get_device_channel_tasks(channel_id)

        # 如果该设备通道只被当前任务使用，则删除并更新camera.ai_detection
        if len(other_task_ids) <= 1:  # 只被当前任务使用（或没有其他任务）
            # 删除设备通道
            channel_pool_manager.delete_device_channel(channel_id)

            # 更新camera.ai_detection状态（根据rtsp_url匹配）
            try:
                # 获取所有摄像机，查找匹配的摄像机
                all_cameras = camera_manager.get_all_cameras()
                matching_camera = None

                # 尝试通过rtsp_url匹配
                for camera in all_cameras:
                    if camera.rtsp_stream == channel.rtsp_url or camera.source == channel.rtsp_url:
                        matching_camera = camera
                        break

                # 如果找到匹配的摄像机，更新ai_detection状态
                if matching_camera:
                    camera_manager.update_camera(matching_camera.id, {'ai_detection': False})
                    logger.info(f"[TaskAPI] 更新摄像机AI检测状态: camera_id={matching_camera.id}, ai_detection=False")
            except Exception as e:
                logger.error(f"[TaskAPI] 更新摄像机AI检测状态失败: {e}")

    # 3. 删除任务本身（会自动删除task_device_channel_bindings表的关联记录）
    success = task_manager.delete_task(task_id)

    if success:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('task_deleted', {'task_id': task_id})

        return jsonify({'message': '任务删除成功'})
    return jsonify({'error': '任务删除失败'}), 500


@task_bp.route('/tasks/<int:task_id>/start', methods=['POST'])
def start_task(task_id: int):
    """启动任务"""
    task_manager = get_task_manager()
    process_manager = get_process_manager()

    # 获取任务和关联的设备通道
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    # ✅ 修复：立即启动进程（避免状态不一致）
    task_config = task.to_dict()
    success = process_manager.start_task(task_config)

    if not success:
        return jsonify({'error': '任务启动失败'}), 500

    # 更新任务状态
    task_manager.update_task(task_id, {'status': 'running', 'enabled': True})

    # 更新所有绑定的设备通道状态
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    channel_pool_manager = get_device_channel_pool_manager()
    channels = channel_pool_manager.get_task_device_channels(task_id)

    for channel in channels:
        # 更新绑定状态为running
        with sqlite3.connect(channel_pool_manager.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE task_device_channel_bindings
                SET status = 'running'
                WHERE task_id = ? AND channel_id = ?
            ''', (task_id, channel.id))
            conn.commit()

    # WebSocket 通知
    if _socketio:
        _socketio.emit('task_started', {'task_id': task_id})

    return jsonify({'message': '任务已启动', 'task': task_manager.get_task(task_id).to_dict()})


@task_bp.route('/tasks/<int:task_id>/stop', methods=['POST'])
def stop_task(task_id: int):
    """停止任务"""
    task_manager = get_task_manager()
    process_manager = get_process_manager()

    # 获取任务和关联的设备通道
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    # ✅ 修复：立即停止进程（避免僵尸进程）
    process_manager.stop_task(task_id)

    # 更新任务状态
    task_manager.update_task(task_id, {'status': 'stopped'})

    # 更新所有绑定的设备通道状态
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    channel_pool_manager = get_device_channel_pool_manager()
    channels = channel_pool_manager.get_task_device_channels(task_id)

    for channel in channels:
        # 更新绑定状态为stopped
        with sqlite3.connect(channel_pool_manager.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE task_device_channel_bindings
                SET status = 'stopped'
                WHERE task_id = ? AND channel_id = ?
            ''', (task_id, channel.id))
            conn.commit()

    # WebSocket 通知
    if _socketio:
        _socketio.emit('task_stopped', {'task_id': task_id})

    return jsonify({'message': '任务已停止', 'task': task_manager.get_task(task_id).to_dict()})


# ========================================
# 任务-摄像机关联 API
# ========================================

@task_bp.route('/tasks/<int:task_id>/cameras', methods=['GET'])
def get_task_cameras(task_id: int):
    """获取任务关联的摄像机ID列表"""
    task_manager = get_task_manager()
    camera_ids = task_manager.get_task_cameras(task_id)
    
    return jsonify(camera_ids)


@task_bp.route('/tasks/<int:task_id>/cameras/<camera_id>', methods=['POST'])
def bind_camera_to_task(task_id: int, camera_id: str):
    """将摄像机绑定到任务
    
    Args:
        task_id: 任务 ID
        camera_id: 摄像机 ID（UUID字符串）
    """
    task_manager = get_task_manager()
    
    # 检查任务是否存在
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    
    # 检查摄像机是否存在（从 camera_manager 获取）
    from src.cameras.camera_manager import get_camera_manager
    camera_manager = get_camera_manager()
    camera = camera_manager.get_camera(camera_id)
    if not camera:
        return jsonify({'error': '摄像机不存在'}), 404
    
    binding_id = task_manager.bind_camera_to_task(task_id, camera_id)
    
    if binding_id:
        # 绑定成功后，更新摄像机的AI检测状态为True
        if not camera.ai_detection:
            camera_manager.update_camera(camera_id, {'ai_detection': True})
            logger.info(f"[Task] 摄像机绑定到任务，启用AI检测: camera_id={camera_id}, task_id={task_id}")
        
        # WebSocket 通知
        if _socketio:
            _socketio.emit('camera_bound', {'task_id': task_id, 'camera_id': camera_id})
        
        return jsonify({
            'message': '摄像机绑定成功',
            'binding_id': binding_id,
            'task': task_manager.get_task(task_id).to_dict()
        })
    return jsonify({'error': '绑定失败或已存在'}), 400


@task_bp.route('/tasks/<int:task_id>/cameras/<camera_id>', methods=['DELETE'])
def unbind_camera_from_task(task_id: int, camera_id: str):
    """解除摄像机与任务的绑定
    
    Args:
        task_id: 任务 ID
        camera_id: 摄像机 ID（UUID字符串）
    """
    task_manager = get_task_manager()
    success = task_manager.unbind_camera_from_task(task_id, camera_id)
    
    if success:
        # 解除绑定后，检查摄像机是否还有其他任务绑定
        from src.cameras.camera_manager import get_camera_manager
        camera_manager = get_camera_manager()
        camera = camera_manager.get_camera(camera_id)
        
        # 获取该摄像机绑定的所有任务
        camera_tasks = task_manager.get_camera_tasks(camera_id)
        
        # 如果没有绑定任何任务，将AI检测状态设为False
        if len(camera_tasks) == 0 and camera and camera.ai_detection:
            camera_manager.update_camera(camera_id, {'ai_detection': False})
            logger.info(f"[Task] 摄像机解除所有任务绑定，禁用AI检测: camera_id={camera_id}")
        
        # WebSocket 通知
        if _socketio:
            _socketio.emit('camera_unbound', {'task_id': task_id, 'camera_id': camera_id})
        
        return jsonify({
            'message': '解除绑定成功',
            'task': task_manager.get_task(task_id).to_dict()
        })
    return jsonify({'error': '绑定不存在'}), 404


@task_bp.route('/cameras/<camera_id>/tasks', methods=['GET'])
def get_camera_tasks(camera_id: str):
    """获取摄像机关联的所有任务
    
    Args:
        camera_id: 摄像机 ID（UUID字符串）
    """
    task_manager = get_task_manager()
    tasks = task_manager.get_camera_tasks(camera_id)
    
    return jsonify([task.to_dict() for task in tasks])


# ========================================
# 算法管理 API
# ========================================

@task_bp.route('/algorithms', methods=['GET'])
def get_algorithms():
    """获取可用算法列表"""
    task_manager = get_task_manager()
    algorithms = task_manager.get_algorithms()
    
    return jsonify(algorithms)


# ========================================
# 事件管理 API
# ========================================

@task_bp.route('/events', methods=['GET'])
def get_events():
    """获取事件列表"""
    # 分页参数
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    offset = (page - 1) * per_page
    
    # 筛选参数
    task_id = request.args.get('task_id', type=int)
    camera_id = request.args.get('camera_id')
    algorithm = request.args.get('algorithm')
    class_name = request.args.get('class_name')
    event_type = request.args.get('event_type')
    status = request.args.get('status')

    task_manager = get_task_manager()
    events = task_manager.get_events(
        task_id=task_id,
        camera_id=camera_id,
        algorithm=algorithm,
        event_type=event_type,
        status=status,
        limit=per_page,
        offset=offset
    )

    # 获取总数
    total = task_manager.get_event_count(
        task_id=task_id,
        camera_id=camera_id,
        algorithm=algorithm,
        event_type=event_type,
        status=status
    )
    
    events_dict = [event.to_dict() for event in events]
    
    # 如果有 class_name 筛选，在这里过滤
    if class_name:
        events_dict = [e for e in events_dict if e.get('class_name') == class_name]
        total = len(events_dict)
    
    # 关联查询设备通道名称
    try:
        import sqlite3
        # 通过 JOIN 查询建立 camera_id -> channel_name 的映射
        db_path = channel_pool_manager.db_path if 'channel_pool_manager' in dir() else 'data/tasks.db'
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT b.camera_id, d.channel_name 
                FROM camera_device_channel_bindings b 
                JOIN device_channel_pool d ON b.channel_id = d.id
            ''')
            channel_name_map = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 为每个事件添加 channel_name 字段
        for event_dict in events_dict:
            camera_id_value = event_dict.get('camera_id', '')
            event_dict['channel_name'] = channel_name_map.get(camera_id_value, camera_id_value)
    except Exception as e:
        # 如果查询失败，使用 camera_id 作为显示名称
        for event_dict in events_dict:
            event_dict['channel_name'] = event_dict.get('camera_id', '-')
    
    return jsonify({
        'events': events_dict,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page if per_page > 0 else 1
    })


@task_bp.route('/events/<event_id>', methods=['GET'])
def get_event_detail(event_id):
    """获取单个事件详情"""
    task_manager = get_task_manager()
    
    # event_id 是 UUID 字符串，需要通过数据库查询
    events = task_manager.get_events(limit=1000)
    event = next((e for e in events if e.event_id == event_id), None)
    
    if event:
        event_dict = event.to_dict()
        # 添加 channel_name 字段
        try:
            import sqlite3
            with sqlite3.connect('data/tasks.db') as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT d.channel_name 
                    FROM camera_device_channel_bindings b 
                    JOIN device_channel_pool d ON b.channel_id = d.id
                    WHERE b.camera_id = ?
                ''', (event.camera_id,))
                row = cursor.fetchone()
                if row:
                    event_dict['channel_name'] = row[0]
        except Exception:
            pass
        return jsonify(event_dict)
    return jsonify({'error': '事件不存在'}), 404


@task_bp.route('/events/count', methods=['GET'])
def get_event_count():
    """获取事件数量"""
    task_id = request.args.get('task_id', type=int)
    camera_id = request.args.get('camera_id')
    algorithm = request.args.get('algorithm')
    
    task_manager = get_task_manager()
    count = task_manager.get_event_count(
        task_id=task_id, 
        camera_id=camera_id,
        algorithm=algorithm
    )
    
    return jsonify({'count': count})


@task_bp.route('/events/clear', methods=['POST'])
def clear_old_events():
    """清理过期事件（含存储文件）

    调用 EventCleanupService 统一清理：
    - 数据库事件记录
    - 对应的存储文件（本地图片/GIF、MinIO 对象、FTP 文件）

    查询参数（query string）:
        days: 保留天数（可选，未指定时从 system_config.yaml 的
              events_cleanup.retention_days 读取，单一真相源）
    """
    from src.events.event_cleanup_service import get_cleanup_service
    from src.system.system_config_loader import get_events_cleanup_config

    # days 优先从请求参数读取，未指定时回退到配置文件默认值
    cfg = get_events_cleanup_config()
    days = request.args.get('days', cfg['retention_days'], type=int)

    cleanup_service = get_cleanup_service()
    result = cleanup_service.cleanup_events_by_days(days)

    return jsonify({
        'deleted_records': result['deleted_records'],
        'deleted_files': result['deleted_files'],
        'failed_files': result['failed_files'],
        'total_files': result['total_files'],
        'days': days,
        'message': (
            f"清理了 {result['deleted_records']} 条超过 {days} 天的事件，"
            f"删除 {result['deleted_files']} 个文件"
            f"（失败 {result['failed_files']} 个）"
        )
    })


# ========================================
# 统计信息 API
# ========================================

@task_bp.route('/statistics', methods=['GET'])
def get_statistics():
    """获取系统统计信息"""
    task_manager = get_task_manager()
    stats = task_manager.get_statistics()
    
    return jsonify(stats)


# ========================================
# 系统状态 API
# ========================================

@task_bp.route('/status', methods=['GET'])
def get_status():
    """获取系统状态"""
    task_manager = get_task_manager()
    
    tasks = task_manager.get_all_tasks()
    running_tasks = [t for t in tasks if t.status == 'running']
    enabled_tasks = [t for t in tasks if t.enabled]
    
    algorithms = task_manager.get_algorithms()
    stats = task_manager.get_statistics()
    
    return jsonify({
        'total_tasks': stats['total_tasks'],
        'running_tasks': stats['running_tasks'],
        'enabled_tasks': stats['enabled_tasks'],
        'total_cameras': stats['total_cameras'],
        'enabled_cameras': stats['enabled_cameras'],
        'total_bindings': stats['total_bindings'],
        'total_events': stats['total_events'],
        'available_algorithms': len(algorithms),
        'running_tasks_detail': [t.to_dict() for t in running_tasks]
    })


# ========================================
# Zone配置管理 API（通道级别，存储于 device_channel_pool.zones_config）
# ========================================
# 说明：Zone 配置为通道级数据，与任务无关。
#   - 推荐路由：/api/channels/<channel_id>/zones
#   - 兼容路由：/api/tasks/<task_id>/channels/<channel_id>/zones（task_id 仅做 URL 兼容，内部忽略）

def _validate_zones_config(zones_config):
    """校验 zones_config 格式，返回 (ok, error_message)"""
    if not isinstance(zones_config, list):
        return False, 'zones字段必须是列表'

    for zone in zones_config:
        if not isinstance(zone, dict):
            return False, 'Zone配置必须是字典格式'

        # 检查必需字段
        if 'name' not in zone:
            return False, 'Zone缺少必需字段: name'
        if 'polygon' not in zone:
            return False, f'Zone {zone.get("name", "")} 缺少必需字段: polygon'

        # 验证polygon格式
        polygon = zone['polygon']
        if not isinstance(polygon, list) or len(polygon) < 3:
            return False, f'Zone {zone["name"]} 的polygon必须是至少3个点的列表'

        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return False, f'Zone {zone["name"]} 的polygon点格式错误，应为[x, y]'

        # 可选字段校验：inertia（进入惯性帧数）
        if 'inertia' in zone:
            inertia = zone['inertia']
            if not isinstance(inertia, int) or isinstance(inertia, bool) or inertia < 1 or inertia > 60:
                return False, f'Zone {zone["name"]} 的inertia必须是1-60之间的整数'

        # 可选字段校验：objects（对象类型白名单）
        if 'objects' in zone:
            objects = zone['objects']
            if not isinstance(objects, list) or not all(isinstance(o, str) for o in objects):
                return False, f'Zone {zone["name"]} 的objects必须是字符串列表'

    return True, None


# ---------- 推荐路由：通道级 ----------

@task_bp.route('/channels/<int:channel_id>/zones', methods=['GET'])
def get_channel_zones_by_channel(channel_id: int):
    """获取通道的Zone配置（通道级）"""
    task_manager = get_task_manager()
    zones = task_manager.get_channel_zones(channel_id)
    return jsonify({
        'channel_id': channel_id,
        'zones': zones
    })


@task_bp.route('/channels/<int:channel_id>/zones', methods=['POST'])
def save_channel_zones_by_channel(channel_id: int):
    """保存通道的Zone配置（通道级）"""
    data = request.json
    if not data:
        return jsonify({'error': '请求数据无效'}), 400

    zones_config = data.get('zones', [])
    ok, err = _validate_zones_config(zones_config)
    if not ok:
        return jsonify({'error': err}), 400

    task_manager = get_task_manager()
    success = task_manager.save_channel_zones(channel_id, zones_config)

    if success:
        if _socketio:
            _socketio.emit('zones_updated', {'channel_id': channel_id})
        return jsonify({
            'message': 'Zone配置保存成功',
            'channel_id': channel_id,
            'zones_count': len(zones_config)
        })

    return jsonify({'error': 'Zone配置保存失败'}), 500


@task_bp.route('/channels/<int:channel_id>/zones', methods=['DELETE'])
def delete_channel_zones_by_channel(channel_id: int):
    """删除通道的Zone配置（通道级，重置为空）"""
    task_manager = get_task_manager()
    success = task_manager.delete_channel_zones(channel_id)

    if success:
        if _socketio:
            _socketio.emit('zones_deleted', {'channel_id': channel_id})
        return jsonify({
            'message': 'Zone配置已删除',
            'channel_id': channel_id
        })

    return jsonify({'error': 'Zone配置删除失败'}), 500


# ---------- 兼容路由：保留 task_id 占位（内部忽略，转发到通道级方法） ----------

@task_bp.route('/tasks/<int:task_id>/channels/<int:channel_id>/zones', methods=['GET'])
def get_channel_zones(task_id: int, channel_id: int):
    """获取通道的Zone配置（兼容路由，task_id 忽略）"""
    return get_channel_zones_by_channel(channel_id)


@task_bp.route('/tasks/<int:task_id>/channels/<int:channel_id>/zones', methods=['POST'])
def save_channel_zones(task_id: int, channel_id: int):
    """保存通道的Zone配置（兼容路由，task_id 忽略）"""
    return save_channel_zones_by_channel(channel_id)


@task_bp.route('/tasks/<int:task_id>/channels/<int:channel_id>/zones', methods=['DELETE'])
def delete_channel_zones(task_id: int, channel_id: int):
    """删除通道的Zone配置（兼容路由，task_id 忽略）"""
    return delete_channel_zones_by_channel(channel_id)


# ========================================
# 人员统计计数区域 API（任务级）
# ========================================

@task_bp.route('/tasks/<int:task_id>/counting-zones', methods=['GET'])
def get_counting_zones(task_id: int):
    """获取任务的计数区域配置"""
    task_manager = get_task_manager()
    zones = task_manager.get_counting_zones(task_id)
    return jsonify({'task_id': task_id, 'zones': zones})


@task_bp.route('/tasks/<int:task_id>/counting-zones', methods=['POST'])
def save_counting_zones(task_id: int):
    """保存任务的计数区域配置（清空替换）

    人员统计任务保存计数区域时兜底：
    - event_config.filter.target_classes 含 person（或空=不限），min_confidence<=1.0，
      避免 person 计数事件被 should_send 过滤
    - tracking_enabled=True（分界线跨越需要跟踪轨迹）
    """
    data = request.json
    if not data:
        return jsonify({'error': '请求数据无效'}), 400

    zones = data.get('zones', [])
    task_manager = get_task_manager()

    # 校验任务是否存在及检测类型
    task = task_manager.get_task(task_id)
    if task is None:
        return jsonify({'error': f'任务不存在: task_id={task_id}'}), 404

    success = task_manager.save_counting_zones(task_id, zones)
    if not success:
        return jsonify({'error': '计数区域保存失败'}), 400

    # people_counting 任务兜底事件过滤与跟踪开关，保证计数事件不被过滤
    if task and task.detection_type == 'people_counting' and zones:
        adjusted = False
        update_data = {}
        try:
            event_config = task.event_config or {}
            ec = dict(event_config)
            filt = dict(ec.get('filter') or {})
            target_classes = list(filt.get('target_classes') or [])
            if target_classes and 'person' not in target_classes:
                target_classes.append('person')
                filt['target_classes'] = target_classes
                adjusted = True
            mc = filt.get('min_confidence')
            if mc is None or float(mc) > 1.0:
                filt['min_confidence'] = 1.0
                adjusted = True
            if adjusted:
                ec['filter'] = filt
                update_data['event_config'] = ec
            if not task.tracking_enabled:
                update_data['tracking_enabled'] = True
                adjusted = True
            if update_data:
                task_manager.update_task(task_id, update_data)
        except Exception as e:
            logger.warning(f"人员统计事件过滤兜底配置失败: {e}")

    if _socketio:
        _socketio.emit('counting_zones_updated', {'task_id': task_id})
    return jsonify({
        'message': '计数区域保存成功',
        'task_id': task_id,
        'zones_count': len(zones)
    })


@task_bp.route('/tasks/<int:task_id>/counting-stats', methods=['GET'])
def get_counting_stats(task_id: int):
    """查询任务当日人员统计（区域明细 + 任务总人数）"""
    task_manager = get_task_manager()
    result = task_manager.get_task_counting_total(task_id)
    return jsonify(result)


@task_bp.route('/tasks/<int:task_id>/counting-stats/reset', methods=['POST'])
def reset_counting_stats(task_id: int):
    """手动复位任务当日计数"""
    task_manager = get_task_manager()
    deleted = task_manager.reset_task_counting_day(task_id)
    return jsonify({'message': '当日计数已复位', 'task_id': task_id, 'deleted': deleted})


# ========================================
# 通道快照 API（用于 Zone Canvas 编辑器底图）
# ========================================

@task_bp.route('/channels/<int:channel_id>/snapshot', methods=['GET'])
def get_channel_snapshot(channel_id: int):
    """获取通道的摄像机快照（截取一帧用于 Zone 配置画布底图）

    使用 OpenCV VideoCapture 从 RTSP 流截取一帧，编码为 JPEG 返回。
    包含超时保护（15秒）、打开重试与多帧读取，提升 RTSP 首次连接成功率。

    Query Parameters:
        max_width: 最大宽度（按比例缩放，默认不缩放）
        max_height: 最大高度（按比例缩放，默认不缩放）

    Returns:
        JPEG 图片（Response），响应头包含 X-Snapshot-Width / X-Snapshot-Height
    """
    import threading
    import cv2
    import numpy as np
    from io import BytesIO

    # 获取通道信息
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    channel_pool_manager = get_device_channel_pool_manager()
    channel = channel_pool_manager.get_device_channel(channel_id)

    if not channel:
        return jsonify({'error': '通道不存在'}), 404

    # 获取 RTSP URL（视频文件需要特殊处理）
    rtsp_url = channel.rtsp_url
    from src.cameras.camera_manager import get_camera_manager
    camera_manager = get_camera_manager()

    if channel.camera_id:
        camera_info = camera_manager.get_camera(channel.camera_id)
        if camera_info and camera_info.protocol == 'file':
            source = camera_info.source if hasattr(camera_info, 'source') else ''
            rtsp_url = source.replace('file://', '') if source.startswith('file://') else source

    if not rtsp_url:
        return jsonify({'error': '通道未配置视频流地址'}), 400

    # 截取参数：RTSP 首次连接较慢，给予充足超时与重试
    SNAPSHOT_TIMEOUT = 15.0       # 整体超时（秒）
    OPEN_RETRY_COUNT = 3          # VideoCapture 打开重试次数
    OPEN_RETRY_INTERVAL = 1.0     # 重试间隔（秒）
    READ_FRAME_LIMIT = 10         # 最多读取帧数（丢弃前几帧不稳定画面）

    result = {'frame': None, 'error': None, 'detail': None}

    def _capture():
        cap = None
        try:
            for attempt in range(1, OPEN_RETRY_COUNT + 1):
                cap = cv2.VideoCapture(rtsp_url)
                if cap is None or not cap.isOpened():
                    logger.warning(f"[Snapshot] VideoCapture 打开失败 (尝试 {attempt}/{OPEN_RETRY_COUNT}): {rtsp_url}")
                    if cap is not None:
                        cap.release()
                        cap = None
                    if attempt < OPEN_RETRY_COUNT:
                        import time as _time
                        _time.sleep(OPEN_RETRY_INTERVAL)
                    continue

                # 减少缓冲，尽快拿到最新帧
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

                # 读取多帧，跳过首帧可能的黑帧/损坏帧
                for i in range(READ_FRAME_LIMIT):
                    ret, frame = cap.read()
                    if ret and frame is not None and frame.size > 0:
                        result['frame'] = frame
                        return
                # 本轮打开成功但读不到有效帧
                logger.warning(f"[Snapshot] 打开成功但读取帧失败 (尝试 {attempt}/{OPEN_RETRY_COUNT})")
                cap.release()
                cap = None
                if attempt < OPEN_RETRY_COUNT:
                    import time as _time
                    _time.sleep(OPEN_RETRY_INTERVAL)

            result['error'] = '无法读取视频帧'
            result['detail'] = f'已重试 {OPEN_RETRY_COUNT} 次，请确认视频流地址可达：{rtsp_url}'
        except Exception as e:
            result['error'] = str(e)
            result['detail'] = 'VideoCapture 异常'
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    thread = threading.Thread(target=_capture, daemon=True)
    thread.start()
    thread.join(timeout=SNAPSHOT_TIMEOUT)

    if thread.is_alive():
        # 超时（线程仍为 daemon，进程退出时自动结束）
        logger.error(f"[Snapshot] 获取快照超时({SNAPSHOT_TIMEOUT}s): channel_id={channel_id}, url={rtsp_url}")
        return jsonify({
            'error': f'获取快照超时（{int(SNAPSHOT_TIMEOUT)}秒），请检查视频流是否可用',
            'detail': f'视频流地址: {rtsp_url}'
        }), 504

    if result['error']:
        logger.error(f"[Snapshot] 获取快照失败: channel_id={channel_id}, error={result['error']}, detail={result['detail']}, url={rtsp_url}")
        return jsonify({
            'error': f'获取快照失败: {result["error"]}',
            'detail': result['detail'] or '请检查视频流地址是否正确、设备是否在线'
        }), 500

    frame = result['frame']
    orig_h, orig_w = frame.shape[:2]

    # 可选缩放
    max_width = request.args.get('max_width', type=int)
    max_height = request.args.get('max_height', type=int)

    if max_width and orig_w > max_width:
        scale = max_width / orig_w
        frame = cv2.resize(frame, (max_width, int(orig_h * scale)))
    if max_height and frame.shape[0] > max_height:
        scale = max_height / frame.shape[0]
        frame = cv2.resize(frame, (int(frame.shape[1] * scale), max_height))

    out_h, out_w = frame.shape[:2]

    # 编码为 JPEG
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, 85]
    _, jpeg_buf = cv2.imencode('.jpg', frame, encode_params)

    response = BytesIO(jpeg_buf.tobytes())
    resp = send_file(
        response,
        mimetype='image/jpeg',
        as_attachment=False
    )
    resp.headers['X-Snapshot-Width'] = str(out_w)
    resp.headers['X-Snapshot-Height'] = str(out_h)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


# ========================================
# 健康检查
# ========================================

@task_bp.route('/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})


# ========================================
# 辅助函数
# ========================================

def register_task_blueprint(app, socketio=None):
    """注册任务 Blueprint 到 Flask 应用
    
    Args:
        app: Flask 应用实例
        socketio: SocketIO 实例（可选，用于实时推送）
    """
    app.register_blueprint(task_bp)
    
    # 设置 SocketIO 实例
    if socketio:
        set_socketio(socketio)
    
    # 初始化任务管理器（创建数据库表）
    from src.tasks.task_manager import get_task_manager
    manager = get_task_manager()
    
    logger.debug("[TaskAPI] 任务管理 Blueprint 已注册")
    logger.debug(f"[TaskAPI] 当前任务数量: {len(manager.get_all_tasks())}")