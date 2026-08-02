"""SageSight AI 检测系统 - 统一主程序

集成Web管理平台和多任务检测进程管理。
提供任务管理、事件查看、可视化监控和检测进程管理功能。
"""

import os
import sys

# 抑制 eventlet hub 的连接异常打印（客户端断开/超时导致的 ConnectionAbortedError 等）
try:
    import eventlet.debug
    eventlet.debug.hub_exceptions(False)
except Exception:
    pass

# ✅ 设置多进程启动方法为 'spawn'（Windows 默认，确保子进程正确继承）
import multiprocessing
try:
    multiprocessing.set_start_method('spawn', force=True)
except RuntimeError:
    pass  # 已经设置过，忽略

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
from eventlet import websocket
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
from src.utils.process import Process

# 配置日志（从system_config.yaml加载，统一通过 LogManager 管理）
import yaml
from pathlib import Path
from src.utils.log_manager import LogManager

_log_config_path = Path(__file__).parent / 'config' / 'system_config.yaml'
_log_cfg = {}
if _log_config_path.exists():
    with open(_log_config_path, 'r', encoding='utf-8') as _f:
        _full_cfg = yaml.safe_load(_f) or {}
    _log_cfg = _full_cfg.get('logging', {})

# 主进程日志配置（root logger + QueueListener）
LogManager.get_instance().setup_main_process(_log_cfg)
logger = logging.getLogger(__name__)


# ========================================
# 任务进程管理器（重构后）
# ========================================

# ========================================
# 模块级别的任务执行函数（用于multiprocessing）
# ========================================

def _run_executor_process(task_config: Dict, ai_stream_enabled_state=None):
    """运行任务执行器（在独立进程中）

    Args:
        task_config: 任务配置字典
        ai_stream_enabled_state: 已废弃（保留参数兼容旧调用），AI 流状态改用文件标记

    Note:
        此函数必须在模块级别定义，以便multiprocessing能够pickle
    """
    # 禁用 stdout/stderr 缓冲（确保日志立即输出）
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    # 从数据库加载设备通道（摄像机）列表
    from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
    from src.tasks.task_manager import TaskManager

    channel_pool_manager = get_device_channel_pool_manager()
    task_manager = TaskManager()  # 子进程中的本地实例（用于读取通道级 Zone 配置）
    task_id = task_config.get('id')
    
    # 获取任务关联的所有设备通道
    channels = channel_pool_manager.get_task_device_channels(task_id)

    # ✅ 如果任务没有关联的设备通道，优雅退出（不抛异常）
    if not channels:
        logger.debug(f"[MultiCameraExecutor-{task_id}] 任务没有关联的摄像机通道，跳过启动")
        logger.debug(f"[MultiCameraExecutor-{task_id}] 请先在任务配置中添加设备通道")
        # 更新任务状态为 stopped，避免监控线程反复重启
        try:
            task_manager.update_task(task_id, {'status': 'stopped', 'enabled': False})
        except Exception:
            pass
        return

    # 获取摄像机管理器（用于查询protocol字段）
    from src.cameras.camera_manager import get_camera_manager
    camera_manager = get_camera_manager()

    # 将设备通道转换为摄像机配置列表
    cameras = []
    for channel in channels:
        # 优先使用关联的摄像机 ID，如果没有或为空值则使用通道 ID 作为降级方案
        # 注意：空列表 [] 在 Python 中是 truthy，需要显式检查
        raw_camera_id = channel.camera_id
        if raw_camera_id is None or (isinstance(raw_camera_id, str) and not raw_camera_id.strip()) or (isinstance(raw_camera_id, (list, dict)) and not raw_camera_id):
            effective_camera_id = str(channel.id)
        else:
            effective_camera_id = str(raw_camera_id)

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
            'protocol': protocol,  # 添加protocol字段（视频文件需要）
            'zones_config': task_manager.get_channel_zones(channel.id),  # 通道级 Zone 配置（device_channel_pool.zones_config）
        }
        cameras.append(camera_config)
    
    # 创建任务执行器并运行（run()方法包含主循环）
    from src.tasks.task_executor import MultiCameraTaskExecutor
    executor = MultiCameraTaskExecutor(task_config, cameras, ai_stream_enabled_state=ai_stream_enabled_state)
    executor.run()  # 运行任务执行器（阻塞，直到任务停止）


