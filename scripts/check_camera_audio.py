"""
摄像机音频能力检测脚本

用于检查摄像机是否支持语音对讲功能，包括：
1. ONVIF音频源配置
2. Audio Back Channel支持
3. 音频编码格式
4. RTSP音频流测试

使用方法：
python scripts/check_camera_audio.py --ip 192.168.5.201 --user admin --password tfld2023
"""

import argparse
import socket
import base64
import requests
import sys
from urllib.parse import quote

# 设置UTF-8编码（Windows兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    from onvif import ONVIFCamera
    ONVIF_AVAILABLE = True
except ImportError:
    ONVIF_AVAILABLE = False
    print("[!] ONVIF库未安装，请安装: pip install onvif-zeep")


def check_camera_audio_onvif(camera_ip, camera_port, username, password):
    """
    通过ONVIF协议检查摄像机音频配置
    """
    print("\n" + "="*60)
    print("[检查] ONVIF音频配置")
    print("="*60)
    
    if not ONVIF_AVAILABLE:
        print("[-] ONVIF库未安装，跳过此步骤")
        return False
    
    try:
        print(f"\n连接摄像机: {camera_ip}:{camera_port}")
        print(f"用户名: {username}")
        
        # 创建ONVIF连接
        cam = ONVIFCamera(camera_ip, camera_port, username, password)
        
        # 获取Media服务
        print("\n[Info] 获取Media服务...")
        media = cam.create_media_service()
        
        # 检查音频源配置
        print("\n[Audio] 检查音频源配置...")
        try:
            audio_sources = media.GetAudioSources()
            print(f"[+] 发现 {len(audio_sources)} 个音频源")
            
            for idx, source in enumerate(audio_sources):
                print(f"\n  音频源 #{idx+1}:")
                print(f"    - Token: {source.token}")
                print(f"    - Name: {source.Name if hasattr(source, 'Name') else 'N/A'}")
                print(f"    - Fixed: {source.Fixed if hasattr(source, 'Fixed') else 'N/A'}")
                
        except Exception as e:
            print(f"[-] 无法获取音频源: {e}")
            print("[!]  摄像机可能不支持音频输入")
        
        # 检查音频输出配置
        print("\n[Speaker] 检查音频输出配置...")
        try:
            audio_outputs = media.GetAudioOutputs()
            print(f"[+] 发现 {len(audio_outputs)} 个音频输出")
            
            for idx, output in enumerate(audio_outputs):
                print(f"\n  音频输出 #{idx+1}:")
                print(f"    - Token: {output.token}")
                print(f"    - Name: {output.Name if hasattr(output, 'Name') else 'N/A'}")
                
        except Exception as e:
            print(f"[-] 无法获取音频输出: {e}")
            print("[!]  摄像机可能不支持音频输出（扬声器）")
        
        # 检查音频编码器配置
        print("\n[Encoder] 检查音频编码器配置...")
        try:
            audio_encoders = media.GetAudioEncoderConfigurationOptions()
            print(f"[+] 音频编码器配置可用")
            
            if hasattr(audio_encoders, 'Options'):
                for option in audio_encoders.Options:
                    print(f"\n  编码器选项:")
                    if hasattr(option, 'Encoding'):
                        print(f"    - Encoding: {option.Encoding}")
                    if hasattr(option, 'BitrateList'):
                        print(f"    - BitrateList: {option.BitrateList}")
                    if hasattr(option, 'SampleRateList'):
                        print(f"    - SampleRateList: {option.SampleRateList}")
                        
        except Exception as e:
            print(f"[-] 无法获取音频编码器配置: {e}")
        
        return True
        
    except Exception as e:
        print(f"[-] ONVIF连接失败: {e}")
        return False


