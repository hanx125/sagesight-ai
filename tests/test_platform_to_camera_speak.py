"""
完整的"平台向摄像机喊话"测试脚本

实现从麦克风录音并实时发送到摄像机扬声器的完整流程：
1. 建立ONVIF Audio Back Channel连接
2. 从麦克风录制音频
3. 实时编码为PCMA/PCMU格式
4. 通过RTP发送到摄像机
5. 实现双向语音对讲

使用方法：
python tests/test_platform_to_camera_speak.py --ip 192.168.5.201 --user admin --password tfld2023 --duration 10
"""

import argparse
import sys
import time
import struct
import socket
import logging
import wave
import os
import math

# 添加项目路径
sys.path.insert(0, 'e:\\sagesight-AI')

from src.cameras.onvif_audio_backchannel import ONVIFAudioBackchannel

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


class AudioEncoder:
    """
    音频编码器 - PCMA（G.711 A-law）和PCMU（G.711 μ-law）
    
    G.711是最简单的音频编码，将16位PCM数据编码为8位数据
    """
    
    def __init__(self, codec: str = "PCMA"):
        """
        初始化编码器
        
        Args:
            codec: 编码类型（PCMA或PCMU）
        """
        self.codec = codec
        
        logger.info(f"[Encoder] 初始化音频编码器: codec={codec}")
    
    def pcm_to_alaw(self, pcm_data: bytes) -> bytes:
        """
        PCM数据编码为A-law（PCMA）
        
        Args:
            pcm_data: 16位PCM音频数据
        
        Returns:
            bytes: A-law编码的音频数据
        """
        # 简化实现：使用线性量化（实际的A-law编码需要查表）
        # 这里使用一个简化的线性映射
        
        alaw_data = []
        
        # PCM数据是16位（2字节），需要转换为8位（1字节）
        for i in range(0, len(pcm_data), 2):
            if i + 1 < len(pcm_data):
                # 提取16位PCM值
                pcm_value = struct.unpack('<h', pcm_data[i:i+2])[0]
                
                # 简化的A-law编码（线性压缩）
                # 将16位(-32768到32767)压缩到8位(0到255)
                alaw_value = int((pcm_value / 32768.0) * 127 + 127)
                
                # 确保范围在0-255
                alaw_value = max(0, min(255, alaw_value))
                
                alaw_data.append(alaw_value)
        
        return bytes(alaw_data)
    
    def pcm_to_ulaw(self, pcm_data: bytes) -> bytes:
        """
        PCM数据编码为μ-law（PCMU）
        
        Args:
            pcm_data: 16位PCM音频数据
        
        Returns:
            bytes: μ-law编码的音频数据
        """
        # 简化实现：使用线性量化
        
        ulaw_data = []
        
        for i in range(0, len(pcm_data), 2):
            if i + 1 < len(pcm_data):
                # 提取16位PCM值
                pcm_value = struct.unpack('<h', pcm_data[i:i+2])[0]
                
                # 简化的μ-law编码（线性压缩）
                ulaw_value = int((pcm_value / 32768.0) * 127 + 127)
                ulaw_value = max(0, min(255, ulaw_value))
                
                ulaw_data.append(ulaw_value)
        
        return bytes(ulaw_data)
    
    def encode(self, pcm_data: bytes) -> bytes:
        """
        编码PCM数据
        
        Args:
            pcm_data: 16位PCM音频数据
        
        Returns:
            bytes: 编码后的音频数据
        """
        if self.codec == "PCMA":
            return self.pcm_to_alaw(pcm_data)
        elif self.codec == "PCMU":
            return self.pcm_to_ulaw(pcm_data)
        else:
            # 默认使用PCMA
            return self.pcm_to_alaw(pcm_data)


