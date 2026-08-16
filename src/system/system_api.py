"""
SageSight AI - 系统管理 API 接口
提供流媒体服务器配置等系统设置的 REST API
"""

import logging
from flask import Blueprint, jsonify, request
from src.system.system_manager import get_system_manager, StreamingServer, LLMConfig

logger = logging.getLogger(__name__)

# 创建 Blueprint
system_bp = Blueprint('system', __name__, url_prefix='/api/system')

# WebSocket 实例（可选）
_socketio = None


def register_system_blueprint(socketio=None):
    """注册系统管理 Blueprint"""
    global _socketio
    _socketio = socketio
    
    # 初始化系统管理器（创建数据库表和默认配置）
    from src.system.system_manager import get_system_manager
    manager = get_system_manager()
    logger.info(f"系统管理器已初始化，流媒体服务器数量: {len(manager.get_all_streaming_servers())}")
    
    return system_bp


# ========================================
# 流媒体服务器 API
# ========================================

@system_bp.route('/streaming-servers', methods=['GET'])
def get_streaming_servers():
    """获取所有流媒体服务器配置"""
    manager = get_system_manager()
    servers = manager.get_all_streaming_servers()
    return jsonify([server.to_dict() for server in servers])


@system_bp.route('/streaming-servers/<int:server_id>', methods=['GET'])
def get_streaming_server(server_id: int):
    """获取单个流媒体服务器配置"""
    manager = get_system_manager()
    server = manager.get_streaming_server(server_id)
    
    if server:
        return jsonify(server.to_dict())
    return jsonify({'error': '流媒体服务器不存在'}), 404


@system_bp.route('/streaming-servers/default', methods=['GET'])
def get_default_streaming_server():
    """获取默认流媒体服务器"""
    manager = get_system_manager()
    server = manager.get_default_streaming_server()
    
    if server:
        return jsonify(server.to_dict())
    return jsonify({'error': '未配置默认流媒体服务器'}), 404


@system_bp.route('/streaming-servers', methods=['POST'])
def create_streaming_server():
    """创建流媒体服务器配置"""
    data = request.get_json()
    
    if not data or not data.get('name'):
        return jsonify({'error': '缺少必要参数'}), 400
    
    manager = get_system_manager()
    
    server = StreamingServer(
        name=data['name'],
        server_type=data.get('server_type', 'zlmediakit'),
        host=data.get('host', 'localhost'),
        rtmp_port=data.get('rtmp_port', 1935),
        rtsp_port=data.get('rtsp_port', 554),
        http_port=data.get('http_port', 88),
        rtc_port=data.get('rtc_port', 8000),
        secret=data.get('secret', ''),
        media_server_id=data.get('media_server_id', ''),
        enabled=data.get('enabled', True),
        is_default=data.get('is_default', False),
        description=data.get('description', '')
    )
    
    server_id = manager.create_streaming_server(server)
    
    if server_id:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('streaming_server_created', {'server_id': server_id})
        
        return jsonify({
            'message': '流媒体服务器创建成功',
            'server_id': server_id,
            'server': manager.get_streaming_server(server_id).to_dict()
        })
    
    return jsonify({'error': '创建失败'}), 500


@system_bp.route('/streaming-servers/<int:server_id>', methods=['PUT'])
def update_streaming_server(server_id: int):
    """更新流媒体服务器配置"""
    data = request.get_json()
    
    if not data:
        return jsonify({'error': '缺少更新数据'}), 400
    
    manager = get_system_manager()
    existing = manager.get_streaming_server(server_id)
    
    if not existing:
        return jsonify({'error': '流媒体服务器不存在'}), 404
    
    # 更新字段
    server = StreamingServer(
        id=server_id,
        name=data.get('name', existing.name),
        server_type=data.get('server_type', existing.server_type),
        host=data.get('host', existing.host),
        rtmp_port=data.get('rtmp_port', existing.rtmp_port),
        rtsp_port=data.get('rtsp_port', existing.rtsp_port),
        http_port=data.get('http_port', existing.http_port),
        rtc_port=data.get('rtc_port', existing.rtc_port),
        secret=data.get('secret', existing.secret),
        media_server_id=data.get('media_server_id', existing.media_server_id),
        enabled=data.get('enabled', existing.enabled),
        is_default=data.get('is_default', existing.is_default),
        description=data.get('description', existing.description),
        status=existing.status
    )
    
    success = manager.update_streaming_server(server_id, server)
    
    if success:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('streaming_server_updated', {'server_id': server_id})
        
        return jsonify({
            'message': '流媒体服务器更新成功',
            'server': manager.get_streaming_server(server_id).to_dict()
        })
    
    return jsonify({'error': '更新失败'}), 500


