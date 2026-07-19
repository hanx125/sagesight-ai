"""
SageSight AI 检测系统 - 统一主程序

集成Web管理平台和多任务检测进程管理。
提供任务管理、事件查看、可视化监控和检测进程管理功能。
"""

import os
import sys

# 过滤 Eventlet 废弃警告（必须在导入 eventlet 之前设置）
os.environ['PYTHONWARNINGS'] = 'ignore::DeprecationWarning'
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

import time
import json
import signal
import threading
import multiprocessing
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from flask import Flask, render_template, jsonify, request, Response, redirect, session, url_for
from flask_socketio import SocketIO, emit
import logging

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.tasks.task_manager import TaskManager
from src.tasks.task_api import register_task_blueprint
from src.tasks.device_channel_pool_api import register_device_channel_pool_blueprint
from src.algorithms.algorithm_api import register_algorithm_blueprint
from src.system.system_api import register_system_blueprint
from src.cameras.camera_api import register_camera_blueprint
from src.cameras.stream_api import register_stream_blueprint
from src.cameras.directory_api import register_directory_blueprint
from src.cameras.ptz_api import register_ptz_api
from src.cameras.audio_talk_api import init_audio_talk_api

# 配置日志（从system_config.yaml加载，支持文件输出和轮转）
import yaml
from logging.handlers import RotatingFileHandler
from pathlib import Path

_log_config_path = Path(__file__).parent / 'config' / 'system_config.yaml'
_log_cfg = {}
if _log_config_path.exists():
    with open(_log_config_path, 'r', encoding='utf-8') as _f:
        _full_cfg = yaml.safe_load(_f) or {}
    _log_cfg = _full_cfg.get('logging', {})

_log_level = getattr(logging, _log_cfg.get('level', 'INFO').upper(), logging.INFO)
_log_format = _log_cfg.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_log_file = _log_cfg.get('file', 'logs/system.log')
_log_max_size = int(_log_cfg.get('max_size', 10)) * 1024 * 1024
_log_backup_count = int(_log_cfg.get('backup_count', 5))
_log_console = _log_cfg.get('console', True)

# 确保日志目录存在
_log_path = Path(_log_file)
_log_path.parent.mkdir(parents=True, exist_ok=True)

# 配置根logger
_root_logger = logging.getLogger()
_root_logger.setLevel(_log_level)

# 清除默认handler
_root_logger.handlers.clear()

# 文件handler（带轮转）
_file_handler = RotatingFileHandler(
    _log_file, maxBytes=_log_max_size, backupCount=_log_backup_count, encoding='utf-8'
)
_file_handler.setLevel(_log_level)
_file_handler.setFormatter(logging.Formatter(_log_format))
_root_logger.addHandler(_file_handler)

# 控制台handler
if _log_console:
    _console_handler = logging.StreamHandler()
    _console_handler.setLevel(_log_level)
    _console_handler.setFormatter(logging.Formatter(_log_format))
    _root_logger.addHandler(_console_handler)

logger = logging.getLogger(__name__)
logger.info(f"日志系统初始化完成: file={_log_file}, level={_log_cfg.get('level', 'INFO')}, max_size={_log_cfg.get('max_size', 10)}MB")


# ========================================
# 任务进程管理器（重构后）
# ========================================

# ========================================
# 模块级别的任务执行函数（用于multiprocessing）
# ========================================