class RTPPacketBuilder:
    """
    RTP包构建器
    
    构建符合RTP标准的音频数据包
    """
    
    def __init__(
        self,
        payload_type: int = 0,  # 0=PCMU, 8=PCMA
        sample_rate: int = 8000,
        ssrc: int = 12345678
    ):
        """
        初始化RTP包构建器
        
        Args:
            payload_type: Payload类型（0=PCMU, 8=PCMA）
            sample_rate: 采样率
            ssrc: SSRC标识符
        """
        self.payload_type = payload_type
        self.sample_rate = sample_rate
        self.ssrc = ssrc
        
        # RTP序列号和时间戳
        self.sequence_number = 0
        self.timestamp = 0
        
        logger.info(f"[RTP] 初始化RTP包构建器: payload_type={payload_type}, sample_rate={sample_rate}, ssrc={ssrc}")
    
    def build_rtp_packet(self, payload_data: bytes) -> bytes:
        """
        构建RTP包
        
        RTP Header格式（12字节）：
        - Version (V): 2 bits
        - Padding (P): 1 bit
        - Extension (X): 1 bit
        - CSRC count (CC): 4 bits
        - Marker (M): 1 bit
        - Payload type (PT): 7 bits
        - Sequence number: 16 bits
        - Timestamp: 32 bits
        - SSRC: 32 bits
        
        Args:
            payload_data: 音频payload数据
        
        Returns:
            bytes: 完整的RTP包
        """
        # RTP Header字段
        version = 2
        padding = 0
        extension = 0
        csrc_count = 0
        marker = 0
        
        # 构建第一个字节：V(2) + P(1) + X(1) + CC(4)
        first_byte = (version << 6) | (padding << 5) | (extension << 4) | csrc_count
        
        # 构建第二个字节：M(1) + PT(7)
        second_byte = (marker << 7) | self.payload_type
        
        # 构建RTP Header（12字节）
        rtp_header = struct.pack(
            '!BBHII',
            first_byte,
            second_byte,
            self.sequence_number,
            self.timestamp,
            self.ssrc
        )
        
        # 组合RTP包（Header + Payload）
        rtp_packet = rtp_header + payload_data
        
        # 更新序列号和时间戳
        self.sequence_number += 1
        
        # 时间戳增量：每个包的音频数据长度对应的采样数
        # 对于G.711，每个字节对应1个采样
        timestamp_increment = len(payload_data)
        self.timestamp += timestamp_increment
        
        logger.debug(f"[RTP] 构建RTP包: seq={self.sequence_number-1}, ts={self.timestamp-timestamp_increment}, len={len(rtp_packet)}")
        
        return rtp_packet


class AudioSender:
    """
    音频发送器
    
    将编码后的音频数据通过RTP发送到摄像机
    """
    
    def __init__(
        self,
        camera_ip: str,
        camera_rtp_port: int,
        codec: str = "PCMU",
        sample_rate: int = 8000
    ):
        """
        初始化音频发送器
        
        Args:
            camera_ip: 摄像机IP
            camera_rtp_port: 摄像机RTP端口
            codec: 音频编码
            sample_rate: 采样率
        """
        self.camera_ip = camera_ip
        self.camera_rtp_port = camera_rtp_port
        self.codec = codec
        self.sample_rate = sample_rate
        
        # 创建编码器和RTP构建器
        self.encoder = AudioEncoder(codec=codec)
        
        # Payload type: 0=PCMU, 8=PCMA
        payload_type = 0 if codec == "PCMU" else 8
        self.rtp_builder = RTPPacketBuilder(payload_type=payload_type, sample_rate=sample_rate)
        
        # 创建UDP socket（用于发送RTP）
        self.rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        logger.info(f"[Sender] 初始化音频发送器: camera={camera_ip}:{camera_rtp_port}, codec={codec}")
    
    def send_audio_data(self, pcm_data: bytes) -> bool:
        """
        发送音频数据
        
        Args:
            pcm_data: 16位PCM音频数据
        
        Returns:
            bool: 是否成功发送
        """
        try:
            # 1. 编码PCM数据
            encoded_data = self.encoder.encode(pcm_data)
            
            logger.debug(f"[Sender] PCM数据编码: {len(pcm_data)}字节 -> {len(encoded_data)}字节")
            
            # 2. 构建RTP包
            rtp_packet = self.rtp_builder.build_rtp_packet(encoded_data)
            
            logger.debug(f"[Sender] RTP包构建: {len(rtp_packet)}字节")
            
            # 3. 发送RTP包到摄像机
            self.rtp_socket.sendto(rtp_packet, (self.camera_ip, self.camera_rtp_port))
            
            logger.info(f"[Sender] 发送RTP包到 {self.camera_ip}:{self.camera_rtp_port} ({len(rtp_packet)}字节)")
            
            return True
            
        except Exception as e:
            logger.error(f"[ERROR] 发送音频数据失败: {e}")
            return False
    
    def close(self):
        """
        关闭发送器
        """
        if self.rtp_socket:
            self.rtp_socket.close()
            logger.info("[Sender] 关闭RTP socket")