class TaskProcessManager:
    """任务进程管理器 - 使用 multiprocessing 管理 task_executor"""

    def __init__(self):
        """初始化进程管理器"""
        self.task_processes: Dict[int, multiprocessing.Process] = {}
        self.task_cameras: Dict[int, list] = {}  # {task_id: [camera_id, ...]} 记录任务关联的摄像机
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
            # 记录任务关联的摄像机ID（用于 stop_task 时清理 AI 流状态）
            # 注意：使用与 _run_executor_process 相同的降级逻辑获取 effective_camera_id
            try:
                from src.tasks.device_channel_pool_manager import get_device_channel_pool_manager
                _channel_pool_mgr = get_device_channel_pool_manager()
                _channels = _channel_pool_mgr.get_task_device_channels(task_id)
                _camera_ids = []
                for _ch in _channels:
                    _raw_id = getattr(_ch, 'camera_id', None)
                    # 空列表/空字典/空字符串/None 均视为无效，降级为通道ID
                    if (_raw_id is None or
                        (isinstance(_raw_id, str) and not _raw_id.strip()) or
                        (isinstance(_raw_id, (list, dict)) and not _raw_id)):
                        _camera_ids.append(str(_ch.id))
                    else:
                        _camera_ids.append(str(_raw_id))
                self.task_cameras[task_id] = _camera_ids
            except Exception as e:
                logger.warning(f"[TaskProcessManager] 获取任务 {task_id} 摄像机列表失败: {e}")
                self.task_cameras[task_id] = []

            # 使用 Process 类启动 task_executor（自动配置日志队列）
            # 注意：设置为非守护进程（daemon=False），因为任务可能需要创建子进程
            # 例如：检测进程池（DetectionProcessPool）需要创建 Manager 子进程
            # AI 流状态改用文件标记管理（data/ai_stream_flags/），不再传递 Manager dict
            process = Process(
                target=_run_executor_process,
                args=(task_config,),
                daemon=False,  # 非守护进程，允许创建子进程
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
            # ✅ 关键修复：杀死进程树（包括所有子进程）
            # 因为任务进程会创建子进程（检测进程池、CameraWorker等）
            import psutil
            import os

            try:
                # 获取进程对象
                proc = psutil.Process(process.pid)

                # 获取所有子进程（递归）
                children = proc.children(recursive=True)
                logger.info(f"[TaskProcessManager] 任务 {task_id} 有 {len(children)} 个子进程")

                # 先终止子进程（避免僵尸进程）
                for child in children:
                    try:
                        child.terminate()
                    except:
                        pass

                # 等待子进程退出
                gone, alive = psutil.wait_procs(children, timeout=3)

                # 强制杀死仍然存活的子进程
                for child in alive:
                    try:
                        child.kill()
                    except:
                        pass

            except Exception as e:
                logger.warning(f"[TaskProcessManager] 清理子进程失败: {e}")

            # 发送终止信号给主进程
            process.terminate()

            # 等待进程退出（最多 5 秒）
            process.join(timeout=5)

            # 如果进程仍然存活，强制终止
            if process.is_alive():
                logger.warning(f"[TaskProcessManager] 任务 {task_id} 未响应终止信号，强制杀死")
                process.kill()
                process.join(timeout=2)

                # ✅ 如果仍然存活，记录警告
                if process.is_alive():
                    logger.error(f"[TaskProcessManager] 任务 {task_id} 无法被杀死（僵尸进程）")

            del self.task_processes[task_id]

            # 清理 AI 流推送标记文件（防止任务重启后旧标记残留导致推流异常）
            camera_ids = self.task_cameras.pop(task_id, [])
            if camera_ids:
                _clear_ai_stream_flags(camera_ids)
                for camera_id in camera_ids:
                    _ai_stream_cache.pop(camera_id, None)
                logger.info(f"[TaskProcessManager] 已清理任务 {task_id} 的 AI 流标记: cameras={camera_ids}")

            logger.info(f"[TaskProcessManager] 任务 ID={task_id} 已停止")

            return True

        except Exception as e:
            logger.error(f"[TaskProcessManager] 停止任务失败: {e}")
            return False

    def stop_all_tasks(self):
        """停止所有任务"""
        logger.info("[TaskProcessManager] 正在停止所有任务...")

        task_ids = list(self.task_processes.keys())
        logger.info(f"[TaskProcessManager] 需要停止 {len(task_ids)} 个任务")

        for task_id in task_ids:
            try:
                self.stop_task(task_id)
            except Exception as e:
                logger.error(f"[TaskProcessManager] 停止任务 {task_id} 失败: {e}")

        # ✅ 强制清理所有残留的 Python 子进程（通过进程名匹配）
        try:
            import psutil
            current_pid = os.getpid()
            parent = psutil.Process(current_pid)

            # 查找所有子进程
            children = parent.children(recursive=True)
            logger.info(f"[TaskProcessManager] 发现 {len(children)} 个子进程")

            for child in children:
                try:
                    if child.name() == 'python.exe':
                        logger.warning(f"[TaskProcessManager] 强制终止子进程 PID={child.pid}")
                        child.kill()
                except Exception as e:
                    logger.error(f"[TaskProcessManager] 终止子进程 PID={child.pid} 失败: {e}")

        except Exception as e:
            logger.error(f"[TaskProcessManager] 清理子进程失败: {e}")

        self.task_processes.clear()
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

# ========================================
# 原生 WebSocket 端点（/ws/raw，供第三方系统标准 WebSocket 接入）
# ========================================
# 双通道方案：保留 Socket.IO（前端用）+ 新增原生 WebSocket（第三方用）
# 第三方连接 ws://<host>:5556/ws/raw，接收 JSON 格式消息：{"event": "...", "data": {...}}

class WebSocketManager:
    """原生 WebSocket 连接管理器（给第三方接入用）"""

    def __init__(self):
        self._clients = set()

    def add(self, ws):
        self._clients.add(ws)

    def remove(self, ws):
        self._clients.discard(ws)

    def broadcast(self, event, data):
        """向所有原生 WebSocket 客户端广播 {event, data} JSON 消息"""
        if not self._clients:
            return
        message = json.dumps({'event': event, 'data': data}, ensure_ascii=False)
        for ws in list(self._clients):
            try:
                ws.send(message)
            except Exception:
                self._clients.discard(ws)


ws_manager = WebSocketManager()


@websocket.WebSocketWSGI
def _ws_raw_handler(ws):
    """原生 WebSocket 端点 /ws/raw 的处理函数"""
    ws_manager.add(ws)
    try:
        ws.send(json.dumps({'event': 'connected', 'data': {'message': '已连接到SageSight实时推送服务'}},
                           ensure_ascii=False))
        while True:
            # 等待客户端消息（第三方一般只接收，这里保持连接直到断开）
            msg = ws.wait()
            if msg is None:
                break
    except Exception:
        pass
    finally:
        ws_manager.remove(ws)


# WSGI 中间件：分发 /ws/raw 到原生 WebSocket，其他路径交给 socketio
_socketio_wsgi = app.wsgi_app


def _dispatching_wsgi(environ, start_response):
    if environ.get('PATH_INFO') == '/ws/raw':
        return _ws_raw_handler(environ, start_response)
    return _socketio_wsgi(environ, start_response)


app.wsgi_app = _dispatching_wsgi

# ========================================
# AI 视频流推送状态（文件标记方案）
# ========================================
# 使用文件标记替代 multiprocessing.Manager dict，避免 Windows spawn 模式下
# Manager dict 代理经过两层进程 pickle（主进程→任务进程→TrackedObjectProcessor进程）
# 后失效的问题。
#
# 原理：
# - 前端点击 AI 播放 → /api/ai-stream/enable → 创建标记文件 data/ai_stream_flags/{camera_id}.flag
# - 前端点击停止 → /api/ai-stream/disable → 删除标记文件
# - TrackedObjectProcessor 检查标记文件是否存在，决定是否推送 AI 帧
# - 文件 I/O 对每秒几次的检查没有性能影响
_ai_stream_flag_dir = Path(project_root) / 'data' / 'ai_stream_flags'


def init_ai_stream_state():
    """初始化 AI 视频流推送的文件标记目录。

    清理上次运行残留的标记文件，确保任务启动时所有 AI 流默认关闭。
    """
    global _ai_stream_flag_dir
    _ai_stream_flag_dir = Path(project_root) / 'data' / 'ai_stream_flags'
    _ai_stream_flag_dir.mkdir(parents=True, exist_ok=True)
    # 清理残留的标记文件
    for flag_file in _ai_stream_flag_dir.glob('*.flag'):
        try:
            flag_file.unlink()
        except Exception:
            pass
    logger.info(f"[Main] AI 视频流标记目录已初始化: {_ai_stream_flag_dir}")


def _set_ai_stream_flag(camera_id: str, enabled: bool):
    """设置摄像机 AI 流的启用标记文件"""
    flag_file = _ai_stream_flag_dir / f'{camera_id}.flag'
    try:
        if enabled:
            flag_file.touch()
        else:
            flag_file.unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"设置 AI 流标记失败: camera_id={camera_id}, enabled={enabled}, error={e}")