def _run_executor_process(task_config: Dict):
    """运行任务执行器（在独立进程中）

    Args:
        task_config: 任务配置字典

    Note:
        此函数必须在模块级别定义，以便multiprocessing能够pickle
    """
    # 禁用 stdout/stderr 缓冲（确保日志立即输出）
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    # 从数据库加载设备通道（摄像机）列表
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    
    channel_pool_manager = get_device_channel_pool_manager()
    task_id = task_config.get('id')
    
    # 获取任务关联的所有设备通道
    channels = channel_pool_manager.get_task_device_channels(task_id)

    # 获取摄像机管理器（用于查询protocol字段）
    from src.cameras.camera_manager import get_camera_manager
    camera_manager = get_camera_manager()

    # 将设备通道转换为摄像机配置列表
    cameras = []
    for channel in channels:
        # 优先使用关联的摄像机 ID，如果没有则使用通道 ID 作为降级方案
        effective_camera_id = channel.camera_id if channel.camera_id else str(channel.id)

        # 从摄像机表获取protocol字段（用于判断视频文件）
        camera_info = camera_manager.get_camera(effective_camera_id)
        protocol = camera_info.protocol if camera_info else 'rtsp'

        # 获取视频流地址
        rtsp_url = channel.rtsp_url
        if protocol == 'file' and camera_info:
            # 视频文件：从cameras表获取source字段，并去除file://前缀
            source = camera_info.source if hasattr(camera_info, 'source') else ''
            rtsp_url = source.replace('file://', '') if source.startswith('file://') else source

        camera_config = {
            'camera_id': effective_camera_id,
            'channel_id': str(channel.id),  # 保留通道 ID 用于日志和调试
            'camera_name': channel.channel_name,
            'rtsp_url': rtsp_url,  # 使用处理后的路径
            'channel_source': channel.channel_source,
            'protocol': protocol  # 添加protocol字段（视频文件需要）
        }
        cameras.append(camera_config)
    
    # 创建任务执行器并运行（run()方法包含主循环）
    from src.tasks.task_executor import MultiCameraTaskExecutor
    executor = MultiCameraTaskExecutor(task_config, cameras)
    executor.run()  # 运行任务执行器（阻塞，直到任务停止）


class TaskProcessManager:
    """任务进程管理器 - 使用 multiprocessing 管理 task_executor"""

    def __init__(self):
        """初始化进程管理器"""
        self.task_processes: Dict[int, multiprocessing.Process] = {}
        self.running = False

        logger.info("[TaskProcessManager] 初始化完成（multiprocessing 模式）")

    def start_task(self, task_config: Dict) -> bool:
        """
        启动任务进程

        Args:
            task_config: 任务配置字典

        Returns:
            是否启动成功
        """
        task_id = task_config.get('id')
        task_name = task_config.get('task_name', 'unknown')

        if task_id in self.task_processes:
            logger.warning(f"[TaskProcessManager] 任务 {task_name} 已在运行")
            return False

        try:
            # 使用 multiprocessing 启动 task_executor（使用模块级别的函数）
            process = multiprocessing.Process(
                target=_run_executor_process,
                args=(task_config,),
                daemon=True,
                name=f"Task-{task_name}"
            )

            process.start()
            self.task_processes[task_id] = process

            logger.info(f"[TaskProcessManager] 任务 {task_name} 已启动 (PID: {process.pid})")

            return True

        except Exception as e:
            logger.error(f"[TaskProcessManager] 启动任务 {task_name} 失败: {e}")
            return False

    def stop_task(self, task_id: int) -> bool:
        """
        停止任务进程

        Args:
            task_id: 任务 ID

        Returns:
            是否停止成功
        """
        if task_id not in self.task_processes:
            return False

        process = self.task_processes[task_id]

        try:
            # 发送终止信号
            process.terminate()

            # 等待进程退出（最多 5 秒）
            process.join(timeout=5)

            # 如果进程仍然存活，强制终止
            if process.is_alive():
                process.kill()
                process.join()

            del self.task_processes[task_id]
            logger.info(f"[TaskProcessManager] 任务 ID={task_id} 已停止")

            return True

        except Exception as e:
            logger.error(f"[TaskProcessManager] 停止任务失败: {e}")
            return False

    def stop_all_tasks(self):
        """停止所有任务"""
        logger.info("[TaskProcessManager] 正在停止所有任务...")

        for task_id in list(self.task_processes.keys()):
            self.stop_task(task_id)

        logger.info("[TaskProcessManager] 所有任务已停止")

    def check_processes(self) -> List[int]:
        """
        检查进程状态

        Returns:
            异常退出的任务 ID 列表
        """
        crashed = []

        for task_id, process in list(self.task_processes.items()):
            if not process.is_alive():
                # 进程已退出
                crashed.append(task_id)
                del self.task_processes[task_id]

        return crashed

    def get_running_tasks(self) -> List[int]:
        """获取正在运行的任务 ID"""
        return list(self.task_processes.keys())

    def get_process_info(self, task_id: int) -> Dict:
        """获取进程信息"""
        if task_id not in self.task_processes:
            return {}

        process = self.task_processes[task_id]
        return {
            'pid': process.pid,
            'running': process.is_alive(),
            'name': process.name
        }