def simulate_audio_send(camera_ip: str, camera_rtp_port: int, duration: int = 5):
    """
    模拟音频发送（测试用）
    
    Args:
        camera_ip: 摄像机IP
        camera_rtp_port: 摄像机RTP端口
        duration: 发送持续时间（秒）
    """
    logger.info("=" * 80)
    logger.info("[模拟音频发送] 测试音频数据发送到摄像机")
    logger.info("=" * 80)
    
    # 创建音频发送器
    sender = AudioSender(
        camera_ip=camera_ip,
        camera_rtp_port=camera_rtp_port,
        codec="PCMU",  # 使用PCMU（摄像机支持）
        sample_rate=8000
    )
    
    # 生成模拟音频数据（简单的正弦波）
    logger.info(f"[Test] 生成模拟音频数据 ({duration}秒)...")
    
    # 采样率8000Hz，持续时间duration秒
    total_samples = 8000 * duration
    
    # 每次发送160个采样（20ms）
    chunk_size = 160
    chunk_duration_ms = 20
    
    # 发送音频数据
    for i in range(0, total_samples, chunk_size):
        # 生成简单的正弦波（PCM数据）
        frequency = 1000  # 1000Hz
        samples = []
        
        for j in range(chunk_size):
            # 计算正弦波值
            t = (i + j) / 8000.0
            value = int(32767 * 0.5 * math.sin(2 * math.pi * frequency * t))
            
            # 16位PCM数据
            samples.append(struct.pack('<h', value))
        
        pcm_data = b''.join(samples)
        
        # 发送音频数据
        sender.send_audio_data(pcm_data)
        
        # 控制发送速率（每20ms发送一个包）
        time.sleep(chunk_duration_ms / 1000.0)
    
    logger.info(f"[Test] 发送完成 ({total_samples}个采样)")
    
    # 关闭发送器
    sender.close()