def check_audio_backchannel(camera_ip, camera_port, username, password):
    """
    检查摄像机是否支持Audio Back Channel（RTSP反向通道）
    """
    print("\n" + "="*60)
    print("[检查] 检查Audio Back Channel支持")
    print("="*60)
    
    try:
        print(f"\n测试RTSP Audio Back Channel...")
        
        # 创建RTSP socket
        rtsp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        rtsp_socket.settimeout(10.0)
        
        # 连接摄像机
        print(f"连接RTSP服务器: {camera_ip}:{camera_port}")
        rtsp_socket.connect((camera_ip, camera_port))
        
        # 编码认证信息
        auth_str = f"{username}:{password}"
        auth_encoded = base64.b64encode(auth_str.encode()).decode()
        
        # 发送RTSP DESCRIBE请求（包含Audio Back Channel要求）
        rtsp_url = f"rtsp://{quote(username)}:{quote(password)}@{camera_ip}:{camera_port}/audio"
        
        describe_request = (
            f"DESCRIBE {rtsp_url} RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"Require: www.onvif.org/ver20/backchannel\r\n"
            f"Authorization: Basic {auth_encoded}\r\n"
            f"User-Agent: ONVIF Audio Check\r\n"
            f"\r\n"
        )
        
        print(f"\n发送RTSP DESCRIBE请求...")
        print(f"URL: {rtsp_url}")
        rtsp_socket.send(describe_request.encode())
        
        # 接收响应
        response = rtsp_socket.recv(4096).decode()
        print(f"\n响应内容:")
        print(response[:500])  # 只显示前500字符
        
        rtsp_socket.close()
        
        # 检查响应
        if '200 OK' in response:
            print("\n[*] Audio Back Channel支持确认!")
            
            # 解析SDP内容
            if 'm=audio' in response:
                print("\n[Info] SDP音频信息:")
                
                # 检查音频编码
                if 'RTP/AVP 0' in response or 'PCMU' in response:
                    print("  [+] 支持编码: PCMU (G.711 μ-law)")
                if 'RTP/AVP 8' in response or 'PCMA' in response:
                    print("  [+] 支持编码: PCMA (G.711 A-law)")
                
                # 检查采样率
                if 'a=rtpmap:0 PCMU/8000' in response:
                    print("  [+] 采样率: 8000 Hz")
                
                # 检查通道数
                if 'a=rtpmap:0 PCMU/8000/1' in response:
                    print("  [+] 通道数: 1 (单声道)")
            
            return True
        else:
            print("\n[-] Audio Back Channel不支持")
            # 使用临时变量避免f-string中的反斜杠
            first_line = response.split('\r\n')[0]
            print(f"响应状态: {first_line}")
            return False
            
    except socket.timeout:
        print("\n[-] RTSP连接超时")
        print("[!]  摄像机可能未开启RTSP服务")
        return False
    except Exception as e:
        print(f"\n[-] RTSP检查失败: {e}")
        return False


def check_rtsp_audio_stream(camera_ip, camera_port, username, password):
    """
    测试RTSP音频流是否可访问
    """
    print("\n" + "="*60)
    print("[检查] 测试RTSP音频流")
    print("="*60)
    
    # 测试多种RTSP音频路径
    rtsp_paths = [
        "/audio",
        "/audio1",
        "/track2",
        "/Streaming/Channels/102",  # 海康威视音频通道
        "/h264/ch1/main/av_stream",  # 通用路径
    ]
    
    success_count = 0
    
    for path in rtsp_paths:
        rtsp_url = f"rtsp://{quote(username)}:{quote(password)}@{camera_ip}:{camera_port}{path}"
        
        try:
            print(f"\n测试路径: {path}")
            print(f"完整URL: {rtsp_url}")
            
            # 创建socket
            rtsp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            rtsp_socket.settimeout(5.0)
            rtsp_socket.connect((camera_ip, camera_port))
            
            # 编码认证
            auth_str = f"{username}:{password}"
            auth_encoded = base64.b64encode(auth_str.encode()).decode()
            
            # 发送DESCRIBE请求
            describe_request = (
                f"DESCRIBE {rtsp_url} RTSP/1.0\r\n"
                f"CSeq: 1\r\n"
                f"Authorization: Basic {auth_encoded}\r\n"
                f"\r\n"
            )
            
            rtsp_socket.send(describe_request.encode())
            response = rtsp_socket.recv(4096).decode()
            rtsp_socket.close()
            
            if '200 OK' in response:
                print(f"[*] 路径可用: {path}")
                success_count += 1
            else:
                print(f"[-] 路径不可用: {path}")
                
        except Exception as e:
            print(f"[-] 测试失败: {e}")
    
    print(f"\n总结: {success_count}/{len(rtsp_paths)} 个路径可用")
    
    return success_count > 0


