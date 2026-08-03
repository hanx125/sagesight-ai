"""节点管理客户端模块。

负责向边缘平台注册节点、上报心跳与系统指标，并接收控制面下发的
工作负载/媒体栈部署与停止指令。所有对外接口与字段名与边缘平台约定
保持一致，不得随意修改。
"""

from src.node_agent.node_agent_service import NodeAgentService, get_node_agent_service

__all__ = ['NodeAgentService', 'get_node_agent_service']