# ========================================
# Flask应用初始化
# ========================================

# 创建Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'sagesight-detection-platform'
app.config['JSON_AS_ASCII'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['UPLOAD_FOLDER'] = 'models'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

# 初始化SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 注册Blueprint
register_algorithm_blueprint(app, socketio)
register_task_blueprint(app, socketio)
register_device_channel_pool_blueprint(app, socketio)
app.register_blueprint(register_system_blueprint(socketio))
register_camera_blueprint(app, socketio)
init_audio_talk_api(app, socketio)
register_stream_blueprint(app)
register_directory_blueprint(app)
register_ptz_api(app)

# 注册认证模块
from src.auth.auth_api import register_auth_blueprint, login_required, get_current_user
register_auth_blueprint(app)

# 会话配置
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.permanent_session_lifetime = 86400  # 24小时

# ========================================
# 静态文件路由 - 提供output目录访问
# ========================================
from flask import send_from_directory

@app.route('/output/images/<path:filename>')
def serve_output_image(filename):
    """提供output/images目录下的图片访问"""
    output_dir = os.path.join(os.path.dirname(__file__), 'output', 'images')
    return send_from_directory(output_dir, filename)

@app.route('/output/<path:filename>')
def serve_output_file(filename):
    """提供output目录下的文件访问"""
    output_dir = os.path.join(os.path.dirname(__file__), 'output')
    return send_from_directory(output_dir, filename)

@app.route('/uploads/<path:filename>')
def serve_upload_file(filename):
    """提供uploads目录下的文件访问（视频文件等）"""
    uploads_dir = os.path.join(os.path.dirname(__file__), 'uploads')
    return send_from_directory(uploads_dir, filename)

# 全局实例
task_manager = None
process_manager = None


def get_task_manager():
    """获取任务管理器实例"""
    global task_manager
    if task_manager is None:
        task_manager = TaskManager()
    return task_manager


def get_process_manager():
    """获取进程管理器实例"""
    global process_manager
    if process_manager is None:
        process_manager = TaskProcessManager()
    return process_manager


# ========================================
# 认证中间件 - 未登录重定向到登录页
# ========================================

# 不需要认证的路径前缀
PUBLIC_PATHS = ('/login', '/api/auth/', '/api/ai-stream/', '/api/events/push', '/static/', '/output/')

@app.before_request
def check_auth():
    """全局认证检查"""
    # 静态资源、登录页、认证API不需要检查
    if request.path.startswith(PUBLIC_PATHS):
        return None
    
    # 已登录则放行
    if 'user_id' in session:
        return None
    
    # API请求返回401
    if request.path.startswith('/api/'):
        return jsonify({'error': '未登录', 'code': 'UNAUTHORIZED'}), 401
    
    # 页面请求重定向到登录页
    return redirect('/login')


# ========================================
# 页面路由
# ========================================

@app.route('/login')
def login_page():
    """登录页面"""
    if 'user_id' in session:
        return redirect('/')
    return render_template('login.html')

@app.route('/')
def index():
    return render_template('monitor.html')

@app.route('/tasks')
def tasks_page():
    return render_template('tasks.html')

@app.route('/cameras')
def cameras_page():
    return render_template('cameras.html')

@app.route('/events')
def events_page():
    return render_template('events.html')

@app.route('/monitor')
def monitor_page():
    return render_template('monitor.html')

@app.route('/algorithms')
def algorithms_page():
    return render_template('algorithms.html')

@app.route('/system')
def system_page():
    return render_template('system.html')


# ========================================
# 任务管理 API（已迁移到 task_api.py）
# ========================================
# 注意：所有任务管理API已迁移到 src/tasks/task_api.py
# 通过 register_task_blueprint(app, socketio) 注册蓝图
# 旧的路由定义已删除，避免覆盖蓝图的正确实现
#
# task_api.py 已正确实现配置优先级：
# - decoder_config: 前端传入 > 配置文件默认值
# - detector_config: 前端传入 > 配置文件默认值
# - tracker_config: 前端传入 > 配置文件默认值
# - motion_config: 前端传入 > 配置文件默认值
# - event_config: 前端传入 > 配置文件默认值
# - display_config: 前端传入 > 配置文件默认值

# ========================================
# 事件管理 API（主要路由在 task_api.py 蓝图中）
# ========================================
# 注意：/api/events 路由已在 task_api.py 蓝图中定义
# 这里只定义额外的事件相关路由

@app.route('/api/events/stats', methods=['GET'])
def get_event_stats():
    """获取事件统计"""
    tm = get_task_manager()
    
    total = tm.get_event_count()
    
    # 按类型统计
    import sqlite3
    with sqlite3.connect(tm.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT type, COUNT(*) FROM events GROUP BY type')
        type_stats = dict(cursor.fetchall())
        
        cursor.execute('SELECT class_name, COUNT(*) FROM events GROUP BY class_name ORDER BY COUNT(*) DESC LIMIT 10')
        class_stats = dict(cursor.fetchall())
        
        cursor.execute('SELECT DATE(created_at) as date, COUNT(*) FROM events GROUP BY DATE(created_at) ORDER BY date DESC LIMIT 7')
        daily_stats = [{'date': row[0], 'count': row[1]} for row in cursor.fetchall()]
    
    return jsonify({
        'total': total,
        'by_type': type_stats,
        'by_class': class_stats,
        'daily': daily_stats
    })


# ========================================
# 监控 API
# ========================================

@app.route('/api/streams/<int:task_id>')
def get_stream(task_id):
    tm = get_task_manager()
    task = tm.get_task(task_id)
    if task:
        return jsonify({
            'task_id': task_id,
            'stream_url': task.rtsp_url,
            'status': task.status
        })
    return jsonify({'error': '任务不存在'}), 404

@app.route('/api/streams/<int:task_id>/status')
def get_stream_status(task_id):
    tm = get_task_manager()
    task = tm.get_task(task_id)
    if task:
        return jsonify({
            'task_id': task_id,
            'status': task.status,
            'camera_name': task.camera_name,
            'enabled': task.enabled
        })
    return jsonify({'error': '任务不存在'}), 404


# ========================================
# AI 视频流 API
# ========================================

# AI 视频帧缓存（最近帧）
_ai_stream_cache = {}  # {camera_id: {'frame': base64, 'timestamp': str}}

@app.route('/api/ai-stream/push', methods=['POST'])
def push_ai_stream_frame():
    """接收任务进程推送的 AI 视频帧，并通过 WebSocket 广播"""
    try:
        data = request.get_json()
        task_id = data.get('task_id')
        camera_id = data.get('camera_id')
        frame = data.get('frame')
        timestamp = data.get('timestamp')

        if not camera_id or not frame:
            return jsonify({'error': '参数不完整'}), 400

        # 缓存最新帧
        _ai_stream_cache[camera_id] = {
            'frame': frame,
            'timestamp': timestamp,
            'task_id': task_id
        }

        # 通过 WebSocket 广播给订阅的客户端
        socketio.emit('ai_stream_frame', {
            'task_id': task_id,
            'camera_id': camera_id,
            'frame': frame,
            'timestamp': timestamp
        }, namespace='/ws')

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"推送 AI 视频帧失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/events/push', methods=['POST'])
def push_event_to_websocket():
    """接收任务进程推送的事件数据，并通过 WebSocket 广播到驾驶舱"""
    try:
        data = request.get_json()
        if not data or 'event_id' not in data:
            return jsonify({'error': '参数不完整'}), 400
        
        # 通过 WebSocket 广播到驾驶舱
        socketio.emit('new_event', data, namespace='/ws')
        logger.debug(f"事件已推送到驾驶舱: {data.get('event_id', '')[:8]}... type={data.get('type', '')}")
        
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"推送事件到驾驶舱失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai-stream/enable/<camera_id>', methods=['POST'])
def enable_ai_stream(camera_id):
    """启用摄像机的 AI 视频流"""
    try:
        # AI 流配置已在 display_config.yaml 中启用（ai_stream_enabled: true）
        # 任务进程启动时会自动读取该配置
        # 此 API 仅用于前端状态同步，实际无需额外操作
        
        return jsonify({'success': True, 'message': 'AI 视频流已启用'})
        
    except Exception as e:
        logger.error(f"启用 AI 视频流失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai-stream/disable/<camera_id>', methods=['POST'])
def disable_ai_stream(camera_id):
    """禁用摄像机的 AI 视频流"""
    try:
        # 清除缓存
        _ai_stream_cache.pop(camera_id, None)

        return jsonify({'success': True, 'message': 'AI 视频流已禁用'})

    except Exception as e:
        logger.error(f"禁用 AI 视频流失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai-stream/frame/<camera_id>', methods=['GET'])
def get_ai_stream_frame(camera_id):
    """获取摄像机的最新 AI 视频帧（用于视频文件播放）"""
    try:
        # 从缓存获取最新帧
        frame_data = _ai_stream_cache.get(camera_id)

        if not frame_data:
            return jsonify({'error': '没有可用的帧数据'}), 404

        return jsonify({
            'success': True,
            'camera_id': camera_id,
            'frame': frame_data.get('frame'),
            'timestamp': frame_data.get('timestamp')
        })

    except Exception as e:
        logger.error(f"获取 AI 视频帧失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# WebSocket 事件处理
# ========================================

@socketio.on('connect', namespace='/ws')
def handle_connect():
    logger.info('客户端已连接')
    emit('connected', {'message': '已连接到实时推送服务'})

@socketio.on('disconnect', namespace='/ws')
def handle_disconnect():
    logger.info('客户端已断开')

@socketio.on('subscribe', namespace='/ws')
def handle_subscribe(data):
    task_ids = data.get('task_ids', [])
    logger.info('客户端订阅任务: %s', task_ids)
    emit('subscribed', {'task_ids': task_ids})


# ========================================
# 统计 API
# ========================================

@app.route('/api/stats')
def get_stats():
    tm = get_task_manager()
    pm = get_process_manager()
    
    all_tasks = tm.get_all_tasks()
    running_tasks = [t for t in all_tasks if t.status == 'running']
    running_process_ids = pm.get_running_tasks()
    
    # 从数据库获取事件统计
    total_events = tm.get_event_count()
    
    return jsonify({
        'total_tasks': len(all_tasks),
        'running_tasks': len(running_tasks),
        'running_processes': len(running_process_ids),
        'stopped_tasks': len([t for t in all_tasks if t.status == 'stopped']),
        'error_tasks': len([t for t in all_tasks if t.status == 'error']),
        'total_events': total_events
    })


# ========================================
# 任务监控线程
# ========================================

def task_monitor_thread():
    """任务监控线程
    
    负责监控任务进程状态，并在异常退出时重启。
    注意：只会重启真正异常退出的任务，不会重启用户主动停止的任务。
    """
    tm = get_task_manager()
    pm = get_process_manager()
    
    logger.info("[TaskMonitor] 监控线程启动")
    
    while pm.running:
        try:
            # 检查进程状态
            crashed = pm.check_processes()
            
            # ========================================
            # 重启异常退出的任务（智能判断）
            # ========================================
            for task_id in crashed:
                task = tm.get_task(task_id)
                if task and task.enabled:
                    # ✓ 检查任务状态是否为 'running'
                    # 如果用户主动停止任务，状态会被设置为 'stopped'
                    # 只有状态仍然是 'running' 的任务才需要重启
                    if task.status == 'running':
                        logger.warning(f"[TaskMonitor] 任务 {task.task_name} 异常退出，尝试重启...")
                        tm.update_task(task_id, {'status': 'error'})
                        
                        time.sleep(5)
                        
                        task_config = task.to_dict()
                        if pm.start_task(task_config):
                            tm.update_task(task_id, {'status': 'running'})
                    else:
                        # 任务状态不是 'running'，说明用户主动停止，不需要重启
                        logger.info(f"[TaskMonitor] 任务 {task.task_name} 已停止（状态={task.status}），不自动重启")
            
            # ========================================
            # 检查数据库中的任务状态变化
            # ========================================
            tasks = tm.get_all_tasks(enabled_only=True)
            running_ids = pm.get_running_tasks()
            
            # 启动新任务（只启动状态为 'running' 且没有进程的任务）
            for task in tasks:
                if task.status == 'running' and task.id not in running_ids:
                    # ✓ 启动任务前再次检查状态，避免重复启动
                    current_task = tm.get_task(task.id)
                    if current_task.status == 'running':
                        logger.info(f"[TaskMonitor] 启动任务: {task.task_name}")
                        task_config = task.to_dict()
                        pm.start_task(task_config)
            
            # 停止不需要的任务
            for task_id in running_ids:
                task = tm.get_task(task_id)
                # ✓ 只停止状态不是 'running' 或 enabled=False 的任务
                if not task or task.status != 'running' or not task.enabled:
                    logger.info(f"[TaskMonitor] 停止任务: {task.task_name if task else 'unknown'}")
                    pm.stop_task(task_id)
                    # ✓ 更新数据库状态为 'stopped'
                    if task:
                        tm.update_task(task_id, {'status': 'stopped'})
            
            # 定期打印状态
            running_count = len(running_ids)
            if running_count > 0:
                logger.info(f"[TaskMonitor] 运行中任务进程: {running_count}")
            
            time.sleep(2)
        
        except Exception as e:
            logger.error(f"[TaskMonitor] 监控错误: {e}")
            time.sleep(5)
    
    logger.info("[TaskMonitor] 监控线程退出")


# ========================================
# 启动应用
# ========================================

if __name__ == '__main__':
    logger.info("="*60)
    logger.info("SageSight AI 检测系统 - 统一主程序")
    logger.info("="*60)
    logger.info(f"启动时间: {datetime.now().isoformat()}")
    logger.info("="*60)
    
    # 初始化进程管理器
    pm = get_process_manager()
    
    # 初始化任务管理器
    tm = get_task_manager()
    
    # ========================================
    # 注册信号处理（确保Ctrl+C时正确清理）
    # ========================================
    import atexit
    
    def cleanup_handler():
        """清理处理函数（atexit注册）"""
        logger.info("\n[Cleanup] 正在清理系统资源...")
        
        pm.running = False
        
        # 停止所有任务进程
        try:
            pm.stop_all_tasks()
        except Exception as e:
            logger.error(f"[Cleanup] 停止任务进程失败: {e}")
        
        # 更新数据库状态
        try:
            running_ids = pm.get_running_tasks()
            for task_id in running_ids:
                tm.update_task(task_id, {'status': 'stopped'})
        except Exception as e:
            logger.error(f"[Cleanup] 更新任务状态失败: {e}")
        
        logger.info("[Cleanup] 系统清理完成")
    
    # 注册清理函数（atexit会在Python退出时调用，比finally更可靠）
    atexit.register(cleanup_handler)
    
    # 注册信号处理（Windows平台）
    def signal_handler(signum, frame):
        """信号处理函数"""
        logger.info(f"\n[Signal] 收到信号 {signum}，正在关闭系统...")
        cleanup_handler()
        sys.exit(0)
    
    # 注册SIGINT（Ctrl+C）和SIGTERM信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 加载所有启用的任务并启动
    tasks = tm.get_all_tasks(enabled_only=True)
    logger.info(f"\n[Main] 发现 {len(tasks)} 个启用的任务")
    
    for task in tasks:
        if task.status == 'running':
            task_config = task.to_dict()
            pm.start_task(task_config)
            logger.info(f"[Main] 启动任务: {task.task_name} (Algorithm: {task.algorithm})")
    
    # 启动任务监控线程
    pm.running = True
    monitor_thread = threading.Thread(target=task_monitor_thread, daemon=True)
    monitor_thread.start()

    logger.info("\n[Main] Web管理平台访问地址: http://localhost:5555")
    logger.info("  - 任务管理: http://localhost:5555/tasks")
    logger.info("  - 摄像机管理: http://localhost:5555/cameras")
    logger.info("  - 事件列表: http://localhost:5555/events")
    logger.info("  - 可视化监控: http://localhost:5555/monitor")
    logger.info("  - 算法库: http://localhost:5555/algorithms")
    logger.info("  - 系统管理: http://localhost:5555/system")
    logger.info("="*60)
    
    # 运行Flask应用（前台）
    try:
        socketio.run(app, host='0.0.0.0', port=5555, debug=False)
    except KeyboardInterrupt:
        logger.info("\n[Main] 用户按下Ctrl+C，正在关闭...")
    except Exception as e:
        logger.error(f"\n[Main] 应用异常退出: {e}")
    finally:
        # 调用清理函数（双重保险）
        cleanup_handler()
        logger.info("[Main] 系统已关闭")
        logger.info("="*60)