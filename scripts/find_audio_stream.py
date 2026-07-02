"""
查找摄像机RTSP音频流路径（支持Digest认证）

使用Digest认证查找正确的音频流路径，解决401 Unauthorized问题

使用方法：
python scripts/find_audio_stream.py --ip 192.168.5.201 --user admin --password tfld2023
"""

import argparse
import socket
import hashlib
import base64
import sys
import re
from urllib.parse import quote

# 设置UTF-8编码（Windows兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class RTSPDigestAuth:
    """RTSP Digest认证实现"""
    
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.realm = None
        self.nonce = None
        self.opaque = None
    
    def parse_auth_header(self, auth_header):
        """
        解析WWW-Authenticate头部
        
        Args:
            auth_header: WWW-Authenticate响应头部
        """
        # 提取realm
        realm_match = re.search(r'realm="([^"]+)"', auth_header)
        if realm_match:
            self.realm = realm_match.group(1)
        
        # 提取nonce
        nonce_match = re.search(r'nonce="([^"]+)"', auth_header)
        if nonce_match:
            self.nonce = nonce_match.group(1)
        
        # 提取opaque
        opaque_match = re.search(r'opaque="([^"]+)"', auth_header)
        if opaque_match:
            self.opaque = opaque_match.group(1)
    
    def compute_digest(self, method, uri):
        """
        计算Digest认证响应
        
        Args:
            method: RTSP方法（DESCRIBE, SETUP等）
            uri: RTSP URI
        
        Returns:
            str: Digest认证字符串
        """
        # 第一步：计算HA1 = MD5(username:realm:password)
        ha1_str = f"{self.username}:{self.realm}:{self.password}"
        ha1 = hashlib.md5(ha1_str.encode()).hexdigest()
        
        # 第二步：计算HA2 = MD5(method:uri)
        ha2_str = f"{method}:{uri}"
        ha2 = hashlib.md5(ha2_str.encode()).hexdigest()
        
        # 第三步：计算response = MD5(HA1:nonce:HA2)
        response_str = f"{ha1}:{self.nonce}:{ha2}"
        response = hashlib.md5(response_str.encode()).hexdigest()
        
        # 构建Digest认证字符串
        auth_str = f"Digest username=\"{self.username}\", realm=\"{self.realm}\", nonce=\"{self.nonce}\", uri=\"{uri}\", response=\"{response}\""
        
        if self.opaque:
            auth_str += f", opaque=\"{self.opaque}\""
        
        return auth_str


def test_rtsp_path_with_digest(camera_ip, camera_port, username, password, path):
    """
    使用Digest认证测试RTSP路径
    
    Args:
        camera_ip: 摄像机IP
        camera_port: RTSP端口
        username: 用户名
        password: 密码
        path: RTSP路径
    
    Returns:
        tuple: (success, response, auth_info)
    """
    rtsp_uri = f"rtsp://{camera_ip}:{camera_port}{path}"
    
    try:
        # 创建socket
        rtsp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        rtsp_socket.settimeout(10.0)
        rtsp_socket.connect((camera_ip, camera_port))
        
        # 第一次请求（无认证，获取realm和nonce）
        describe_request_1 = (
            f"DESCRIBE {rtsp_uri} RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"Require: www.onvif.org/ver20/backchannel\r\n"
            f"User-Agent: ONVIF Audio Check\r\n"
            f"\r\n"
        )
        
        rtsp_socket.send(describe_request_1.encode())
        response_1 = rtsp_socket.recv(4096).decode()
        
        # 检查是否需要Digest认证
        if '401 Unauthorized' in response_1 and 'WWW-Authenticate: Digest' in response_1:
            # 创建Digest认证对象
            auth = RTSPDigestAuth(username, password)
            
            # 解析认证头部
            auth_header_line = None
            for line in response_1.split('\r\n'):
                if 'WWW-Authenticate' in line:
                    auth_header_line = line
                    break
            
            if auth_header_line:
                auth.parse_auth_header(auth_header_line)
                
                # 计算Digest认证响应
                digest_response = auth.compute_digest("DESCRIBE", rtsp_uri)
                
                # 发送第二次请求（包含Digest认证）
                describe_request_2 = (
                    f"DESCRIBE {rtsp_uri} RTSP/1.0\r\n"
                    f"CSeq: 2\r\n"
                    f"Require: www.onvif.org/ver20/backchannel\r\n"
                    f"Authorization: {digest_response}\r\n"
                    f"User-Agent: ONVIF Audio Check\r\n"
                    f"\r\n"
                )
                
                rtsp_socket.send(describe_request_2.encode())
                response_2 = rtsp_socket.recv(4096).decode()
                
                rtsp_socket.close()
                
                if '200 OK' in response_2:
                    return True, response_2, auth
                else:
                    return False, response_2, auth
        
        else:
            # 不需要认证或认证失败
            rtsp_socket.close()
            return False, response_1, None
    
    except Exception as e:
        return False, str(e), None


