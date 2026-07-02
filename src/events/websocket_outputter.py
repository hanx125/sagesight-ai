"""
WebSocket 输出器

用于向前端实时推送检测框、跟踪框、轨迹点等数据。
支持浏览器原生 WebSocket 连接。
"""

import asyncio
import json
import threading
from typing import Dict, Set, Optional
from datetime import datetime


class WebSocketOutputter:
    """WebSocket 输出器 - 向前端实时推送数据"""
    
    def __init__(self, config: Dict = None):
        """
        初始化 WebSocket 输出器
        
        Args:
            config: WebSocket 配置字典
        """
        self.config = config or {}
        
        # WebSocket 配置
        self.host = self.config.get('host', 'localhost')
        self.port = self.config.get('port', 8765)
        
        # 连接的客户端
        self.clients: Set = set()
        
        # WebSocket 服务器
        self.server = None
        
        # 异步循环
        self.loop = None
        self.thread = None
        
        # 是否已启动
        self.started = False
        
        # 消息队列（用于异步发送）
        self.message_queue = []
        
        print(f"[WebSocketOutputter] 初始化完成")
        print(f"[WebSocketOutputter]   地址: ws://{self.host}:{self.port}")
        print(f"[WebSocketOutputter]   用途: 前端实时绘制")
    
    def start(self):
        """启动 WebSocket 服务器（在独立线程中）"""
        if self.started:
            print(f"[WebSocketOutputter] 服务器已启动")
            return
        
        # 创建独立线程运行异步服务器
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()
        
        # 等待服务器启动
        import time
        time.sleep(1)
        
        self.started = True
        print(f"[WebSocketOutputter] 服务器已启动: ws://{self.host}:{self.port}")
    
    def _run_server(self):
        """在独立线程中运行 WebSocket 服务器"""
        try:
            import websockets
            
            # 创建异步循环
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            # 启动服务器
            start_server = websockets.serve(
                self._handle_connection,
                self.host,
                self.port
            )
            
            self.server = self.loop.run_until_complete(start_server)
            
            # 运行消息发送循环
            self.loop.run_until_complete(self._message_sender())
            
            # 保持运行
            self.loop.run_forever()
            
        except ImportError:
            print("[WebSocketOutputter] websockets 库未安装")
            print("[WebSocketOutputter] 请安装: pip install websockets")
        except Exception as e:
            print(f"[WebSocketOutputter] 服务器启动失败: {e}")
    
    async def _handle_connection(self, websocket, path):
        """处理 WebSocket 连接"""
        # 注册客户端
        client_addr = websocket.remote_address if websocket.remote_address else 'unknown'
        self.clients.add(websocket)
        print(f"[WebSocketOutputter] 新客户端连接: {client_addr} (总数: {len(self.clients)})")
        
        try:
            # 保持连接
            await websocket.wait_closed()
        finally:
            # 移除客户端
            self.clients.remove(websocket)
            print(f"[WebSocketOutputter] 客户端断开: {client_addr} (总数: {len(self.clients)})")
    
    async def _message_sender(self):
        """消息发送循环"""
        while True:
            # 检查消息队列
            if self.message_queue and self.clients:
                # 发送队列中的消息
                messages_to_send = self.message_queue[:10]  # 每次最多发送10条
                self.message_queue = self.message_queue[10:]
                
                # 发送到所有客户端
                for message in messages_to_send:
                    await self._send_to_clients(message)
            
            # 等待一小段时间
            await asyncio.sleep(0.01)
    
    async def _send_to_clients(self, message: str):
        """发送消息到所有客户端"""
        if not self.clients:
            return
        
        # 发送到所有客户端
        await asyncio.gather(
            *[client.send(message) for client in self.clients],
            return_exceptions=True
        )
    
    def send_realtime_data(self, data: Dict):
        """
        发送实时数据
        
        Args:
            data: 实时数据字典（检测框、跟踪框、轨迹点等）
        """
        if not self.started or not self.clients:
            return
        
        # 构建 WebSocket 消息
        message = json.dumps(data)
        
        # 添加到消息队列
        self.message_queue.append(message)
    
    def send_detection_frame(self, camera_id: str, frame_id: int, 
                            detections: list, tracks: list, 
                            trajectories: dict = None):
        """
        发送检测帧数据
        
        Args:
            camera_id: 摄像机 ID
            frame_id: 帧 ID
            detections: 检测结果列表
            tracks: 跟踪结果列表
            trajectories: 轨迹数据字典
        """
        # 构建实时数据
        realtime_data = {
            'type': 'detection_frame',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'camera_id': camera_id,
            'frame_id': frame_id,
            'detections': detections,
            'tracks': tracks,
            'trajectories': trajectories or {}
        }
        
        # 发送
        self.send_realtime_data(realtime_data)
    
    def send_trajectory_update(self, camera_id: str, track_id: int, 
                               trajectory_points: list):
        """
        发送轨迹更新
        
        Args:
            camera_id: 摄像机 ID
            track_id: 跟踪 ID
            trajectory_points: 轨迹点列表
        """
        # 构建轨迹数据
        trajectory_data = {
            'type': 'trajectory_update',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'camera_id': camera_id,
            'track_id': track_id,
            'points': trajectory_points
        }
        
        # 发送
        self.send_realtime_data(trajectory_data)
    
    def close(self):
        """关闭 WebSocket 服务器"""
        if self.server and self.loop:
            # 停止服务器
            self.server.close()
            
            # 停止异步循环
            self.loop.call_soon_threadsafe(self.loop.stop)
            
            print(f"[WebSocketOutputter] 服务器已关闭")
        
        self.started = False