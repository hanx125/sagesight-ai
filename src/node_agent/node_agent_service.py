"""节点管理主服务：注册、心跳、指标采集与 HTTP 服务启动。

指标采集复用 AI 平台已有的资源监测逻辑（CPU/内存/磁盘/GPU/NPU），
并叠加节点管理专属的工作负载字段，上报字段名与边缘平台约定保持一致。
"""

import logging
import platform
import socket
import threading
import time
from typing import Any, Dict, List, Optional

import psutil
import requests

from src.system.system_config_loader import get_node_management_config
from src.node_agent.node_agent_config import sync_to_environment

logger = logging.getLogger(__name__)


def _get_gpu_info() -> List[Dict[str, Any]]:
    """采集 GPU 信息（pynvml 优先，nvidia-smi 回退）。

    返回结构兼容边缘平台心跳字段：id/name/util/mem_used_mb/mem_total_mb。
    """
    # pynvml 方案
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
                'id': i,
                'name': name,
                'util': float(util.gpu),
                'mem_used_mb': round(mem_info.used / 1024 / 1024, 1),
                'mem_total_mb': round(mem_info.total / 1024 / 1024, 1),
            })
        pynvml.nvmlShutdown()
        if result:
            return result
    except Exception as e:
        logger.debug('pynvml GPU 采集跳过: %s', e)

    # nvidia-smi 回退
    try:
        import subprocess
        proc = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,utilization.gpu,memory.used,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result = []
            for line in proc.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 5:
                    result.append({
                        'id': int(parts[0]),
                        'name': parts[1],
                        'util': float(parts[2]) if parts[2] != '[N/A]' else 0,
                        'mem_used_mb': float(parts[3]) if parts[3] != '[N/A]' else 0,
                        'mem_total_mb': float(parts[4]) if parts[4] != '[N/A]' else 0,
                    })
            return result
    except Exception as e:
        logger.debug('nvidia-smi GPU 采集跳过: %s', e)

    return []


def _get_npu_info() -> List[Dict[str, Any]]:
    """采集 RK3588 NPU 使用率，返回与 GPU 相同结构的列表。"""
    try:
        from src.utils.platform_utils import is_rk3588
        if not is_rk3588():
            return []
    except Exception:
        return []

    import re
    try:
        with open('/sys/kernel/debug/rknpu/load', 'r') as f:
            content = f.read()
        cores = re.findall(r'Core\d+:\s*(\d+)%', content)
        if cores:
            util = sum(int(c) for c in cores) / len(cores)
            return [{
                'id': 0,
                'name': 'RK3588 NPU',
                'util': float(util),
                'mem_used_mb': 0,
                'mem_total_mb': 0,
            }]
    except Exception as e:
        logger.debug('NPU 采集跳过: %s', e)
    return []