def find_audio_stream_path(camera_ip, camera_port, username, password):
    """
    查找正确的音频流路径
    
    Args:
        camera_ip: 摄像机IP
        camera_port: RTSP端口
        username: 用户名
        password: 密码
    
    Returns:
        list: 可用的音频流路径列表
    """
    print("\n" + "="*60)
    print("[查找] RTSP音频流路径（Digest认证）")
    print("="*60)
    
    # 测试多种常见的音频流路径
    audio_paths = [
        # 海康威视常见路径
        "/Streaming/Channels/102",  # 音频通道（主码流102）
        "/Streaming/Channels/202",  # 音频通道（子码流202）
        "/h264/ch1/main/av_stream",  # 主码流（包含音频）
        "/h264/ch1/sub/av_stream",   # 子码流（包含音频）
        "/ISAPI/Streaming/Channels/102",
        
        # 通用ONVIF路径
        "/audio",
        "/audio1",
        "/audio2",
        "/track2",  # 第二个track（通常是音频）
        "/track3",
        
        # 其他常见路径
        "/live/audio",
        "/stream/audio",
        "/cam/realmonitor?channel=1&subtype=1",  # 通用路径
    ]
    
    valid_paths = []
    
    for path in audio_paths:
        print(f"\n[测试] 路径: {path}")
        
        success, response, auth = test_rtsp_path_with_digest(
            camera_ip, camera_port, username, password, path
        )
        
        if success:
            print(f"[+] 路径可用: {path}")
            print(f"[Info] 响应状态: 200 OK")
            
            # 检查是否包含音频
            if 'm=audio' in response:
                print(f"[+] 包含音频流!")
                
                # 解析音频信息
                if 'RTP/AVP 8' in response or 'PCMA' in response or 'G711' in response:
                    print(f"[+] 音频编码: G.711 A-law (PCMA)")
                if 'RTP/AVP 0' in response or 'PCMU' in response:
                    print(f"[+] 音频编码: G.711 U-law (PCMU)")
                if 'a=rtpmap' in response:
                    # 提取rtpmap信息
                    rtpmap_match = re.search(r'a=rtpmap:(\d+) ([^/\r\n]+)', response)
                    if rtpmap_match:
                        print(f"[+] RTP编码: {rtpmap_match.group(2)}")
            
            valid_paths.append({
                'path': path,
                'response': response,
                'has_audio': 'm=audio' in response
            })
        else:
            # 检查响应状态
            if '404 Not Found' in response:
                print(f"[-] 路径不存在: {path}")
            elif '401 Unauthorized' in response:
                print(f"[-] 认证失败: {path}")
            elif '400 Bad Request' in response:
                print(f"[-] 请求错误: {path}")
            else:
                # 使用临时变量避免f-string中的反斜杠
                first_line = response.split('\r\n')[0] if '\r\n' in response else response
                print(f"[-] 其他错误: {first_line}")
    
    print(f"\n[总结] 发现 {len(valid_paths)} 个可用路径")
    
    return valid_paths


