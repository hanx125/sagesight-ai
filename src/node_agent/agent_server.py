"""节点管理 HTTP 服务：接收控制面下发的部署/停止指令。

对外接口与认证方式与边缘平台约定保持一致：
- 所有非 /health 路径需携带 X-Agent-Token 头部，值等于 AGENT_TOKEN
- 响应统一格式：{'code': 0, 'msg': 'success', 'data': {...}}
"""

import logging
import os
from typing import Optional

from flask import Flask, jsonify, request

from src.node_agent.workload_manager import WorkloadManager, find_available_port
from src.node_agent.media_manager import MediaStackManager
from src.system.system_config_loader import get_node_management_config

logger = logging.getLogger(__name__)


def _read_token() -> str:
    """读取 Agent 令牌（每次请求读取，支持运行时通过配置热更新）。"""
    return get_node_management_config().get('agent_token', '') or os.environ.get('AGENT_TOKEN', '')


def _ok(data=None):
    return jsonify({'code': 0, 'msg': 'success', 'data': data or {}})


def _err(msg: str, code: int = 1):
    return jsonify({'code': code, 'msg': msg, 'data': None}), 400


def _check_token():
    token = request.headers.get('X-Agent-Token', '')
    expected = _read_token()
    if not expected or token != expected:
        return False
    return True


def create_app(workload_manager: Optional[WorkloadManager] = None,
               media_manager: Optional[MediaStackManager] = None) -> Flask:
    """创建节点管理 Flask 应用。

    Args:
        workload_manager: 工作负载管理器实例（不传则内部新建）
        media_manager: 媒体栈管理器实例（不传则内部新建）

    Returns:
        Flask 应用实例
    """
    app = Flask(__name__)
    manager = workload_manager or WorkloadManager()
    media_mgr = media_manager or MediaStackManager()

    @app.before_request
    def auth_middleware():
        if request.path in ('/health',):
            return None
        if not _check_token():
            return _err('Agent 认证失败', 401)

    @app.get('/health')
    def health():
        return _ok({'status': 'ok'})

    @app.post('/workload/deploy')
    def deploy_workload():
        try:
            spec = request.get_json(force=True) or {}
            if not spec.get('workloadType') or not spec.get('workloadId'):
                return _err('workloadType 和 workloadId 必填')
            env = spec.get('env') or {}
            if not env.get('PORT'):
                port = find_available_port(int(env.get('START_PORT', 8000)))
                if port:
                    env['PORT'] = str(port)
                    spec['env'] = env
            data = manager.deploy(spec)
            return _ok(data)
        except Exception as e:
            logger.exception('部署失败')
            return _err(str(e))

    @app.post('/workload/stop')
    def stop_workload():
        try:
            body = request.get_json(force=True) or {}
            workload_type = body.get('workloadType')
            workload_id = body.get('workloadId')
            if not workload_type or not workload_id:
                return _err('workloadType 和 workloadId 必填')
            manager.stop(workload_type, workload_id)
            return _ok({'stopped': True})
        except Exception as e:
            logger.exception('停止失败')
            return _err(str(e))

    @app.get('/workload/list')
    def list_workloads():
        return _ok({'workloads': manager.list_workloads(), 'activeTasks': manager.active_count()})

    @app.post('/media/deploy')
    def deploy_media_stack():
        try:
            spec = request.get_json(force=True) or {}
            if not spec.get('stackType') and not spec.get('mediaType'):
                return _err('stackType 必填')
            data = media_mgr.deploy(spec)
            return _ok(data)
        except Exception as e:
            logger.exception('媒体栈部署失败')
            return _err(str(e))

    @app.post('/media/stop')
    def stop_media_stack():
        try:
            body = request.get_json(force=True) or {}
            stack_type = body.get('stackType') or body.get('mediaType')
            if not stack_type:
                return _err('stackType 必填')
            media_mgr.stop(stack_type)
            return _ok({'stopped': True})
        except Exception as e:
            logger.exception('媒体栈停止失败')
            return _err(str(e))

    # 暴露管理器实例给指标采集使用
    app.config['WORKLOAD_MANAGER'] = manager
    app.config['MEDIA_MANAGER'] = media_mgr
    return app