class NodeAgentService:
    """节点管理服务（注册 + 心跳 + HTTP 服务）。"""

    def __init__(self):
        self._workload_manager = None
        self._media_manager = None
        self._flask_app = None
        self._server_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False

    # ========================================
    # 配置访问
    # ========================================

    def _config(self) -> Dict[str, Any]:
        return get_node_management_config()

    # ========================================
    # 指标采集（合并 AI 平台资源监测 + 工作负载）
    # ========================================

    def collect_metrics(self) -> Dict[str, Any]:
        """采集心跳上报所需的完整指标。

        字段命名与边缘平台约定保持一致（cpuPercent/memPercent/...）。
        CPU/内存/磁盘/GPU/NPU 与系统监控页共用同一套采集逻辑，
        工作负载字段由 WorkloadManager 提供。
        """
        active_tasks = 0
        workloads: List[Dict[str, Any]] = []
        if self._workload_manager is not None:
            try:
                active_tasks = self._workload_manager.active_count()
                workloads = self._workload_manager.list_workloads()
            except Exception as e:
                logger.debug('工作负载指标采集失败: %s', e)

        # 整机 CPU 利用率（psutil 已对多核取平均，勿对各核求和）
        cpu = round(psutil.cpu_percent(interval=0.5), 2)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        gpu_info = _get_gpu_info()
        if not gpu_info:
            gpu_info = _get_npu_info()

        return {
            'cpuPercent': cpu,
            'memPercent': round(mem.percent, 2),
            'memUsedBytes': mem.used,
            'memTotalBytes': mem.total,
            'diskPercent': round(disk.percent, 2),
            'diskUsedBytes': disk.used,
            'diskTotalBytes': disk.total,
            'activeTasks': active_tasks,
            'bandwidthMbps': 0,
            'gpuInfo': gpu_info,
            'workloads': workloads,
        }

    # ========================================
    # 控制面通信
    # ========================================

    def _post_json(self, path: str, payload: Dict[str, Any]) -> bool:
        cfg = self._config()
        base_url = (cfg.get('control_plane_url') or '').rstrip('/')
        if not base_url:
            logger.warning('控制面地址未配置，跳过上报')
            return False
        url = f'{base_url}{path}'
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 0:
                    return True
                logger.warning('请求失败 %s: %s', url, data.get('msg', data))
            else:
                logger.warning('HTTP %s: %s', resp.status_code, url)
        except Exception as e:
            logger.warning('请求异常 %s: %s', url, e)
        return False

    def _register(self) -> bool:
        cfg = self._config()
        node_id = cfg.get('node_id', '')
        agent_token = cfg.get('agent_token', '')
        if not node_id or not agent_token:
            logger.warning('节点 ID 或 Agent 令牌未配置，跳过注册')
            return False
        payload = {
            'nodeId': node_id,
            'agentToken': agent_token,
            'hostname': socket.gethostname(),
            'osInfo': f'{platform.system()} {platform.release()}',
            'agentVersion': cfg.get('agent_version', '1.0.0'),
            'cpuCores': psutil.cpu_count(logical=True),
        }
        logger.info('注册节点 nodeId=%s -> %s', node_id, cfg.get('control_plane_url'))
        return self._post_json('/register', payload)

    def _heartbeat(self) -> bool:
        cfg = self._config()
        metrics = self.collect_metrics()
        payload = {
            'nodeId': cfg.get('node_id', ''),
            'agentToken': cfg.get('agent_token', ''),
            **metrics,
        }
        return self._post_json('/heartbeat', payload)

    def _heartbeat_loop(self):
        """心跳主循环：先注册成功后再周期性上报心跳。"""
        while not self._stop_event.is_set():
            if not self._register():
                logger.info('注册失败，5 秒后重试...')
                if self._stop_event.wait(timeout=5):
                    return
                continue
            logger.info('注册成功，开始心跳 (interval=%ss)', self._config().get('heartbeat_interval', 10))
            break

        while not self._stop_event.is_set():
            if not self._heartbeat():
                logger.warning('心跳失败，尝试重新注册...')
                # 重新注册（注册失败会等待 5 秒，受 stop_event 控制可被打断）
                self._register()
            interval = self._config().get('heartbeat_interval', 10)
            if self._stop_event.wait(timeout=interval):
                return

    # ========================================
    # HTTP 服务（接收部署/停止指令）
    # ========================================

    def _run_flask_server(self):
        """在独立线程中运行节点管理 HTTP 服务。"""
        from src.node_agent.agent_server import create_app
        from werkzeug.serving import make_server

        cfg = self._config()
        host = cfg.get('agent_listen_host', '0.0.0.0')
        port = int(cfg.get('agent_listen_port', 9100))

        self._flask_app = create_app(
            workload_manager=self._workload_manager,
            media_manager=self._media_manager,
        )
        server = make_server(host, port, self._flask_app, threaded=True)
        logger.info('节点管理 HTTP 服务启动 %s:%s', host, port)
        # server.serve_forever 会被 shutdown() 打断
        server.serve_forever()
        logger.info('节点管理 HTTP 服务已停止')

    # ========================================
    # 生命周期
    # ========================================

    def start(self) -> bool:
        """启动节点管理服务（心跳线程 + HTTP 服务线程）。

        Returns:
            是否成功启动（已启动或配置未启用均返回 False 表示不启动）
        """
        if self._started:
            return False

        cfg = self._config()
        if not cfg.get('enabled', False):
            logger.info('节点管理未启用（node_management.enabled=false），跳过启动')
            return False

        node_id = cfg.get('node_id', '')
        agent_token = cfg.get('agent_token', '')
        if not node_id or not agent_token:
            logger.warning('节点管理已启用但 node_id/agent_token 未配置，跳过启动')
            return False

        # 同步配置到环境变量（供 workload_manager / media_manager 读取）
        sync_to_environment(cfg)

        # 延迟导入，避免模块加载阶段强依赖
        from src.node_agent.workload_manager import WorkloadManager
        from src.node_agent.media_manager import MediaStackManager
        self._workload_manager = WorkloadManager()
        self._media_manager = MediaStackManager()

        self._stop_event.clear()

        # 心跳线程
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name='NodeAgent-Heartbeat',
            daemon=True,
        )
        self._heartbeat_thread.start()

        # HTTP 服务线程
        self._server_thread = threading.Thread(
            target=self._run_flask_server,
            name='NodeAgent-HTTP',
            daemon=True,
        )
        self._server_thread.start()

        self._started = True
        logger.info('节点管理服务已启动: node_id=%s, control_plane=%s',
                    node_id, cfg.get('control_plane_url'))
        return True

    def stop(self):
        """停止节点管理服务。"""
        if not self._started:
            return
        self._stop_event.set()
        # 心跳线程为 daemon，会在 stop_event 触发后退出
        # HTTP 服务由 werkzeug server 持有，进程退出时随之结束
        self._started = False
        logger.info('节点管理服务已停止')


# 模块级单例（供主进程统一启停）
_singleton: Optional[NodeAgentService] = None


def get_node_agent_service() -> NodeAgentService:
    """获取节点管理服务单例。"""
    global _singleton
    if _singleton is None:
        _singleton = NodeAgentService()
    return _singleton