def test_audio_backchannel_setup(camera_ip, camera_port, username, password, audio_path):
    """
    测试Audio Back Channel SETUP请求
    
    Args:
        camera_ip: 摄像机IP
        camera_port: RTSP端口
        username: 用户名
        password: 密码
        audio_path: 音频流路径
    
    Returns:
        tuple: (success, response, setup_info)
    """
    print("\n" + "="*60)
    print("[测试] Audio Back Channel SETUP")
    print("="*60)
    
    rtsp_uri = f"rtsp://{camera_ip}:{camera_port}{audio_path}"
    
    try:
        # 创建socket
        rtsp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        rtsp_socket.settimeout(10.0)
        rtsp_socket.connect((camera_ip, camera_port))
        
        # 第一步：DESCRIBE请求获取认证信息
        describe_request_1 = (
            f"DESCRIBE {rtsp_uri} RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"Require: www.onvif.org/ver20/backchannel\r\n"
            f"User-Agent: ONVIF Audio Backchannel Test\r\n"
            f"\r\n"
        )
        
        rtsp_socket.send(describe_request_1.encode())
        response_1 = rtsp_socket.recv(4096).decode()
        
        # 解析认证信息
        if '401 Unauthorized' in response_1 and 'WWW-Authenticate: Digest' in response_1:
            auth = RTSPDigestAuth(username, password)
            
            auth_header_line = None
            for line in response_1.split('\r\n'):
                if 'WWW-Authenticate' in line:
                    auth_header_line = line
                    break
            
            if auth_header_line:
                auth.parse_auth_header(auth_header_line)
                
                # 第二步：DESCRIBE请求（带认证）
                digest_response = auth.compute_digest("DESCRIBE", rtsp_uri)
                
                describe_request_2 = (
                    f"DESCRIBE {rtsp_uri} RTSP/1.0\r\n"
                    f"CSeq: 2\r\n"
                    f"Require: www.onvif.org/ver20/backchannel\r\n"
                    f"Authorization: {digest_response}\r\n"
                    f"User-Agent: ONVIF Audio Backchannel Test\r\n"
                    f"\r\n"
                )
                
                rtsp_socket.send(describe_request_2.encode())
                response_2 = rtsp_socket.recv(4096).decode()
                
                if '200 OK' in response_2:
                    print(f"[+] DESCRIBE成功")
                    
                    # 第三步：SETUP请求（建立Audio Back Channel）
                    # 提取Session ID（如果有的话）
                    session_id = None
                    
                    digest_response_setup = auth.compute_digest("SETUP", rtsp_uri)
                    
                    setup_request = (
                        f"SETUP {rtsp_uri} RTSP/1.0\r\n"
                        f"CSeq: 3\r\n"
                        f"Require: www.onvif.org/ver20/backchannel\r\n"
                        f"Authorization: {digest_response_setup}\r\n"
                        f"Transport: RTP/AVP;unicast;client_port=5000-5001;mode=record\r\n"
                        f"User-Agent: ONVIF Audio Backchannel Test\r\n"
                        f"\r\n"
                    )
                    
                    rtsp_socket.send(setup_request.encode())
                    response_3 = rtsp_socket.recv(4096).decode()
                    
                    rtsp_socket.close()
                    
                    if '200 OK' in response_3:
                        print(f"[+] SETUP成功！Audio Back Channel支持确认!")
                        print(f"[Info] SETUP响应:")
                        print(response_3[:500])
                        
                        # 提取Session信息
                        session_match = re.search(r'Session: ([^;\r\n]+)', response_3)
                        if session_match:
                            print(f"[+] Session ID: {session_match.group(1)}")
                        
                        # 提取Transport信息
                        transport_match = re.search(r'Transport: ([^\r\n]+)', response_3)
                        if transport_match:
                            print(f"[+] Transport: {transport_match.group(1)}")
                        
                        return True, response_3, {'session': session_match.group(1) if session_match else None}
                    else:
                        print(f"[-] SETUP失败")
                        # 使用临时变量避免f-string中的反斜杠
                        first_line = response_3.split('\r\n')[0]
                        print(f"[-] 响应: {first_line}")
                        return False, response_3, None
                else:
                    rtsp_socket.close()
                    return False, response_2, None
        
        else:
            rtsp_socket.close()
            return False, response_1, None
    
    except Exception as e:
        print(f"[-] 异常: {e}")
        return False, str(e), None


def main():
    parser = argparse.ArgumentParser(description='查找摄像机RTSP音频流路径（Digest认证）')
    parser.add_argument('--ip', required=True, help='摄像机IP地址')
    parser.add_argument('--port', type=int, default=554, help='RTSP端口（默认554）')
    parser.add_argument('--user', required=True, help='用户名')
    parser.add_argument('--password', required=True, help='密码')
    
    args = parser.parse_args()
    
    print("\n" + "="*80)
    print("[目标] 摄像机RTSP音频流路径查找工具（Digest认证）")
    print("="*80)
    print(f"摄像机IP: {args.ip}")
    print(f"RTSP端口: {args.port}")
    print(f"用户名: {args.user}")
    
    # 查找音频流路径
    valid_paths = find_audio_stream_path(args.ip, args.port, args.user, args.password)
    
    # 测试Audio Back Channel SETUP
    if valid_paths:
        print(f"\n[*] 发现可用音频流路径，尝试建立Audio Back Channel...")
        
        # 选择第一个包含音频的路径
        audio_path = None
        for path_info in valid_paths:
            if path_info['has_audio']:
                audio_path = path_info['path']
                break
        
        if not audio_path:
            # 使用第一个可用路径
            audio_path = valid_paths[0]['path']
        
        test_audio_backchannel_setup(args.ip, args.port, args.user, args.password, audio_path)
    else:
        print("\n[-] 未发现可用的音频流路径")
    
    print("\n" + "="*80)
    print("[*] 查找完成")
    print("="*80)
    
    if valid_paths:
        print("\n[+] 可用的音频流路径:")
        for path_info in valid_paths:
            status = "[音频]" if path_info['has_audio'] else "[视频]"
            print(f"  - {status} {path_info['path']}")
        
        print("\n[Guide] 下一步:")
        print("1. 使用以上路径更新audio_talk_api.py或audio_talk_service_onvif.py中的RTSP URL")
        print("2. 检查Audio Back Channel SETUP是否成功")
        print("3. 测试语音对讲功能")


if __name__ == '__main__':
    main()