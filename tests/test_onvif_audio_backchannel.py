"""
ONVIF Audio Back Channel 测试脚本

测试标准ONVIF协议的双向语音对讲功能，验证：
1. RTSP连接建立
2. Audio Back Channel支持检测
3. SDP解析（提取音频轨道）
4. RTSP SETUP建立反向通道
5. RTSP PLAY激活音频
6. 音频数据发送准备

使用方法：
python tests/test_onvif_audio_backchannel.py --ip 192.168.5.201 --user admin --password tfld2023
"""

import argparse
import sys
import logging

# 添加项目路径
sys.path.insert(0, 'e:\\sagesight-AI')

from src.cameras.onvif_audio_backchannel import test_onvif_audio_backchannel

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


def main():
    """
    主测试函数
    """
    # 解析参数
    parser = argparse.ArgumentParser(description='ONVIF Audio Back Channel测试脚本')
    parser.add_argument('--ip', required=True, help='摄像机IP地址（例如：192.168.5.201）')
    parser.add_argument('--port', type=int, default=554, help='RTSP端口（默认：554）')
    parser.add_argument('--user', default='admin', help='用户名（默认：admin）')
    parser.add_argument('--password', default='', help='密码')
    
    args = parser.parse_args()
    
    logger.info("=" * 80)
    logger.info("[启动] ONVIF Audio Back Channel标准协议测试")
    logger.info("=" * 80)
    logger.info(f"摄像机IP: {args.ip}")
    logger.info(f"RTSP端口: {args.port}")
    logger.info(f"用户名: {args.user}")
    logger.info("")
    
    # 执行测试
    result = test_onvif_audio_backchannel(
        camera_ip=args.ip,
        camera_port=args.port,
        username=args.user,
        password=args.password
    )
    
    # 显示测试结果
    logger.info("")
    logger.info("=" * 80)
    logger.info("[测试结果]")
    logger.info("=" * 80)
    
    if result['success']:
        logger.info("[SUCCESS] ✓ Audio Back Channel已成功建立！")
        logger.info("[INFO] 摄像机支持ONVIF标准的双向语音对讲")
        logger.info("[INFO] 可以向摄像机扬声器发送音频数据")
    else:
        logger.error(f"[FAILED] ✗ 测试失败: {result['error']}")
    
    logger.info("")
    logger.info(f"Audio Back Channel支持: {result['audio_backchannel_supported']}")
    logger.info(f"发现的音频轨道数量: {len(result['audio_tracks'])}")
    
    # 显示音频轨道详细信息
    if result['audio_tracks']:
        logger.info("")
        logger.info("[音频轨道信息]")
        for idx, track in enumerate(result['audio_tracks']):
            logger.info(f"  轨道 #{idx+1}:")
            logger.info(f"    - 类型: {track.get('type', 'unknown')}")
            logger.info(f"    - 端口: {track.get('port', 'unknown')}")
            logger.info(f"    - 编码: {track.get('codec', 'unknown')}")
            logger.info(f"    - 模式: {track.get('mode', 'unknown')}")
            logger.info(f"    - Track ID: {track.get('track_id', 'unknown')}")
            
            if track.get('mode') == 'sendonly':
                logger.info(f"    ✓ [Audio Back Channel] - 可以发送音频到摄像机")
            elif track.get('mode') == 'recvonly':
                logger.info(f"    ✓ [音频接收] - 可以接收摄像机的音频")
    
    logger.info("")
    logger.info("=" * 80)
    
    # 根据结果给出建议
    if result['success']:
        logger.info("[下一步]")
        logger.info("1. Audio Back Channel已建立，可以实现'平台向摄像机喊话'功能")
        logger.info("2. 需要实现音频编码（PCMA）和RTP发送模块")
        logger.info("3. 整合到现有的audio_talk系统")
    else:
        logger.info("[建议]")
        if not result['audio_backchannel_supported']:
            logger.info("1. 该摄像机不支持ONVIF Audio Back Channel")
            logger.info("2. 可能需要使用厂商私有协议（如海康威视ISAPI）")
            logger.info("3. 或者使用当前的ZLM单向语音方案")
        else:
            logger.info("1. 检查摄像机网络连接")
            logger.info("2. 验证用户名和密码")
            logger.info("3. 检查RTSP端口是否正确")
    
    logger.info("=" * 80)


if __name__ == "__main__":
    main()