"""
MQTT 输出器

负责将事件发送到 MQTT Broker
"""

import json
from typing import Dict
from urllib.parse import urlparse
import paho.mqtt.client as mqtt
import logging

logger = logging.getLogger(__name__)


class MQTTOutputter:
    """MQTT 元数据输出器"""
    
    def __init__(self, config: Dict):
        """
        初始化 MQTT 输出器
        
        Args:
            config: MQTT 配置字典
        """
        self.broker = config.get('broker', 'mqtt://localhost:1883')
        self.topic = config.get('topic', 'sagesight/events')
        self.qos = config.get('qos', 1)
        self.retain = config.get('retain', False)

        # 解析 broker URL（支持 mqtt:// / mqtts:// 前缀，兼容无端口）
        parsed = urlparse(self.broker)
        if parsed.hostname:
            # 带 scheme（mqtt://host:port 或 mqtts://host:port）
            self.host = parsed.hostname
            self.port = parsed.port or 1883
        else:
            # 无 scheme（host:port 或 host），手动分离
            broker_host = self.broker
            if ':' in broker_host:
                self.host, port_str = broker_host.rsplit(':', 1)
                self.port = int(port_str)
            else:
                self.host = broker_host
                self.port = 1883
        
        # 创建 MQTT 客户端
        self.client = mqtt.Client()
        
        # 设置回调
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        
        # 连接到 Broker
        try:
            self.client.connect(self.host, self.port, keepalive=60)
            self.client.loop_start()
            logger.debug(f"[MQTTOutputter] 已连接到 {self.host}:{self.port}")
        except Exception as e:
            logger.debug(f"[MQTTOutputter] 连接失败: {e}")
            raise
    
    def _on_connect(self, client, userdata, flags, rc):
        """连接回调"""
        if rc == 0:
            logger.debug(f"[MQTTOutputter] MQTT 连接成功")
        else:
            logger.debug(f"[MQTTOutputter] MQTT 连接失败，错误码: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """断开连接回调"""
        if rc != 0:
            logger.debug(f"[MQTTOutputter] MQTT 意外断开连接，错误码: {rc}")
            # 尝试重连
            try:
                self.client.reconnect()
            except Exception as e:
                logger.debug(f"[MQTTOutputter] 重连失败: {e}")
    
    def send(self, event: Dict):
        """
        发送事件到 MQTT
        
        Args:
            event: 事件字典
        """
        payload = json.dumps(event, ensure_ascii=False)
        self.client.publish(self.topic, payload, qos=self.qos, retain=self.retain)
    
    def close(self):
        """关闭 MQTT 连接"""
        self.client.loop_stop()
        self.client.disconnect()
        logger.debug(f"[MQTTOutputter] 已断开连接")
