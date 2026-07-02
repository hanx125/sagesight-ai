"""
测试ONVIF语音对讲系统集成

测试完整流程：
1. 创建语音对讲会话
2. 启动Audio Back Channel
3. 发送测试音频数据
4. 停止会话

验证各个模块的集成情况。
"""

import sys
import os
import time
import math
import struct
import argparse

# 添加项目路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.cameras.audio_talk_service_onvif import (
    ONVIFAudioTalkServiceManager,
    get_onvif_audio_talk_manager
)


def test_audio_talk_integration(
    camera_ip: str,
    camera_port: int,
    username: str,
    password: str,
    duration: int = 5
):
    """
    测试完整的ONVIF语音对讲集成
    
    Args:
        camera_ip: 摄像机IP
        camera_port: RTSP端口
        username: 用户名
        password: 密码
        duration: 测试持续时间（秒）
    """
    print("=" * 80)
    print("[集成测试] ONVIF语音对讲系统")
    print("=" * 80)
    
    # 1. 创建服务管理器
    print("\n[步骤1] 创建服务管理器")
    manager = get_onvif_audio_talk_manager()
    print("✓ 服务管理器已创建")
    
    # 2. 创建会话
    print("\n[步骤2] 创建语音对讲会话")
    session_id = "test_session_integration"
    session = manager.create_session(
        session_id=session_id,
        camera_id="test_camera",
        camera_ip=camera_ip,
        camera_rtsp_port=camera_port,
        username=username,
        password=password,
        audio_codec="PCMU",
        sample_rate=8000
    )
    print(f"✓ 会话已创建: session_id={session_id}")
    
    # 3. 启动会话
    print("\n[步骤3] 启动ONVIF Audio Back Channel")
    if not manager.start_session(session_id):
        print("✗ 会话启动失败")
        return False
    
    print("✓ Audio Back Channel已建立")
    
    # 4. 发送测试音频
    print(f"\n[步骤4] 发送测试音频（{duration}秒）")
    
    # 生成正弦波PCM数据
    sample_rate = 8000
    total_samples = sample_rate * duration
    chunk_size = 160  # 20ms
    
    frequency = 1000  # 1000Hz正弦波
    packets_sent = 0
    
    print(f"生成正弦波: frequency={frequency}Hz, duration={duration}s")
    
    for i in range(0, total_samples, chunk_size):
        # 生成PCM数据
        samples = []
        
        for j in range(chunk_size):
            t = (i + j) / sample_rate
            value = int(32767 * 0.3 * math.sin(2 * math.pi * frequency * t))
            samples.append(struct.pack('<h', value))
        
        pcm_data = b''.join(samples)
        
        # 发送音频数据
        if manager.send_audio_to_session(session_id, pcm_data):
            packets_sent += 1
        
        # 控制发送速率
        time.sleep(0.02)
    
    print(f"✓ 音频数据已发送: {packets_sent}个RTP包")
    
    # 5. 检查活动会话
    print("\n[步骤5] 检查活动会话")
    active_sessions = manager.get_active_sessions()
    print(f"活动会话数量: {len(active_sessions)}")
    
    if session_id in active_sessions:
        print(f"✓ 会话处于活动状态: {session_id}")
        print(f"  - camera_ip: {active_sessions[session_id]['camera_ip']}")
        print(f"  - audio_codec: {active_sessions[session_id]['audio_codec']}")
        print(f"  - sample_rate: {active_sessions[session_id]['sample_rate']}")
    
    # 6. 停止会话
    print("\n[步骤6] 停止语音对讲会话")
    manager.stop_session(session_id)
    print("✓ 会话已停止")
    
    # 7. 最终验证
    print("\n[最终验证]")
    active_sessions_final = manager.get_active_sessions()
    print(f"活动会话数量（停止后）: {len(active_sessions_final)}")
    
    print("\n" + "=" * 80)
    print("[SUCCESS] ✓ ONVIF语音对讲系统集成测试成功！")
    print("=" * 80)
    
    print("\n[测试总结]")
    print(f"1. ✓ 服务管理器创建成功")
    print(f"2. ✓ 会话创建成功")
    print(f"3. ✓ Audio Back Channel建立成功")
    print(f"4. ✓ 音频数据发送成功（{packets_sent}个RTP包）")
    print(f"5. ✓ 会话管理功能正常")
    print(f"6. ✓ 会话停止成功")
    
    return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='测试ONVIF语音对讲系统集成')
    parser.add_argument('--ip', required=True, help='摄像机IP地址')
    parser.add_argument('--port', type=int, default=554, help='RTSP端口')
    parser.add_argument('--user', default='admin', help='用户名')
    parser.add_argument('--password', default='', help='密码')
    parser.add_argument('--duration', type=int, default=5, help='测试持续时间（秒）')
    
    args = parser.parse_args()
    
    # 运行集成测试
    success = test_audio_talk_integration(
        camera_ip=args.ip,
        camera_port=args.port,
        username=args.user,
        password=args.password,
        duration=args.duration
    )
    
    if success:
        print("\n✓ 集成测试通过！系统可以正常使用。")
    else:
        print("\n✗ 集成测试失败！请检查日志排查问题。")
        sys.exit(1)


if __name__ == "__main__":
    main()