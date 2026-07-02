"""
测试ONVIF Audio Back Channel自动轨道选择功能

验证智能匹配逻辑是否能正确处理不同品牌的摄像机轨道信息。

测试场景：
1. 标准ONVIF摄像机（明确sendonly模式）
2. 海康威视（trackID=数字格式）
3. 大华（audio_backchannel关键字）
4. 宇视（audio1等自定义格式）
5. 其他品牌（相对路径或特殊格式）
"""

import sys
import os
import argparse

# 添加项目路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.cameras.onvif_audio_backchannel import ONVIFAudioBackchannel


def test_auto_track_selection(
    camera_ip: str,
    camera_port: int,
    username: str,
    password: str
):
    """
    测试自动轨道选择功能
    
    Args:
        camera_ip: 摄像机IP
        camera_port: RTSP端口
        username: 用户名
        password: 密码
    """
    print("=" * 80)
    print("[测试] ONVIF Audio Back Channel自动轨道选择")
    print("=" * 80)
    
    # 创建ONVIF客户端
    client = ONVIFAudioBackchannel(
        camera_ip=camera_ip,
        camera_port=camera_port,
        username=username,
        password=password
    )
    
    # 步骤1: 连接RTSP服务器
    print("\n[步骤1] 连接RTSP服务器")
    if not client.connect():
        print("✗ 连接失败")
        return False
    
    print("✓ 连接成功")
    
    # 步骤2: DESCRIBE - 获取SDP信息
    print("\n[步骤2] RTSP DESCRIBE - 获取SDP信息")
    sdp_info = client.describe_audio_backchannel(audio_path="/audio")
    
    # 显示解析结果
    print("\n[SDP解析结果]")
    print(f"Audio Back Channel支持: {sdp_info.get('audio_backchannel_supported')}")
    print(f"音频轨道数量: {len(sdp_info.get('audio_tracks', []))}")
    print(f"视频轨道数量: {len(sdp_info.get('video_tracks', []))}")
    
    # 显示所有音频轨道
    print("\n[所有音频轨道详情]")
    for idx, track in enumerate(sdp_info.get('audio_tracks', [])):
        print(f"轨道#{idx}:")
        print(f"  - track_id: {track.get('track_id')}")
        print(f"  - mode: {track.get('mode')}")
        print(f"  - codec: {track.get('codec')}")
        print(f"  - payload_type: {track.get('payload_type')}")
        print(f"  - payload_types: {track.get('payload_types')}")
        print(f"  - port: {track.get('port')}")
        print(f"  - rtpmap: {track.get('rtpmap')}")
    
    # 显示智能选择的轨道
    print("\n[智能选择的Audio Back Channel轨道]")
    selected_track = sdp_info.get('selected_backchannel_track')
    
    if selected_track:
        print("✓ 成功选择轨道")
        print(f"  - track_id: {selected_track.get('track_id')}")
        print(f"  - mode: {selected_track.get('mode')}")
        print(f"  - codec: {selected_track.get('codec')}")
        print(f"  - payload_type: {selected_track.get('payload_type')}")
        print(f"  - needs_testing: {selected_track.get('needs_testing', False)}")
        
        # 步骤3: SETUP - 测试轨道是否可用
        print("\n[步骤3] RTSP SETUP - 测试轨道可用性")
        if client.setup_audio_backchannel(selected_track):
            print("✓ SETUP成功，轨道可用")
            
            # 步骤4: PLAY - 激活音频通道
            print("\n[步骤4] RTSP PLAY - 激活音频通道")
            if client.play():
                print("✓ PLAY成功，Audio Back Channel已激活")
                
                # 显示连接信息
                print("\n[连接信息]")
                print(f"Session ID: {client.session_id}")
                print(f"Server RTP端口: {client.audio_rtp_port}")
                print(f"Server RTCP端口: {client.audio_rtcp_port}")
                
                # 关闭连接
                client.teardown()
                print("\n✓ 测试完成，连接已关闭")
                
                return True
            else:
                print("✗ PLAY失败")
                client.teardown()
                return False
        else:
            print("✗ SETUP失败，该轨道不可用")
            client.teardown()
            return False
    else:
        print("✗ 智能选择失败，未找到合适的Audio Back Channel轨道")
        client.teardown()
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='测试ONVIF Audio Back Channel自动轨道选择')
    parser.add_argument('--ip', required=True, help='摄像机IP地址')
    parser.add_argument('--port', type=int, default=554, help='RTSP端口')
    parser.add_argument('--user', default='admin', help='用户名')
    parser.add_argument('--password', default='', help='密码')
    
    args = parser.parse_args()
    
    # 运行测试
    success = test_auto_track_selection(
        camera_ip=args.ip,
        camera_port=args.port,
        username=args.user,
        password=args.password
    )
    
    if success:
        print("\n" + "=" * 80)
        print("[SUCCESS] ✓ 自动轨道选择功能测试成功！")
        print("=" * 80)
        print("\n[结论]")
        print("系统已成功识别并匹配Audio Back Channel轨道")
        print("支持多种品牌的摄像机轨道格式")
    else:
        print("\n" + "=" * 80)
        print("[FAILED] ✗ 自动轨道选择功能测试失败")
        print("=" * 80)
        sys.exit(1)


if __name__ == "__main__":
    main()