@system_bp.route('/streaming-servers/<int:server_id>', methods=['DELETE'])
def delete_streaming_server(server_id: int):
    """删除流媒体服务器配置"""
    manager = get_system_manager()
    
    # 检查是否存在
    server = manager.get_streaming_server(server_id)
    if not server:
        return jsonify({'error': '流媒体服务器不存在'}), 404
    
    # 检查是否为默认服务器
    if server.is_default:
        return jsonify({'error': '无法删除默认流媒体服务器'}), 400
    
    success = manager.delete_streaming_server(server_id)
    
    if success:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('streaming_server_deleted', {'server_id': server_id})
        
        return jsonify({'message': '流媒体服务器删除成功'})
    
    return jsonify({'error': '删除失败'}), 500



@system_bp.route('/streaming-servers/<int:server_id>/set-default', methods=['POST'])
def set_default_streaming_server(server_id: int):
    """设置默认流媒体服务器"""
    manager = get_system_manager()
    
    server = manager.get_streaming_server(server_id)
    if not server:
        return jsonify({'error': '流媒体服务器不存在'}), 404
    
    # 更新为默认服务器
    server.is_default = True
    server.enabled = True
    success = manager.update_streaming_server(server_id, server)
    
    if success:
        # WebSocket 通知
        if _socketio:
            _socketio.emit('streaming_server_updated', {'server_id': server_id})
        
        return jsonify({
            'message': '已设置为默认流媒体服务器',
            'server': manager.get_streaming_server(server_id).to_dict()
        })
    
    return jsonify({'error': '设置失败'}), 500


# ========================================
# 大模型 API 配置
# ========================================

@system_bp.route('/llm-configs', methods=['GET'])
def get_llm_configs():
    """获取所有大模型配置"""
    manager = get_system_manager()
    configs = manager.get_all_llm_configs()
    return jsonify([config.to_dict() for config in configs])


@system_bp.route('/llm-configs/<int:config_id>', methods=['GET'])
def get_llm_config(config_id: int):
    """获取单个大模型配置"""
    manager = get_system_manager()
    config = manager.get_llm_config(config_id)
    if config:
        return jsonify(config.to_dict())
    return jsonify({'error': '大模型配置不存在'}), 404


@system_bp.route('/llm-configs/default', methods=['GET'])
def get_default_llm_config():
    """获取默认大模型配置"""
    manager = get_system_manager()
    config = manager.get_default_llm_config()
    if config:
        return jsonify(config.to_dict())
    return jsonify({'error': '未配置默认大模型'}), 404


@system_bp.route('/llm-configs', methods=['POST'])
def create_llm_config():
    """创建大模型配置"""
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'error': '缺少配置名称'}), 400
    if not data.get('model'):
        return jsonify({'error': '缺少模型名称'}), 400

    manager = get_system_manager()
    config = LLMConfig(
        name=data['name'],
        provider=data.get('provider', 'openai'),
        base_url=data.get('base_url', ''),
        api_key=data.get('api_key', ''),
        model=data['model'],
        temperature=float(data.get('temperature', 0.3)),
        max_tokens=int(data.get('max_tokens', 500)),
        support_images=bool(data.get('support_images', True)),
        enable_description=bool(data.get('enable_description', True)),
        enable_dedup=bool(data.get('enable_dedup', True)),
        is_default=bool(data.get('is_default', False)),
        enabled=bool(data.get('enabled', True)),
        description=data.get('description', '')
    )
    config_id = manager.create_llm_config(config)
    if config_id:
        if _socketio:
            _socketio.emit('llm_config_created', {'config_id': config_id})
        return jsonify({
            'message': '大模型配置创建成功',
            'config_id': config_id,
            'config': manager.get_llm_config(config_id).to_dict()
        })
    return jsonify({'error': '创建失败'}), 500