def print_camera_config_guide():
    """
    打印摄像机音频配置指南
    """
    print("\n" + "="*60)
    print("[Guide] 摄像机音频配置指南")
    print("="*60)
    
    print("\n1️⃣  海康威视摄像机配置:")
    print("   - Web界面 → 配置 → 音频/视频")
    print("   - 启用音频输入: 选择麦克风类型（线性输入/麦克风输入）")
    print("   - 启用音频输出: 选择扬声器类型")
    print("   - 音频编码: 选择G.711 (PCM μ-law 或 A-law)")
    print("   - 采样率: 8000 Hz")
    print("   - 音频通道: 第2通道（通常102为音频通道）")
    
    print("\n2️⃣  大华摄像机配置:")
    print("   - Web界面 → 设置 → 音频设置")
    print("   - 启用音频输入")
    print("   - 音频编码: G.711A 或 G.711U")
    print("   - 采样率: 8KHz")
    
    print("\n3️⃣  ONVIF标准配置:")
    print("   - 摄像机需支持Profile T（双向语音）")
    print("   - Audio Back Channel支持")
    print("   - 音频编码: PCMU 或 PCMA")
    print("   - RTSP端口: 554")
    
    print("\n4️⃣  物理连接:")
    print("   - 音频输入: 连接外部麦克风到摄像机音频输入接口")
    print("   - 音频输出: 连接扬声器到摄像机音频输出接口")
    print("   - 检查音频线缆连接是否正确")
    
    print("\n5️⃣  网络配置:")
    print("   - RTSP端口: 554 (默认)")
    print("   - ONVIF端口: 80 (默认)")
    print("   - 确保防火墙允许RTSP连接")
    
    print("\n[!]  注意事项:")
    print("   - 某些摄像机可能需要手动启用音频功能")
    print("   - Audio Back Channel需要摄像机支持ONVIF Profile T")
    print("   - 音频编码必须使用G.711格式（PCMU或PCMA）")
    print("   - 采样率通常为8000 Hz")


def main():
    parser = argparse.ArgumentParser(description='检查摄像机音频配置')
    parser.add_argument('--ip', required=True, help='摄像机IP地址')
    parser.add_argument('--port', type=int, default=554, help='RTSP端口（默认554）')
    parser.add_argument('--onvif-port', type=int, default=80, help='ONVIF端口（默认80）')
    parser.add_argument('--user', required=True, help='用户名')
    parser.add_argument('--password', required=True, help='密码')
    
    args = parser.parse_args()
    
    print("\n" + "="*80)
    print("[目标] 摄像机音频能力检测工具")
    print("="*80)
    print(f"摄像机IP: {args.ip}")
    print(f"RTSP端口: {args.port}")
    print(f"ONVIF端口: {args.onvif_port}")
    print(f"用户名: {args.user}")
    
    # 检查ONVIF音频配置
    check_camera_audio_onvif(args.ip, args.onvif_port, args.user, args.password)
    
    # 检查Audio Back Channel
    check_audio_backchannel(args.ip, args.port, args.user, args.password)
    
    # 测试RTSP音频流
    check_rtsp_audio_stream(args.ip, args.port, args.user, args.password)
    
    # 打印配置指南
    print_camera_config_guide()
    
    print("\n" + "="*80)
    print("[*] 检测完成")
    print("="*80)
    print("\n如果检测结果为不支持，请按照上述配置指南调整摄像机设置。")


if __name__ == '__main__':
    main()