def _clear_ai_stream_flags(camera_ids: list):
    """清理指定摄像机的 AI 流标记文件（任务停止时调用）"""
    for camera_id in camera_ids:
        try:
            flag_file = _ai_stream_flag_dir / f'{camera_id}.flag'
            flag_file.unlink(missing_ok=True)
        except Exception:
            pass

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
PUBLIC_PATHS = ('/login', '/api/auth/', '/api/ai-stream/', '/api/events/push', '/api/events/update', '/api/events/gif_ready', '/api/display/config', '/static/', '/output/')

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


@app.route('/api/display/config', methods=['GET'])
def get_display_config():
    """返回显示配置（含 alarm 报警开关）给前端

    前端实时监控页通过此接口拉取 display_config.yaml 中的 alarm 段，
    用于控制屏幕闪烁报警行为。配置文件为单一真相源，避免前端硬编码。
    """
    from src.tasks.task_manager import get_default_display_config
    return jsonify(get_default_display_config())


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
# WebSocket 架构说明
# ========================================
# 系统使用 Flask-SocketIO 作为唯一的 WebSocket 通信方案（端口 5556，namespace: '/ws'）
#
# AI 视频流推送模式（后端画框，统一架构）：
#    - TrackedObjectProcessor → HTTP POST /api/ai-stream/push（仅当 {flag_dir}/{camera_id}.flag 存在时推送）
#    - 主进程 → socketio.emit('ai_stream_frame', {...}, namespace='/ws')
#    - 前端接收事件，直接在 Canvas 上 drawImage 显示带检测框的 JPEG 帧
#    - 前端点击 AI 播放 → /api/ai-stream/enable → 创建标记文件 → 后端开始推流
#    - 前端点击停止 → /api/ai-stream/disable → 删除标记文件 → 后端停止推流
#    文件标记方案替代 Manager dict 代理，避免 Windows spawn 模式下跨两层进程 pickle 失效
#
# 注意：旧的 detection_frame 前端画框模式已完全移除，统一使用后端画框
# ========================================