@system_bp.route('/llm-configs/<int:config_id>', methods=['PUT'])
def update_llm_config(config_id: int):
    """更新大模型配置"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '缺少更新数据'}), 400

    manager = get_system_manager()
    existing = manager.get_llm_config(config_id)
    if not existing:
        return jsonify({'error': '大模型配置不存在'}), 404

    # api_key 脱敏返回的 '***' 不回写，空值保留原 key
    api_key = data.get('api_key', '')
    if not api_key or api_key.endswith('***'):
        api_key = existing.api_key

    config = LLMConfig(
        id=config_id,
        name=data.get('name', existing.name),
        provider=data.get('provider', existing.provider),
        base_url=data.get('base_url', existing.base_url),
        api_key=api_key,
        model=data.get('model', existing.model),
        temperature=float(data.get('temperature', existing.temperature)),
        max_tokens=int(data.get('max_tokens', existing.max_tokens)),
        support_images=bool(data.get('support_images', existing.support_images)),
        enable_description=bool(data.get('enable_description', existing.enable_description)),
        enable_dedup=bool(data.get('enable_dedup', existing.enable_dedup)),
        is_default=bool(data.get('is_default', existing.is_default)),
        enabled=bool(data.get('enabled', existing.enabled)),
        description=data.get('description', existing.description)
    )
    success = manager.update_llm_config(config_id, config)
    if success:
        if _socketio:
            _socketio.emit('llm_config_updated', {'config_id': config_id})
        return jsonify({
            'message': '大模型配置更新成功',
            'config': manager.get_llm_config(config_id).to_dict()
        })
    return jsonify({'error': '更新失败'}), 500


@system_bp.route('/llm-configs/<int:config_id>', methods=['DELETE'])
def delete_llm_config(config_id: int):
    """删除大模型配置"""
    manager = get_system_manager()
    config = manager.get_llm_config(config_id)
    if not config:
        return jsonify({'error': '大模型配置不存在'}), 404
    if config.is_default:
        return jsonify({'error': '无法删除默认大模型配置'}), 400
    success = manager.delete_llm_config(config_id)
    if success:
        if _socketio:
            _socketio.emit('llm_config_deleted', {'config_id': config_id})
        return jsonify({'message': '大模型配置删除成功'})
    return jsonify({'error': '删除失败'}), 500


@system_bp.route('/llm-configs/<int:config_id>/set-default', methods=['POST'])
def set_default_llm_config(config_id: int):
    """设置默认大模型配置"""
    manager = get_system_manager()
    config = manager.get_llm_config(config_id)
    if not config:
        return jsonify({'error': '大模型配置不存在'}), 404
    success = manager.set_default_llm_config(config_id)
    if success:
        if _socketio:
            _socketio.emit('llm_config_updated', {'config_id': config_id})
        return jsonify({
            'message': '已设置为默认大模型配置',
            'config': manager.get_llm_config(config_id).to_dict()
        })
    return jsonify({'error': '设置失败'}), 500


@system_bp.route('/llm-configs/<int:config_id>/test', methods=['POST'])
def test_llm_config(config_id: int):
    """测试大模型连通性（发送简单文本请求验证 API 可用）"""
    manager = get_system_manager()
    config = manager.get_llm_config(config_id)
    if not config:
        return jsonify({'error': '大模型配置不存在'}), 404
    try:
        from src.llm.llm_client import LLMClient
        client = LLMClient(config)
        reply = client.chat_text("请回复'连接成功'四个字。")
        return jsonify({'message': '连接成功', 'reply': reply})
    except Exception as e:
        return jsonify({'error': f'连接失败: {e}'}), 500


@system_bp.route('/llm-enabled', methods=['GET'])
def get_llm_enabled():
    """获取大模型功能总开关状态

    总开关关闭时，整个系统不再调用大模型（事件描述/语义去重均不执行），
    避免产生不必要的 token 费用。
    """
    manager = get_system_manager()
    return jsonify({'llm_enabled': manager.is_llm_enabled()})


@system_bp.route('/llm-enabled', methods=['PUT'])
def set_llm_enabled():
    """设置大模型功能总开关"""
    data = request.get_json() or {}
    manager = get_system_manager()
    enabled = bool(data.get('llm_enabled', True))
    manager.set_llm_enabled(enabled)
    if _socketio:
        _socketio.emit('llm_enabled_updated', {'llm_enabled': enabled})
    return jsonify({
        'message': '大模型功能已' + ('启用' if enabled else '关闭'),
        'llm_enabled': enabled
    })


# ========================================
# 系统状态 API
# ========================================

@system_bp.route('/status', methods=['GET'])
def get_system_status():
    """获取系统整体状态"""
    manager = get_system_manager()
    servers = manager.get_all_streaming_servers()
    
    online_count = sum(1 for s in servers if s.status == 'online')
    offline_count = sum(1 for s in servers if s.status == 'offline')
    unknown_count = sum(1 for s in servers if s.status == 'unknown')
    
    return jsonify({
        'streaming_servers': {
            'total': len(servers),
            'online': online_count,
            'offline': offline_count,
            'unknown': unknown_count
        }
    })


# ========================================
# 日志管理 API
# ========================================

import os
import glob
from pathlib import Path

def _get_log_dir():
    """获取日志目录"""
    project_root = Path(__file__).parent.parent.parent
    log_dir = project_root / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

@system_bp.route('/logs/files', methods=['GET'])
def get_log_files():
    """获取日志文件列表"""
    log_dir = _get_log_dir()
    files = []
    
    for f in log_dir.glob('*'):
        if f.is_file():
            stat = f.stat()
            files.append({
                'name': f.name,
                'size': stat.st_size,
                'size_mb': round(stat.st_size / 1024 / 1024, 2),
                'modified': stat.st_mtime,
                'modified_str': os.path.basename(str(f))
            })
    
    files.sort(key=lambda x: x['modified'], reverse=True)
    
    return jsonify({'files': files})


@system_bp.route('/logs/read', methods=['GET'])
def read_log_file():
    """读取日志文件内容"""
    filename = request.args.get('file', 'system.log')
    lines = int(request.args.get('lines', 500))
    level = request.args.get('level', '')  # 筛选日志级别
    
    log_dir = _get_log_dir()
    log_path = log_dir / filename
    
    # 安全检查：防止路径遍历
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': '无效的文件名'}), 400
    
    if not log_path.exists():
        return jsonify({'error': '日志文件不存在'}), 404
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
        
        # 从末尾读取指定行数
        all_lines = all_lines[-lines:]
        
        # 按级别筛选
        if level:
            level_upper = level.upper()
            all_lines = [l for l in all_lines if f'[{level_upper}]' in l or f' - {level_upper} -' in l]
        
        return jsonify({
            'filename': filename,
            'total_lines': len(all_lines),
            'lines': all_lines
        })
    except Exception as e:
        return jsonify({'error': f'读取失败: {str(e)}'}), 500


@system_bp.route('/logs/clear', methods=['POST'])
def clear_log_file():
    """清空日志文件"""
    filename = request.args.get('file', 'system.log')
    
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': '无效的文件名'}), 400
    
    log_dir = _get_log_dir()
    log_path = log_dir / filename
    
    if not log_path.exists():
        return jsonify({'error': '日志文件不存在'}), 404
    
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('')
        logger.info(f"日志文件已清空: {filename}")
        return jsonify({'message': f'日志文件 {filename} 已清空'})
    except Exception as e:
        return jsonify({'error': f'清空失败: {str(e)}'}), 500


@system_bp.route('/logs/download', methods=['GET'])
def download_log_file():
    """下载日志文件"""
    from flask import send_file
    filename = request.args.get('file', 'system.log')
    
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': '无效的文件名'}), 400
    
    log_dir = _get_log_dir()
    log_path = log_dir / filename
    
    if not log_path.exists():
        return jsonify({'error': '日志文件不存在'}), 404
    
    return send_file(str(log_path), as_attachment=True, download_name=filename)


def _collect_nvidia_gpu():
    """收集 NVIDIA GPU 信息（pynvml 优先，nvidia-smi 回退）

    Returns:
        list: GPU 设备列表，每个含 device_type='gpu'、has_memory=True
    """
    # 方案1：pynvml
    try:
        import pynvml
        pynvml.nvmlInit()
        gpu_count = pynvml.nvmlDeviceGetCount()
        result = []
        for i in range(gpu_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode('utf-8')
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            result.append({
                'index': i,
                'name': name,
                'device_type': 'gpu',
                'gpu_utilization': util.gpu,
                'memory_total': mem_info.total,
                'memory_used': mem_info.used,
                'memory_total_mb': round(mem_info.total / 1024 / 1024, 0),
                'memory_used_mb': round(mem_info.used / 1024 / 1024, 0),
                'memory_percent': round(mem_info.used / mem_info.total * 100, 1) if mem_info.total > 0 else 0,
                'has_memory': True,
            })
        pynvml.nvmlShutdown()
        if result:
            return result
    except Exception:
        pass

    # 方案2：nvidia-smi 命令解析（nvml.dll 找不到时的回退）
    try:
        import subprocess
        proc = subprocess.run(
            [
                'nvidia-smi',
                '--query-gpu=index,name,utilization.gpu,memory.total,memory.used',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result = []
            for line in proc.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(',')]
                if len(parts) < 5:
                    continue
                idx = int(parts[0])
                name = parts[1]
                util = int(parts[2].split()[0]) if parts[2] else 0
                mem_total = float(parts[3])
                mem_used = float(parts[4])
                result.append({
                    'index': idx,
                    'name': name,
                    'device_type': 'gpu',
                    'gpu_utilization': util,
                    'memory_total': int(mem_total * 1024 * 1024),
                    'memory_used': int(mem_used * 1024 * 1024),
                    'memory_total_mb': mem_total,
                    'memory_used_mb': mem_used,
                    'memory_percent': round(mem_used / mem_total * 100, 1) if mem_total > 0 else 0,
                    'has_memory': True,
                })
            if result:
                return result
    except Exception:
        pass

    return []


def _collect_rk3588_npu():
    """收集 RK3588 NPU 使用率

    读取 /sys/kernel/debug/rknpu/load 获取 3 个 NPU 核心的使用率，取平均值。
    NPU 共享系统内存、无独立显存，因此 has_memory=False（前端不显示显存行）。

    Returns:
        list: NPU 设备列表，每个含 device_type='npu'、has_memory=False
    """
    try:
        from src.utils.platform_utils import is_rk3588
        if not is_rk3588():
            return []
    except Exception:
        return []

    import re
    # /sys/kernel/debug/rknpu/load 需 root（算法盒子默认 root 运行）
    try:
        with open('/sys/kernel/debug/rknpu/load', 'r') as f:
            content = f.read()
        cores = re.findall(r'Core\d+:\s*(\d+)%', content)
        if cores:
            util = sum(int(c) for c in cores) / len(cores)
            return [{
                'index': 0,
                'name': 'RK3588 NPU',
                'device_type': 'npu',
                'gpu_utilization': round(util, 1),
                'memory_total_mb': 0,
                'memory_used_mb': 0,
                'memory_percent': 0,
                'has_memory': False,
            }]
    except Exception:
        pass

    return []


def _collect_accelerators():
    """收集所有 AI 加速器（GPU/NPU 等）

    通用架构：每种加速器独立收集，通过 device_type 区分类型，前端依据 device_type
    显示对应标签（GPU/NPU），不绑定具体厂商。新增加速器支持只需追加一个收集函数。

    Returns:
        list: 加速器设备列表，每个含 device_type 字段（'gpu'/'npu'）
    """
    devices = []
    devices.extend(_collect_nvidia_gpu())
    devices.extend(_collect_rk3588_npu())
    return devices


# ========================================
# 系统监控 API
# ========================================

@system_bp.route('/monitor', methods=['GET'])
def get_system_monitor():
    """获取系统监控信息"""
    import psutil
    import platform
    import time
    
    # CPU信息
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()
    
    # 内存信息
    mem = psutil.virtual_memory()
    
    # 磁盘信息
    disk = psutil.disk_usage('/')
    
    # 网络信息
    net = psutil.net_io_counters()
    
    # 系统信息
    boot_time = psutil.boot_time()
    uptime = int(time.time() - boot_time)
    
    # 进程信息
    process_count = len(psutil.pids())
    
    # AI 加速器信息（GPU/NPU，通过 device_type 区分类型）
    accelerators = _collect_accelerators()

    return jsonify({
        'cpu': {
            'percent': cpu_percent,
            'count': cpu_count,
            'freq_mhz': cpu_freq.current if cpu_freq else 0
        },
        'memory': {
            'total': mem.total,
            'used': mem.used,
            'available': mem.available,
            'percent': mem.percent,
            'total_gb': round(mem.total / 1024**3, 2),
            'used_gb': round(mem.used / 1024**3, 2)
        },
        'disk': {
            'total': disk.total,
            'used': disk.used,
            'free': disk.free,
            'percent': round(disk.used / disk.total * 100, 1) if disk.total > 0 else 0,
            'total_gb': round(disk.total / 1024**3, 2),
            'used_gb': round(disk.used / 1024**3, 2)
        },
        'network': {
            'bytes_sent': net.bytes_sent,
            'bytes_recv': net.bytes_recv,
            'sent_mb': round(net.bytes_sent / 1024**2, 2),
            'recv_mb': round(net.bytes_recv / 1024**2, 2)
        },
        'system': {
            'platform': platform.platform(),
            'python_version': platform.python_version(),
            'hostname': platform.node(),
            'uptime': uptime,
            'uptime_str': f"{uptime // 3600}h {(uptime % 3600) // 60}m",
            'process_count': process_count,
            'boot_time': boot_time
        },
        'gpu': accelerators,        # 兼容历史字段名，内含 device_type 区分 GPU/NPU
        'accelerators': accelerators,  # AI 加速器列表（GPU/NPU，含 device_type）
        'timestamp': time.time()
    })


@system_bp.route('/monitor/processes', methods=['GET'])
def get_system_processes():
    """获取系统进程列表（按CPU排序）"""
    import psutil
    
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'memory_info', 'status']):
        try:
            info = p.info
            procs.append({
                'pid': info['pid'],
                'name': info['name'],
                'cpu_percent': info['cpu_percent'] or 0,
                'memory_percent': round(info['memory_percent'] or 0, 2),
                'memory_mb': round(info['memory_info'].rss / 1024 / 1024, 1) if info['memory_info'] else 0,
                'status': info['status']
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    # 按CPU使用率排序，取前20
    procs.sort(key=lambda x: x['cpu_percent'], reverse=True)
    procs = procs[:20]
    
    return jsonify({'processes': procs})


# ========================================
# 全局配置管理 API（存储/MQTT/WebSocket）
# ========================================

import yaml
from pathlib import Path

def _get_config_path():
    """获取system_config.yaml路径"""
    return Path(__file__).parent.parent.parent / 'config' / 'system_config.yaml'

def _load_system_config():
    """加载system_config.yaml"""
    config_path = _get_config_path()
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}

def _save_system_config(config):
    """保存system_config.yaml"""
    config_path = _get_config_path()
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


@system_bp.route('/config', methods=['GET'])
def get_system_config():
    """获取存储/MQTT/实时通信/节点管理配置"""
    try:
        config = _load_system_config()
        # 节点管理配置统一通过 loader 归一化字段类型，避免前端处理字符串/整数混用
        from src.system.system_config_loader import get_node_management_config, get_event_dedup_config
        return jsonify({
            'storage': config.get('storage', {}),
            'mqtt': config.get('mqtt', {}),
            'web_server': config.get('web_server', {}),
            'database': config.get('database', {}),
            'node_management': get_node_management_config(),
            'event_dedup': get_event_dedup_config(),
            'video_file_upload': config.get('video_file_upload', {
                'max_file_size_mb': 100,
                'allowed_formats': ['mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv'],
                'upload_dir': 'uploads/video_files',
                'max_batch_count': 20
            }),
            # 简化字段名（前端使用）
            'video_file_max_size_mb': config.get('video_file_upload', {}).get('max_file_size_mb', 100)
        })
    except Exception as e:
        logger.error(f"获取系统配置失败: {e}")
        return jsonify({'error': str(e)}), 500


@system_bp.route('/config', methods=['PUT'])
def update_system_config():
    """更新存储/MQTT/WebSocket配置"""
    try:
        data = request.get_json()
        config = _load_system_config()
        
        # 更新存储配置
        if 'storage' in data:
            storage = data['storage']
            if 'minio' in storage:
                config.setdefault('storage', {}).setdefault('minio', {}).update(storage['minio'])
            if 'ftp' in storage:
                config.setdefault('storage', {}).setdefault('ftp', {}).update(storage['ftp'])
        
        # 更新MQTT配置
        if 'mqtt' in data:
            mqtt = data['mqtt']
            if 'enabled' in mqtt:
                config.setdefault('mqtt', {})['enabled'] = mqtt['enabled']
            if 'config' in mqtt:
                config.setdefault('mqtt', {}).setdefault('config', {}).update(mqtt['config'])
        
        # 更新实时通信配置（Socket.IO + 原生 WebSocket）
        if 'web_server' in data:
            web_server = data['web_server']
            ws_config = config.setdefault('web_server', {})

            # 基础服务配置（host/端口/调试/CORS/静态目录/模板目录）
            # host：盒子部署时填实际 IP，用于生成事件图片/GIF 的可访问 URL
            if 'host' in web_server and web_server['host']:
                ws_config['host'] = str(web_server['host']).strip()
            # 端口校验：1-65535
            if 'port' in web_server:
                try:
                    port_val = int(web_server['port'])
                    if 1 <= port_val <= 65535:
                        ws_config['port'] = port_val
                    else:
                        return jsonify({'error': '端口必须在 1-65535 范围内'}), 400
                except (ValueError, TypeError):
                    return jsonify({'error': '端口必须为整数'}), 400
            if 'debug' in web_server:
                ws_config['debug'] = bool(web_server['debug'])
            if 'cors_enabled' in web_server:
                ws_config['cors_enabled'] = bool(web_server['cors_enabled'])
            if 'static_dir' in web_server and web_server['static_dir']:
                ws_config['static_dir'] = str(web_server['static_dir'])
            if 'template_dir' in web_server and web_server['template_dir']:
                ws_config['template_dir'] = str(web_server['template_dir'])

            # Socket.IO 子配置
            if 'socketio' in web_server:
                socketio_cfg = web_server['socketio']
                ws_sio = ws_config.setdefault('socketio', {})
                if 'enabled' in socketio_cfg:
                    ws_sio['enabled'] = bool(socketio_cfg['enabled'])
                if 'namespace' in socketio_cfg and socketio_cfg['namespace']:
                    ws_sio['namespace'] = str(socketio_cfg['namespace'])
                if 'cors_allowed_origins' in socketio_cfg and socketio_cfg['cors_allowed_origins']:
                    ws_sio['cors_allowed_origins'] = str(socketio_cfg['cors_allowed_origins'])

            # 原生 WebSocket 子配置
            if 'raw_websocket' in web_server:
                raw_ws_cfg = web_server['raw_websocket']
                ws_raw = ws_config.setdefault('raw_websocket', {})
                if 'enabled' in raw_ws_cfg:
                    ws_raw['enabled'] = bool(raw_ws_cfg['enabled'])
                if 'path' in raw_ws_cfg and raw_ws_cfg['path']:
                    ws_raw['path'] = str(raw_ws_cfg['path'])

        # 更新节点管理配置（向边缘平台注册/上报心跳的客户端配置）
        # 字段名与边缘平台约定保持一致，端口/心跳间隔需校验类型
        if 'node_management' in data:
            nm = data['node_management'] or {}
            nm_cfg = config.setdefault('node_management', {})

            if 'enabled' in nm:
                nm_cfg['enabled'] = bool(nm['enabled'])
            if 'node_id' in nm:
                nm_cfg['node_id'] = str(nm['node_id']).strip()
            if 'agent_token' in nm:
                nm_cfg['agent_token'] = str(nm['agent_token']).strip()
            if 'control_plane_url' in nm and nm['control_plane_url']:
                nm_cfg['control_plane_url'] = str(nm['control_plane_url']).strip()
            if 'heartbeat_interval' in nm:
                try:
                    interval = int(nm['heartbeat_interval'])
                    if interval < 1:
                        return jsonify({'error': '心跳间隔必须大于 0 秒'}), 400
                    nm_cfg['heartbeat_interval'] = interval
                except (ValueError, TypeError):
                    return jsonify({'error': '心跳间隔必须为整数'}), 400
            if 'agent_listen_host' in nm and nm['agent_listen_host']:
                nm_cfg['agent_listen_host'] = str(nm['agent_listen_host']).strip()
            if 'agent_listen_port' in nm:
                try:
                    agent_port = int(nm['agent_listen_port'])
                    if not (1 <= agent_port <= 65535):
                        return jsonify({'error': 'Agent 监听端口必须在 1-65535 范围内'}), 400
                    nm_cfg['agent_listen_port'] = agent_port
                except (ValueError, TypeError):
                    return jsonify({'error': 'Agent 监听端口必须为整数'}), 400
            if 'agent_version' in nm and nm['agent_version']:
                nm_cfg['agent_version'] = str(nm['agent_version']).strip()
            if 'ai_root' in nm and nm['ai_root']:
                nm_cfg['ai_root'] = str(nm['ai_root']).strip()
            if 'video_root' in nm and nm['video_root']:
                nm_cfg['video_root'] = str(nm['video_root']).strip()
            if 'media_cluster_root' in nm and nm['media_cluster_root']:
                nm_cfg['media_cluster_root'] = str(nm['media_cluster_root']).strip()

        # 更新事件规则去重配置（事件管理功能，独立于大模型，不消耗 token）
        if 'event_dedup' in data:
            ed = data['event_dedup'] or {}
            ed_cfg = config.setdefault('event_dedup', {})

            if 'enabled' in ed:
                ed_cfg['enabled'] = bool(ed['enabled'])
            if 'window_seconds' in ed:
                try:
                    window = int(ed['window_seconds'])
                    if window < 1:
                        return jsonify({'error': '去重时间窗口必须大于 0 秒'}), 400
                    ed_cfg['window_seconds'] = window
                except (ValueError, TypeError):
                    return jsonify({'error': '去重时间窗口必须为整数（秒）'}), 400
            if 'iou_threshold' in ed:
                try:
                    iou = float(ed['iou_threshold'])
                    if not (0.0 < iou <= 1.0):
                        return jsonify({'error': 'IoU 阈值必须在 (0, 1] 范围内'}), 400
                    ed_cfg['iou_threshold'] = iou
                except (ValueError, TypeError):
                    return jsonify({'error': 'IoU 阈值必须为 0-1 之间的小数'}), 400

        _save_system_config(config)
        # 强制重新加载配置缓存，使后续请求（包括节点管理服务自身）能立即读到最新配置
        try:
            from src.system.system_config_loader import load_system_config
            load_system_config(force_reload=True)
        except Exception:
            pass
        logger.info("系统配置已更新")

        return jsonify({'success': True, 'message': '配置已保存，端口/目录/节点管理变更需重启服务后生效'})
    except Exception as e:
        logger.error(f"更新系统配置失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# 节点管理 API（运行状态查询）
# ========================================

@system_bp.route('/node-management/status', methods=['GET'])
def get_node_management_status():
    """获取节点管理服务运行状态与最近一次心跳指标"""
    try:
        from src.node_agent import get_node_agent_service
        from src.system.system_config_loader import get_node_management_config

        cfg = get_node_management_config()
        service = get_node_agent_service()

        status = {
            'enabled': cfg.get('enabled', False),
            'running': service._started,
            'node_id': cfg.get('node_id', ''),
            'control_plane_url': cfg.get('control_plane_url', ''),
            'agent_listen_host': cfg.get('agent_listen_host', '0.0.0.0'),
            'agent_listen_port': cfg.get('agent_listen_port', 9100),
            'heartbeat_interval': cfg.get('heartbeat_interval', 10),
        }

        # 附加最近一次心跳指标（复用同一套采集逻辑，便于前端展示与边缘平台一致的数据）
        try:
            status['metrics'] = service.collect_metrics()
        except Exception as e:
            logger.debug(f"采集节点管理指标失败: {e}")
            status['metrics'] = None

        return jsonify(status)
    except Exception as e:
        logger.error(f"获取节点管理状态失败: {e}")
        return jsonify({'error': str(e)}), 500


# ========================================
# 数据库管理 API（路径配置以 system_config.yaml 为单一真相源）
# ========================================

@system_bp.route('/database/info', methods=['GET'])
def get_database_info():
    """获取当前数据库信息（路径、大小、类型、备份数量）"""
    import os
    try:
        from src.system.system_config_loader import get_db_path, get_database_config
        db_cfg = get_database_config()
        db_path = get_db_path()
        abs_path = os.path.join(_get_project_root(), db_path)

        info = {
            'path': db_path,
            'type': db_cfg.get('type', 'sqlite'),
            'pool_enabled': db_cfg.get('pool_enabled', False),
            'pool_size': db_cfg.get('pool_size', 5),
            'exists': os.path.exists(abs_path),
            'size_bytes': 0,
            'size_display': '不存在',
        }

        if info['exists']:
            size = os.path.getsize(abs_path)
            info['size_bytes'] = size
            info['size_display'] = _format_file_size(size)

        # 统计备份文件
        backup_dir = os.path.join(_get_project_root(), 'data', 'backups')
        backups = []
        if os.path.isdir(backup_dir):
            for f in sorted(os.listdir(backup_dir), reverse=True):
                if f.startswith('tasks_') and f.endswith('.db'):
                    fpath = os.path.join(backup_dir, f)
                    backups.append({
                        'filename': f,
                        'size_display': _format_file_size(os.path.getsize(fpath)),
                        'created_at': os.path.getmtime(fpath),
                    })
        info['backup_count'] = len(backups)
        info['backups'] = backups[:20]  # 最多返回 20 条

        return jsonify(info)
    except Exception as e:
        logger.error(f"获取数据库信息失败: {e}")
        return jsonify({'error': str(e)}), 500


@system_bp.route('/database/path', methods=['PUT'])
def update_database_path():
    """更新数据库路径配置（写入 system_config.yaml，重启后生效）"""
    try:
        data = request.get_json()
        new_path = (data or {}).get('path', '').strip()

        if not new_path:
            return jsonify({'error': '路径不能为空'}), 400

        # 安全校验：只允许 .db/.sqlite 扩展名
        if not new_path.endswith(('.db', '.sqlite', '.sqlite3')):
            return jsonify({'error': '数据库路径必须以 .db 或 .sqlite 结尾'}), 400

        config = _load_system_config()
        config.setdefault('database', {})['path'] = new_path
        _save_system_config(config)
        logger.info(f"数据库路径已更新为: {new_path}（重启后生效）")

        return jsonify({
            'success': True,
            'message': f'数据库路径已更新为 {new_path}，需重启服务后生效',
            'path': new_path
        })
    except Exception as e:
        logger.error(f"更新数据库路径失败: {e}")
        return jsonify({'error': str(e)}), 500


@system_bp.route('/database/backup', methods=['POST'])
def backup_database():
    """立即备份数据库（复制当前 db 文件到 data/backups/）"""
    import shutil
    from datetime import datetime
    try:
        from src.system.system_config_loader import get_db_path
        db_path = get_db_path()
        abs_path = os.path.join(_get_project_root(), db_path)

        if not os.path.exists(abs_path):
            return jsonify({'error': '数据库文件不存在，无法备份'}), 404

        backup_dir = os.path.join(_get_project_root(), 'data', 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f'tasks_{timestamp}.db'
        backup_path = os.path.join(backup_dir, backup_name)

        shutil.copy2(abs_path, backup_path)
        size = os.path.getsize(backup_path)
        logger.info(f"数据库已备份到: {backup_path} ({_format_file_size(size)})")

        return jsonify({
            'success': True,
            'message': f'备份成功: {backup_name}',
            'backup': {
                'filename': backup_name,
                'size_display': _format_file_size(size),
                'created_at': os.path.getmtime(backup_path),
            }
        })
    except Exception as e:
        logger.error(f"数据库备份失败: {e}")
        return jsonify({'error': str(e)}), 500


@system_bp.route('/database/restore', methods=['POST'])
def restore_database():
    """恢复数据库备份（上传备份文件覆盖当前数据库，重启后生效）"""
    try:
        from src.system.system_config_loader import get_db_path

        if 'file' not in request.files:
            return jsonify({'error': '请选择备份文件'}), 400

        upload_file = request.files['file']
        if not upload_file.filename:
            return jsonify({'error': '文件名为空'}), 400

        db_path = get_db_path()
        abs_path = os.path.join(_get_project_root(), db_path)
        backup_dir = os.path.join(_get_project_root(), 'data', 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        # 先保存上传文件到临时目录
        temp_path = os.path.join(backup_dir, f'upload_{upload_file.filename}')
        upload_file.save(temp_path)

        # 备份当前数据库（恢复前的快照，防止误操作）
        if os.path.exists(abs_path):
            from datetime import datetime
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            pre_backup = os.path.join(backup_dir, f'pre_restore_{ts}.db')
            import shutil
            shutil.copy2(abs_path, pre_backup)

        # 覆盖当前数据库
        import shutil
        shutil.move(temp_path, abs_path)
        logger.info(f"数据库已从上传文件恢复: {upload_file.filename}")

        return jsonify({
            'success': True,
            'message': f'已从 {upload_file.filename} 恢复数据库，需重启服务后生效'
        })
    except Exception as e:
        logger.error(f"数据库恢复失败: {e}")
        return jsonify({'error': str(e)}), 500


@system_bp.route('/database/backups/<filename>', methods=['DELETE'])
def delete_backup(filename: str):
    """删除指定的备份文件"""
    import os
    try:
        # 安全校验：文件名不能含路径分隔符
        if '/' in filename or '\\' in filename or '..' in filename:
            return jsonify({'error': '非法文件名'}), 400

        backup_dir = os.path.join(_get_project_root(), 'data', 'backups')
        backup_path = os.path.join(backup_dir, filename)

        if not os.path.exists(backup_path):
            return jsonify({'error': '备份文件不存在'}), 404

        os.remove(backup_path)
        logger.info(f"已删除备份文件: {filename}")

        return jsonify({'success': True, 'message': f'已删除 {filename}'})
    except Exception as e:
        logger.error(f"删除备份文件失败: {e}")
        return jsonify({'error': str(e)}), 500


def _get_project_root() -> str:
    """获取项目根目录"""
    import os
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f'{size_bytes} B'
    elif size_bytes < 1024 * 1024:
        return f'{size_bytes / 1024:.1f} KB'
    elif size_bytes < 1024 * 1024 * 1024:
        return f'{size_bytes / (1024 * 1024):.2f} MB'
    else:
        return f'{size_bytes / (1024 * 1024 * 1024):.2f} GB'