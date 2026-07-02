"""
MQTT 输出器

负责将事件发送到 MQTT Broker
"""

import json
import paho.mqtt.client as mqtt


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
        
        # 解析 broker URL
        if self.broker.startswith('mqtt://'):
            self.broker_host = self.broker[6:]
        else:
            self.broker_host = self.broker
        
        # 分离主机和端口
        if ':' in self.broker_host:
            self.host, port_str = self.broker_host.rsplit(':', 1)
            self.port = int(port_str)
        else:
            self.host = self.broker_host
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
            print(f"[MQTTOutputter] 已连接到 {self.host}:{self.port}")
        except Exception as e:
            print(f"[MQTTOutputter] 连接失败: {e}")
            raise
    
    def _on_connect(self, client, userdata, flags, rc):
        """连接回调"""
        if rc == 0:
            print(f"[MQTTOutputter] MQTT 连接成功")
        else:
            print(f"[MQTTOutputter] MQTT 连接失败，错误码: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """断开连接回调"""
        if rc != 0:
            print(f"[MQTTOutputter] MQTT 意外断开连接，错误码: {rc}")
            # 尝试重连
            try:
                self.client.reconnect()
            except Exception as e:
                print(f"[MQTTOutputter] 重连失败: {e}")
    
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
        print(f"[MQTTOutputter] 已断开连接")
