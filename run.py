"""
SageSight AI 检测系统 - 统一主程序

集成Web管理平台和多任务检测进程管理。
提供任务管理、事件查看、可视化监控和检测进程管理功能。
"""

import os
import sys
import time
import json
import signal
import threading
import multiprocessing
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from flask import Flask, render_template, jsonify, request, Response
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

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
    # 从数据库加载设备通道（摄像机）列表
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    
    channel_pool_manager = get_device_channel_pool_manager()
    task_id = task_config.get('id')
    
    # 获取任务关联的所有设备通道
    channels = channel_pool_manager.get_task_device_channels(task_id)
    
    # 将设备通道转换为摄像机配置列表
    cameras = []
    for channel in channels:
        # 优先使用关联的摄像机 ID，如果没有则使用通道 ID 作为降级方案
        effective_camera_id = channel.camera_id if channel.camera_id else str(channel.id)
        
        camera_config = {
            'camera_id': effective_camera_id,
            'channel_id': str(channel.id),  # 保留通道 ID 用于日志和调试
            'camera_name': channel.channel_name,
            'rtsp_url': channel.rtsp_url,
            'channel_source': channel.channel_source
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
# 页面路由
# ========================================

@app.route('/')
def index():
    return render_template('index.html')

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
# 事件管理 API
# ========================================

_events_store = [
    {
        'id': 1,
        'task_id': 1,
        'camera_id': 'camera_001',
        'type': 'detection',
        'class_name': 'person',
        'confidence': 0.92,
        'description': '检测到人员进入监控区域',
        'severity': 'info',
        'acknowledged': False,
        'created_at': datetime.now().isoformat()
    }
]

@app.route('/api/events', methods=['GET'])
def get_events():
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    severity = request.args.get('severity')
    acknowledged = request.args.get('acknowledged')
    
    events = _events_store
    
    if severity:
        events = [e for e in events if e.get('severity') == severity]
    if acknowledged is not None:
        events = [e for e in events if e.get('acknowledged') == (acknowledged.lower() == 'true')]
    
    total = len(events)
    events = events[(page-1)*per_page : page*per_page]
    
    return jsonify({
        'events': events,
        'total': total,
        'page': page,
        'per_page': per_page
    })

@app.route('/api/events/<int:event_id>', methods=['GET'])
def get_event(event_id):
    event = next((e for e in _events_store if e['id'] == event_id), None)
    if event:
        return jsonify(event)
    return jsonify({'error': '事件不存在'}), 404

@app.route('/api/events/<int:event_id>/acknowledge', methods=['POST'])
def acknowledge_event(event_id):
    event = next((e for e in _events_store if e['id'] == event_id), None)
    if event:
        event['acknowledged'] = True
        event['acknowledged_at'] = datetime.now().isoformat()
        socketio.emit('event_acknowledged', {'event_id': event_id}, namespace='/ws')
        return jsonify({'success': True})
    return jsonify({'error': '事件不存在'}), 404


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
        camera_id = data.get('camera_id')
        frame = data.get('frame')
        timestamp = data.get('timestamp')
        
        if not camera_id or not frame:
            return jsonify({'error': '参数不完整'}), 400
        
        # 缓存最新帧
        _ai_stream_cache[camera_id] = {
            'frame': frame,
            'timestamp': timestamp
        }
        
        # 通过 WebSocket 广播给订阅的客户端
        socketio.emit('ai_stream_frame', {
            'camera_id': camera_id,
            'frame': frame,
            'timestamp': timestamp
        }, namespace='/ws')
        
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"推送 AI 视频帧失败: {e}")
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
    
    return jsonify({
        'total_tasks': len(all_tasks),
        'running_tasks': len(running_tasks),
        'running_processes': len(running_process_ids),
        'stopped_tasks': len([t for t in all_tasks if t.status == 'stopped']),
        'error_tasks': len([t for t in all_tasks if t.status == 'error']),
        'total_events': len(_events_store),
        'unacknowledged_events': len([e for e in _events_store if not e.get('acknowledged', False)])
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
    finally:
        logger.info("\n[Main] 正在关闭系统...")

        pm.running = False
        pm.stop_all_tasks()

        # 更新所有任务状态
        for task_id in pm.get_running_tasks():
            tm.update_task(task_id, {'status': 'stopped'})

        logger.info("[Main] 系统已关闭")
        logger.info("="*60)