# AI 视频帧缓存（最近帧）
_ai_stream_cache = {}  # {camera_id: {'frame': base64, 'timestamp': str}}


def _emit_ai_stream_frame(payload):
    """后台 greenlet 执行的 AI 帧 WebSocket 广播

    socketio.emit 在 eventlet 下为非阻塞写 socket 缓冲区（缓冲区满才 yield），
    不会卡死 hub。从 HTTP 请求派发到此 greenlet，避免 emit 遍历慢客户端时
    阻塞 HTTP 协程导致任务进程超时。
    """
    try:
        socketio.emit('ai_stream_frame', payload, namespace='/ws')
        ws_manager.broadcast('ai_stream_frame', payload)
    except Exception as e:
        logger.error(f"广播 AI 帧失败 [{payload.get('camera_id', '')}]: {e}")


@app.route('/api/ai-stream/push', methods=['POST'])
def push_ai_stream_frame():
    """接收任务进程推送的 AI 视频帧，并派发到后台 greenlet 异步广播

    HTTP 处理只做：解析 → 缓存最新帧 → 派发 emit greenlet，立即返回，
    避免前端客户端接收慢时阻塞本协程导致任务进程 HTTP 超时。
    （注意：不能用标准库 queue.Queue 的阻塞 get 做常驻 greenlet——本项目未调用
     eventlet.monkey_patch()，阻塞 get 会卡死 eventlet hub，导致整个服务无响应。）
    """
    try:
        data = request.get_json()
        task_id = data.get('task_id')
        camera_id = data.get('camera_id')
        # 兼容 frame_base64（TrackedObjectProcessor 发送）和 frame（旧版兼容）
        frame = data.get('frame_base64') or data.get('frame')
        timestamp = data.get('timestamp')

        if not camera_id or not frame:
            return jsonify({'error': '参数不完整'}), 400

        # 缓存最新帧
        _ai_stream_cache[camera_id] = {
            'frame': frame,
            'timestamp': timestamp,
            'task_id': task_id
        }

        # 派发到后台 greenlet 执行 emit（start_background_task 在 eventlet 下用
        # eventlet.spawn，非阻塞；HTTP 立即返回，emit 不阻塞本协程）
        payload = {
            'task_id': task_id,
            'camera_id': camera_id,
            'frame': frame,
            'timestamp': timestamp
        }
        try:
            socketio.start_background_task(_emit_ai_stream_frame, payload)
        except Exception as e:
            logger.error(f"[AI流] 派发广播 greenlet 失败: {e}")

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
        ws_manager.broadcast('new_event', data)
        logger.debug(f"事件已推送到驾驶舱: {data.get('event_id', '')[:8]}... type={data.get('type', '')}")

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"推送事件到驾驶舱失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/events/update', methods=['POST'])
def push_event_update_to_websocket():
    """接收事件状态更新（ended），通过 WebSocket 广播到驾驶舱

    单一事件模型：跟踪结束时调用，让前端更新已显示事件卡片的状态为"已结束"。
    """
    try:
        data = request.get_json()
        if not data or 'event_id' not in data:
            return jsonify({'error': '参数不完整'}), 400

        socketio.emit('event_update', data, namespace='/ws')
        ws_manager.broadcast('event_update', data)
        logger.info(f"[Event] event_update 推送: event_id={data['event_id'][:8]}, status={data.get('status', '')}")

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"推送 event_update 失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/events/gif_ready', methods=['POST'])
def push_gif_ready_to_websocket():
    """接收 GIF 生成完成通知，通过 WebSocket 广播到驾驶舱和事件管理页面

    让前端实时收到 GIF 并更新已显示事件卡片的 GIF 缩略图。
    """
    try:
        data = request.get_json()
        if not data or 'event_id' not in data:
            return jsonify({'error': '参数不完整'}), 400

        socketio.emit('gif_ready', data, namespace='/ws')
        ws_manager.broadcast('gif_ready', data)
        logger.info(f"[GIF] gif_ready 推送: event_id={data['event_id'][:8]}")

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"推送 gif_ready 失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai-stream/enable/<camera_id>', methods=['POST'])
def enable_ai_stream(camera_id):
    """启用摄像机的 AI 视频流（前端点击 AI 播放时调用）

    通过创建文件标记通知任务子进程的 TrackedObjectProcessor 开始推送 AI 帧到前端。
    文件标记方案不依赖 Manager dict 代理的跨进程传递，更加可靠。
    """
    try:
        _set_ai_stream_flag(camera_id, True)
        logger.info(f"[AI流] 已启用: camera_id={camera_id}")
        return jsonify({'success': True, 'message': 'AI 视频流已启用'})

    except Exception as e:
        logger.error(f"启用 AI 视频流失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai-stream/disable/<camera_id>', methods=['POST'])
def disable_ai_stream(camera_id):
    """禁用摄像机的 AI 视频流（前端点击停止 AI 播放时调用）

    通过删除文件标记通知任务子进程的 TrackedObjectProcessor 停止推送 AI 帧到前端。
    """
    try:
        _set_ai_stream_flag(camera_id, False)
        _ai_stream_cache.pop(camera_id, None)
        logger.info(f"[AI流] 已禁用: camera_id={camera_id}")
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
            # 检查数据库中的任务状态变化（只停止不需要的任务）
            # ========================================
            # ✅ 修复：移除启动任务的逻辑，完全由 API 控制
            # 监控线程只负责：
            # 1. 检测异常退出的任务并重启（上面的逻辑）
            # 2. 停止不需要的任务（下面的逻辑）
            # 不再负责启动新任务（避免与 API 冲突）

            tasks = tm.get_all_tasks(enabled_only=True)
            running_ids = pm.get_running_tasks()

            # ✅ 移除：启动新任务的逻辑（已由 API 负责）
            # for task in tasks:
            #     if task.status == 'running' and task.id not in running_ids:
            #         ...

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
                logger.debug(f"[TaskMonitor] 运行中任务进程: {running_count}")
            
            time.sleep(2)
        
        except Exception as e:
            logger.error(f"[TaskMonitor] 监控错误: {e}")
            time.sleep(5)
    
    logger.info("[TaskMonitor] 监控线程退出")


# ========================================
# 自签名证书生成（用于 HTTPS，使语音对讲功能在 IP 访问时可用）
# ========================================

def _generate_self_signed_cert(cert_path: str, key_path: str):
    """生成自签名 SSL 证书（首次启用 HTTPS 时自动调用）"""
    import ssl
    import datetime

    # 确保目录存在
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    os.makedirs(os.path.dirname(key_path), exist_ok=True)

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        # 生成 RSA 私钥
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # 构建证书主题
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SageSight AI"),
            x509.NameAttribute(NameOID.COMMON_NAME, "SageSight"),
        ])

        # 创建自签名证书（有效期 10 年）
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(__import__('ipaddress').ip_address("0.0.0.0")),
                ]),
                critical=False,
            )
            .sign(private_key, hashes.SHA256())
        )

        # 写入私钥文件
        with open(key_path, "wb") as f:
            f.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )

        # 写入证书文件
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        logger.info("[HTTPS] 自签名证书生成成功（有效期10年）")

    except ImportError:
        # cryptography 不可用时，回退到 OpenSSL 命令行
        logger.warning("[HTTPS] cryptography 库不可用，尝试使用 OpenSSL 命令行...")
        import subprocess
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path, "-out", cert_path,
            "-days", "3650", "-nodes",
            "-subj", "/CN=SageSight/O=SageSight AI/C=CN",
        ], check=True, capture_output=True)
        logger.info("[HTTPS] OpenSSL 自签名证书生成成功")