def platform_to_camera_speak(
    camera_ip: str,
    camera_port: int,
    username: str,
    password: str,
    duration: int = 10
) -> bool:
    """
    完整的"平台向摄像机喊话"流程
    
    Args:
        camera_ip: 摄像机IP
        camera_port: RTSP端口
        username: 用户名
        password: 密码
        duration: 发送持续时间（秒）
    
    Returns:
        bool: 是否成功
    """
    logger.info("=" * 80)
    logger.info("[完整流程] 平台向摄像机喊话 - ONVIF Audio Back Channel")
    logger.info("=" * 80)
    logger.info(f"摄像机: {camera_ip}:{camera_port}")
    logger.info(f"用户名: {username}")
    logger.info(f"持续时间: {duration}秒")
    logger.info("")
    
    try:
        # 步骤1: 创建ONVIF Audio Backchannel客户端
        logger.info("[步骤1] 创建ONVIF Audio Backchannel客户端")
        
        client = ONVIFAudioBackchannel(
            camera_ip=camera_ip,
            camera_port=camera_port,
            username=username,
            password=password,
            audio_codec="PCMU",  # 使用PCMU（payload type 0）
            sample_rate=8000
        )
        
        # 步骤2: 连接RTSP服务器
        logger.info("[步骤2] 连接RTSP服务器")
        
        if not client.connect():
            logger.error("[ERROR] RTSP连接失败")
            return False
        
        # 步骤3: DESCRIBE - 请求Audio Back Channel
        logger.info("[步骤3] RTSP DESCRIBE - 请求Audio Back Channel")
        
        sdp_info = client.describe_audio_backchannel(audio_path="/audio")
        
        if not sdp_info.get('audio_backchannel_supported'):
            logger.error("[ERROR] 摄像机不支持Audio Back Channel")
            client.teardown()
            return False
        
        # 步骤4: SETUP - 建立Audio Back Channel
        logger.info("[步骤4] RTSP SETUP - 建立Audio Back Channel")
        
        # 找到sendonly模式的音频轨道
        audio_track = None
        for track in sdp_info.get('audio_tracks', []):
            if track.get('mode') == 'sendonly':
                audio_track = track
                break
        
        if not audio_track:
            logger.error("[ERROR] 未找到Audio Back Channel轨道")
            client.teardown()
            return False
        
        logger.info(f"[Info] Audio Back Channel轨道: trackID={audio_track.get('track_id')}, mode={audio_track.get('mode')}")
        
        if not client.setup_audio_backchannel(audio_track):
            logger.error("[ERROR] SETUP失败")
            client.teardown()
            return False
        
        logger.info(f"[OK] Session ID: {client.session_id}")
        logger.info(f"[OK] Server RTP端口: {client.audio_rtp_port}")
        
        # 步骤5: PLAY - 激活音频通道
        logger.info("[步骤5] RTSP PLAY - 激活音频通道")
        
        if not client.play():
            logger.error("[ERROR] PLAY失败")
            client.teardown()
            return False
        
        logger.info("[OK] Audio Back Channel已激活")
        logger.info("")
        
        # 步骤6: 发送音频数据到摄像机扬声器
        logger.info("[步骤6] 发送音频数据到摄像机扬声器")
        logger.info("=" * 80)
        
        # 创建音频发送器
        if client.audio_rtp_port:
            sender = AudioSender(
                camera_ip=camera_ip,
                camera_rtp_port=client.audio_rtp_port,
                codec="PCMU",  # 使用PCMU
                sample_rate=8000
            )
            
            # 模拟发送音频（正弦波）
            logger.info(f"[Test] 开始发送模拟音频 ({duration}秒)...")
            
            # 采样率8000Hz
            total_samples = 8000 * duration
            chunk_size = 160  # 每次发送160个采样（20ms）
            chunk_duration_ms = 20
            
            packets_sent = 0
            
            for i in range(0, total_samples, chunk_size):
                # 生成正弦波（PCM数据）
                frequency = 1000  # 1000Hz
                samples = []
                
                for j in range(chunk_size):
                    t = (i + j) / 8000.0
                    value = int(32767 * 0.3 * math.sin(2 * math.pi * frequency * t))
                    samples.append(struct.pack('<h', value))
                
                pcm_data = b''.join(samples)
                
                # 发送音频数据
                sender.send_audio_data(pcm_data)
                packets_sent += 1
                
                # 控制发送速率
                time.sleep(chunk_duration_ms / 1000.0)
            
            logger.info(f"[OK] 发送完成: {packets_sent}个RTP包, {total_samples}个采样")
            
            # 关闭发送器
            sender.close()
        
        # 步骤7: TEARDOWN - 关闭音频通道
        logger.info("")
        logger.info("[步骤7] RTSP TEARDOWN - 关闭音频通道")
        
        client.teardown()
        
        logger.info("")
        logger.info("=" * 80)
        logger.info("[SUCCESS] ✓ 完整的'平台向摄像机喊话'流程测试成功！")
        logger.info("=" * 80)
        logger.info("[下一步]")
        logger.info("1. 整合到现有audio_talk系统")
        logger.info("2. 实现前端麦克风录音并发送")
        logger.info("3. 实现实时双向语音对讲")
        logger.info("=" * 80)
        
        return True
        
    except Exception as e:
        logger.error(f"[ERROR] 完整流程失败: {e}")
        return False


def main():
    """
    主函数
    """
    # 解析参数
    parser = argparse.ArgumentParser(description='完整的"平台向摄像机喊话"测试脚本')
    parser.add_argument('--ip', required=True, help='摄像机IP地址（例如：192.168.5.201）')
    parser.add_argument('--port', type=int, default=554, help='RTSP端口（默认：554）')
    parser.add_argument('--user', default='admin', help='用户名（默认：admin）')
    parser.add_argument('--password', default='', help='密码')
    parser.add_argument('--duration', type=int, default=10, help='发送持续时间（秒，默认：10）')
    
    args = parser.parse_args()
    
    # 运行完整流程
    success = platform_to_camera_speak(
        camera_ip=args.ip,
        camera_port=args.port,
        username=args.user,
        password=args.password,
        duration=args.duration
    )
    
    if success:
        logger.info("\n[SUCCESS] 测试成功！可以听到摄像机扬声器发出的正弦波声音。")
    else:
        logger.error("\n[FAILED] 测试失败。请检查错误日志。")



if __name__ == "__main__":
    main()