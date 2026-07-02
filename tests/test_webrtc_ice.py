#!/usr/bin/env python3
"""
测试aiortc的ICE候选生成机制
"""
import asyncio
import json
import logging
import socket
import ipaddress
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer, MediaStreamTrack

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DummyAudioTrack(MediaStreamTrack):
    """测试音频轨道"""
    kind = "audio"
    
    async def recv(self):
        from av import AudioFrame
        frame = AudioFrame(format='s16', layout='mono', samples=8000)
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        return frame

async def test_ice_generation():
    """测试ICE候选生成"""
    
    # 获取本地IP地址
    hostname = socket.gethostname()
    local_ips = socket.gethostbyname_ex(hostname)[2]
    logger.info(f"本地IP地址: {local_ips}")
    
    # 过滤掉169.254.x.x
    valid_ips = [ip for ip in local_ips if not ip.startswith('169.254.')]
    logger.info(f"有效本地IP地址: {valid_ips}")
    
    # 测试1: 使用STUN服务器
    logger.info("\n" + "="*60)
    logger.info("测试1: 使用STUN服务器")
    logger.info("="*60)
    
    pc1 = RTCPeerConnection(configuration=RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=['stun:stun.l.google.com:19302']),
        ]
    ))
    
    # 添加音频轨道
    pc1.addTrack(DummyAudioTrack())
    
    ice_candidates_1 = []
    
    @pc1.on("icecandidate")
    def on_icecandidate1(candidate):
        logger.info(f"ICE候选回调触发: {candidate}")
        if candidate:
            logger.info(f"  候选内容: {candidate.candidate}")
            ice_candidates_1.append(str(candidate))
    
    # 创建Offer并等待ICE收集
    offer = await pc1.createOffer()
    await pc1.setLocalDescription(offer)
    
    logger.info(f"ICE收集状态: {pc1.iceGatheringState}")
    
    # 等待ICE收集完成
    if pc1.iceGatheringState != 'complete':
        logger.info("等待ICE收集...")
        await asyncio.sleep(5)  # 等待5秒
    
    logger.info(f"收集到 {len(ice_candidates_1)} 个ICE候选")
    for i, cand in enumerate(ice_candidates_1):
        logger.info(f"  候选 #{i+1}: {cand}")
    
    await pc1.close()
    
    # 测试2: 不使用STUN服务器
    logger.info("\n" + "="*60)
    logger.info("测试2: 不使用STUN服务器（仅host候选）")
    logger.info("="*60)
    
    pc2 = RTCPeerConnection(configuration=RTCConfiguration(
        iceServers=[]  # 不使用STUN
    ))
    
    # 添加音频轨道
    pc2.addTrack(DummyAudioTrack())
    
    ice_candidates_2 = []
    
    @pc2.on("icecandidate")
    def on_icecandidate2(candidate):
        logger.info(f"ICE候选回调触发: {candidate}")
        if candidate:
            logger.info(f"  候选内容: {candidate.candidate}")
            ice_candidates_2.append(str(candidate))
    
    # 创建Offer并等待ICE收集
    offer2 = await pc2.createOffer()
    await pc2.setLocalDescription(offer2)
    
    logger.info(f"ICE收集状态: {pc2.iceGatheringState}")
    
    # 等待ICE收集完成
    if pc2.iceGatheringState != 'complete':
        logger.info("等待ICE收集...")
        await asyncio.sleep(5)  # 等待5秒
    
    logger.info(f"收集到 {len(ice_candidates_2)} 个ICE候选")
    for i, cand in enumerate(ice_candidates_2):
        logger.info(f"  候选 #{i+1}: {cand}")
    
    await pc2.close()
    
    # 测试3: 检查aiortc版本和网络接口
    logger.info("\n" + "="*60)
    logger.info("测试3: aiortc版本和网络接口信息")
    logger.info("="*60)
    
    import aiortc
    logger.info(f"aiortc版本: {aiortc.__version__}")
    
    # 检查aioice版本
    import aioice
    logger.info(f"aioice版本: {aioice.__version__}")
    
    # 测试aioice的ICE候选生成
    from aioice import IceConnection
    
    ice_conn = IceConnection(ice_controlling=True)
    logger.info(f"aioice IceConnection创建成功")
    
    # 结论
    logger.info("\n" + "="*60)
    logger.info("结论:")
    logger.info("="*60)
    logger.info(f"测试1（使用STUN）: {len(ice_candidates_1)} 个候选")
    logger.info(f"测试2（不使用STUN）: {len(ice_candidates_2)} 个候选")
    
    if len(ice_candidates_2) == 0:
        logger.error("⚠️  aiortc没有生成任何host类型ICE候选！")
        logger.error("这可能是网络接口配置问题或aiortc库的问题")
    else:
        logger.info(f"✓ aiortc成功生成 {len(ice_candidates_2)} 个host类型候选")

if __name__ == '__main__':
    asyncio.run(test_ice_generation())