# ========================================
# 启动应用
# ========================================

if __name__ == '__main__':
    # 初始化 AI 流推送共享状态（必须在 main 块内创建 Manager）
    # Windows 使用 spawn 模式启动子进程，子进程会重新导入本模块，
    # 若在模块顶层创建 Manager() 会导致递归创建子进程的 RuntimeError。
    init_ai_stream_state()

    logger.info("="*60)
    logger.info("SageSight AI 检测系统 - 统一主程序")
    logger.info("="*60)
    logger.info(f"启动时间: {datetime.now().isoformat()}")
    logger.info("="*60)

    # 日志监听器已在模块级别通过 LogManager.setup_main_process() 启动

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

        # 停止日志监听器
        try:
            LogManager.get_instance().stop()
            logger.info("[Cleanup] 日志监听器已停止")
        except Exception as e:
            logger.error(f"[Cleanup] 停止日志监听器失败: {e}")

        # ✅ 强制杀死所有残留的子进程（最后手段）
        try:
            import psutil
            import signal
            import os

            current_pid = os.getpid()
            parent = psutil.Process(current_pid)
            children = parent.children(recursive=True)

            if children:
                logger.warning(f"[Cleanup] 发现 {len(children)} 个残留子进程，强制清理")
                for child in children:
                    try:
                        if child.name() == 'python.exe':
                            logger.warning(f"[Cleanup] 强制终止子进程 PID={child.pid}")
                            child.kill()
                    except Exception as e:
                        logger.error(f"[Cleanup] 终止子进程 PID={child.pid} 失败: {e}")
        except Exception as e:
            logger.error(f"[Cleanup] 清理残留子进程失败: {e}")

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

    # 启动事件清理后台线程（按 system_config.yaml 的 events_cleanup 配置定期清理过期事件及存储文件）
    # enabled=false 时不启动后台线程（仍可通过 POST /api/events/clear 手动清理）
    from src.events.event_cleanup_service import start_event_cleanup_scheduler
    start_event_cleanup_scheduler()

    # 读取端口配置
    from src.system.system_config_loader import load_system_config
    sys_cfg = load_system_config()
    web_cfg = sys_cfg.get('web_server', {})
    port = web_cfg.get('port', 5556)

    # 显示访问地址
    logger.info(f"\n[Main] Web管理平台访问地址: http://localhost:{port}")
    logger.info(f"  - 任务管理: http://localhost:{port}/tasks")
    logger.info(f"  - 摄像机管理: http://localhost:{port}/cameras")
    logger.info(f"  - 事件列表: http://localhost:{port}/events")
    logger.info(f"  - 可视化监控: http://localhost:{port}/monitor")
    logger.info(f"  - 算法库: http://localhost:{port}/algorithms")
    logger.info(f"  - 系统管理: http://localhost:{port}/system")
    logger.info("="*60)

    # 运行Flask应用（前台）
    try:
        logger.info(f"[HTTP] 服务已启用: http://0.0.0.0:{port}")
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    except KeyboardInterrupt:
        logger.info("\n[Main] 用户按下Ctrl+C，正在关闭...")
    except Exception as e:
        logger.error(f"\n[Main] 应用异常退出: {e}")
    finally:
        # 停止事件清理后台线程（优雅退出）
        from src.events.event_cleanup_service import stop_event_cleanup_scheduler
        stop_event_cleanup_scheduler()
        # 调用清理函数（双重保险）
        cleanup_handler()
        logger.info("[Main] 系统已关闭")
        logger.info("="*60)