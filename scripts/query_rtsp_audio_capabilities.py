"""
RTSP音频能力查询脚本

通过RTSP协议查询摄像机的音频能力：
1. OPTIONS：查询支持的RTSP方法（是否支持RECORD等）
2. DESCRIBE：获取SDP信息，查看音频轨道
3. SETUP尝试：测试Audio Back Channel支持
"""

import socket
import hashlib
import base64
import re
import time
from typing import Dict, Any, List, Optional
from urllib.parse import quote


class RTSPAudioCapabilityQuery:
    """RTSP音频能力查询"""
    
    def __init__(self, camera_ip: str, camera_port: int, username: str, password: str):
        """
        初始化
        
        Args:
            camera_ip: 摄像机IP
            camera_port: RTSP端口（通常是554）
            username: 用户名
            password: 密码
        """
        self.camera_ip = camera_ip
        self.camera_port = camera_port
        self.username = username
        self.password = password
        
        # RTSP连接
        self.socket = None
        self.cseq = 1
        
        # 认证信息
        self.auth_type = None
        self.realm = None
        self.nonce = None
        
    def connect(self) -> bool:
        """
        连接到摄像机RTSP服务器
        
        Returns:
            bool: 是否成功连接
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5.0)
            self.socket.connect((self.camera_ip, self.camera_port))
            print(f"[OK] 已连接到RTSP服务器: {self.camera_ip}:{self.camera_port}")
            return True
        except Exception as e:
            print(f"[ERROR] 连接失败: {e}")
            return False
    
    def disconnect(self):
        """关闭连接"""
        if self.socket:
            self.socket.close()
            self.socket = None
    
    def _parse_auth_header(self, response: str) -> Dict[str, Any]:
        """
        解析WWW-Authenticate header
        
        Args:
            response: RTSP响应
        
        Returns:
            Dict: 认证信息
        """
        auth_info = {
            'type': None,
            'realm': None,
            'nonce': None,
            'stale': None,
        }
        
        for line in response.split('\r\n'):
            if line.startswith('WWW-Authenticate:'):
                auth_line = line[len('WWW-Authenticate:'):].strip()
                
                if auth_line.startswith('Digest'):
                    auth_info['type'] = 'Digest'
                    params = auth_line[len('Digest'):].strip()
                    
                    realm_match = re.search(r'realm="([^"]+)"', params)
                    nonce_match = re.search(r'nonce="([^"]+)"', params)
                    stale_match = re.search(r'sale="([^"]+)"', params)
                    
                    if realm_match:
                        auth_info['realm'] = realm_match.group(1)
                    if nonce_match:
                        auth_info['nonce'] = nonce_match.group(1)
                    if stale_match:
                        auth_info['stale'] = stale_match.group(1)
                    
                elif auth_line.startswith('Basic'):
                    auth_info['type'] = 'Basic'
                    realm_match = re.search(r'realm="([^"]+)"', auth_line)
                    if realm_match:
                        auth_info['realm'] = realm_match.group(1)
                
                break
        
        return auth_info
    
    def _build_digest_auth(self, method: str, uri: str) -> str:
        """
        生成Digest认证头
        
        Args:
            method: RTSP方法
            uri: RTSP URI
        
        Returns:
            str: Digest认证头
        """
        ha1_str = f"{self.username}:{self.realm}:{self.password}"
        ha1 = hashlib.md5(ha1_str.encode()).hexdigest()
        
        ha2_str = f"{method}:{uri}"
        ha2 = hashlib.md5(ha2_str.encode()).hexdigest()
        
        response_str = f"{ha1}:{self.nonce}:{ha2}"
        response = hashlib.md5(response_str.encode()).hexdigest()
        
        return (
            f"Digest username=\"{self.username}\", "
            f"realm=\"{self.realm}\", "
            f"nonce=\"{self.nonce}\", "
            f"uri=\"{uri}\", "
            f"response=\"{response}\""
        )
    
    def _encode_basic_auth(self) -> str:
        """
        编码Basic认证
        
        Returns:
            str: Basic认证头
        """
        auth_str = f"{self.username}:{self.password}"
        return f"Basic {base64.b64encode(auth_str.encode()).decode()}"
    
    def send_request(self, method: str, uri: str, extra_headers: Dict[str, str] = None) -> str:
        """
        发送RTSP请求（自动处理认证）
        
        Args:
            method: RTSP方法（OPTIONS、DESCRIBE、SETUP等）
            uri: RTSP URI
            extra_headers: 额外的headers
        
        Returns:
            str: RTSP响应
        """
        # 构造请求
        request_lines = [
            f"{method} {uri} RTSP/1.0",
            f"CSeq: {self.cseq}",
        ]
        
        # 添加额外headers
        if extra_headers:
            for key, value in extra_headers.items():
                request_lines.append(f"{key}: {value}")
        
        # 添加认证（如果已知）
        if self.auth_type:
            if self.auth_type == 'Digest':
                auth_value = self._build_digest_auth(method, uri)
                request_lines.append(f"Authorization: {auth_value}")
            elif self.auth_type == 'Basic':
                auth_value = self._encode_basic_auth()
                request_lines.append(f"Authorization: {auth_value}")
        
        request_lines.append("\r\n")
        request = "\r\n".join(request_lines)
        
        # 发送请求
        self.socket.send(request.encode())
        
        # 接收响应
        response = self.socket.recv(4096).decode()
        
        # 检查是否需要认证
        if '401 Unauthorized' in response:
            auth_info = self._parse_auth_header(response)
            
            if auth_info['type']:
                self.auth_type = auth_info['type']
                self.realm = auth_info['realm']
                self.nonce = auth_info['nonce']
                
                print(f"检测到认证类型: {self.auth_type}, realm={self.realm}")
                
                # 重试请求（带认证）
                self.cseq += 1
                return self.send_request(method, uri, extra_headers)
        
        self.cseq += 1
        return response
    
    def query_options(self, uri: str) -> Dict[str, Any]:
        """
        查询RTSP OPTIONS（支持的方法）
        
        Args:
            uri: RTSP URI
        
        Returns:
            Dict: OPTIONS信息
        """
        print(f"\n{'='*60}")
        print(f"查询OPTIONS: {uri}")
        print(f"{'='*60}")
        
        response = self.send_request('OPTIONS', uri)
        
        result = {
            'status': None,
            'methods': [],
            'response': response
        }
        
        # 解析响应
        if '200 OK' in response:
            result['status'] = 'success'
            
            # 解析Public header（支持的方法）
            for line in response.split('\r\n'):
                if line.startswith('Public:'):
                    methods_str = line.split(':')[1].strip()
                    result['methods'] = [m.strip() for m in methods_str.split(',')]
                    
                    print(f"[OK] OPTIONS查询成功")
                    print(f"支持的RTSP方法: {result['methods']}")
                    
                    # 检查是否支持RECORD方法（Audio Back Channel）
                    if 'RECORD' in result['methods']:
                        print(f"[OK] 支持RECORD方法（可能支持Audio Back Channel）")
                    else:
                        print(f"[WARN] 不支持RECORD方法")
                    
                    break
        else:
            result['status'] = 'failed'
            print(f"[ERROR] OPTIONS查询失败")
            print(f"响应: {response}")
        
        return result
    
    def query_describe(self, uri: str) -> Dict[str, Any]:
        """
        查询DESCRIBE（获取SDP信息）
        
        Args:
            uri: RTSP URI
        
        Returns:
            Dict: DESCRIBE信息
        """
        print(f"\n{'='*60}")
        print(f"查询DESCRIBE: {uri}")
        print(f"{'='*60}")
        
        response = self.send_request('DESCRIBE', uri, {'Accept': 'application/sdp'})
        
        result = {
            'status': None,
            'audio_tracks': [],
            'video_tracks': [],
            'sdp': None,
            'response': response
        }
        
        # 解析响应
        if '200 OK' in response:
            result['status'] = 'success'
            
            # 分离SDP内容
            sdp_start = response.find('\r\n\r\n')
            if sdp_start > 0:
                sdp_content = response[sdp_start + 4:]
                result['sdp'] = sdp_content
                
                print(f"[OK] DESCRIBE查询成功")
                print(f"\nSDP内容:")
                print("-" * 60)
                
                # 解析SDP
                sdp_lines = sdp_content.split('\r\n')
                current_track = None
                
                for i, line in enumerate(sdp_lines):
                    print(f"  {i}: {line}")
                    
                    # 解析媒体类型
                    if line.startswith('m='):
                        media_type = line.split()[1]
                        if media_type == 'audio':
                            result['audio_tracks'].append({'line': line, 'index': i})
                            current_track = 'audio'
                        elif media_type == 'video':
                            result['video_tracks'].append({'line': line, 'index': i})
                            current_track = 'video'
                    
                    # 解析音频编码
                    if current_track == 'audio' and line.startswith('a=rtpmap:'):
                        codec_info = line.split(':')[1]
                        result['audio_tracks'][-1]['codec'] = codec_info
                
                print("-" * 60)
                
                if result['audio_tracks']:
                    print(f"\n[OK] 发现 {len(result['audio_tracks'])} 个音频轨道:")
                    for track in result['audio_tracks']:
                        print(f"  - 音频轨道: {track.get('line')}")
                        if 'codec' in track:
                            print(f"    编码格式: {track['codec']}")
                else:
                    print(f"\n[WARN] 未发现音频轨道")
                
                if result['video_tracks']:
                    print(f"\n[OK] 发现 {len(result['video_tracks'])} 个视频轨道:")
                    for track in result['video_tracks']:
                        print(f"  - 视频轨道: {track.get('line')}")
        else:
            result['status'] = 'failed'
            print(f"[ERROR] DESCRIBE查询失败")
            print(f"响应: {response}")
        
        return result
    
    def test_setup_audio_backchannel(self, uri: str, transport_params: List[str]) -> Dict[str, Any]:
        """
        测试SETUP Audio Back Channel
        
        Args:
            uri: RTSP URI
            transport_params: Transport参数列表
        
        Returns:
            Dict: SETUP测试结果
        """
        print(f"\n{'='*60}")
        print(f"测试SETUP Audio Back Channel: {uri}")
        print(f"{'='*60}")
        
        results = []
        
        for transport in transport_params:
            print(f"\n尝试Transport参数: {transport}")
            print("-" * 60)
            
            response = self.send_request('SETUP', uri, {
                'Require': 'www.onvif.org/ver20/backchannel',
                'Transport': transport
            })
            
            result = {
                'transport': transport,
                'status': None,
                'response': response
            }
            
            # 解析响应
            if '200 OK' in response:
                result['status'] = 'success'
                print(f"[OK] SETUP成功！Audio Back Channel支持已确认")
                
                # 解析Session ID
                for line in response.split('\r\n'):
                    if line.startswith('Session:'):
                        session_id = line.split(':')[1].strip().split(';')[0]
                        result['session_id'] = session_id
                        print(f"Session ID: {session_id}")
                        break
                
                # Audio Back Channel成功，可以停止测试
                results.append(result)
                break
                
            elif '415 Unsupported Media Type' in response:
                result['status'] = 'unsupported'
                print(f"[WARN] 415 Unsupported Media Type - Transport参数不正确或不支持Audio Back Channel")
                
            elif '404 Not Found' in response:
                result['status'] = 'not_found'
                print(f"[WARN] 404 Not Found - RTSP路径不正确")
                
            elif '401 Unauthorized' in response:
                result['status'] = 'auth_failed'
                print(f"[WARN] 401 Unauthorized - 认证失败")
                
            else:
                result['status'] = 'failed'
                print(f"[ERROR] SETUP失败")
                print(f"响应: {response[:200]}")
            
            results.append(result)
        
        return {
            'uri': uri,
            'results': results,
            'success': any(r['status'] == 'success' for r in results)
        }
    
    def query_audio_capabilities(self) -> Dict[str, Any]:
        """
        综合查询音频能力
        
        Returns:
            Dict: 音频能力信息
        """
        print(f"\n{'='*80}")
        print(f"RTSP音频能力查询 - {self.camera_ip}:{self.camera_port}")
        print(f"{'='*80}")
        
        if not self.connect():
            return {'success': False, 'error': '连接失败'}
        
        results = {
            'camera_ip': self.camera_ip,
            'camera_port': self.camera_port,
            'success': True,
            'options': None,
            'describe': None,
            'audio_backchannel_tests': []
        }
        
        try:
            # 测试不同的RTSP路径
            test_paths = [
                f"rtsp://{quote(self.username)}:{quote(self.password)}@{self.camera_ip}:{self.camera_port}/audio",
                f"rtsp://{quote(self.username)}:{quote(self.password)}@{self.camera_ip}:{self.camera_port}/audiobackchannel",
                f"rtsp://{quote(self.username)}:{quote(self.password)}@{self.camera_ip}:{self.camera_port}/Streaming/Channels/102",
                f"rtsp://{quote(self.username)}:{quote(self.password)}@{self.camera_ip}:{self.camera_port}/live/audio",
                f"rtsp://{quote(self.username)}:{quote(self.password)}@{self.camera_ip}:{self.camera_port}/h264/audio",
            ]
            
            # 测试OPTIONS
            options_result = self.query_options(test_paths[0])
            results['options'] = options_result
            
            # 测试DESCRIBE
            describe_result = self.query_describe(test_paths[0])
            results['describe'] = describe_result
            
            # 测试不同的Transport参数
            transport_params = [
                'RTP/AVP;unicast;client_port=5000-5001;mode=record',
                'RTP/AVP/TCP;unicast;interleaved=0-1;mode=record',
                'RTP/AVP;unicast;client_port=5000-5001',
            ]
            
            # 测试SETUP Audio Back Channel（不同的路径）
            for uri in test_paths:
                setup_result = self.test_setup_audio_backchannel(uri, transport_params)
                results['audio_backchannel_tests'].append(setup_result)
                
                # 如果找到成功的Audio Back Channel，可以停止测试
                if setup_result['success']:
                    break
            
        except Exception as e:
            print(f"\n[ERROR] 查询失败: {e}")
            results['success'] = False
            results['error'] = str(e)
        
        finally:
            self.disconnect()
        
        # 总结
        print(f"\n{'='*80}")
        print(f"音频能力查询总结")
        print(f"{'='*80}")
        
        if results['describe'] and results['describe']['audio_tracks']:
            print(f"[OK] 音频接收能力:")
            print(f"  - 音频轨道数量: {len(results['describe']['audio_tracks'])}")
            for track in results['describe']['audio_tracks']:
                if 'codec' in track:
                    print(f"  - 编码格式: {track['codec']}")
        
        audio_backchannel_supported = any(
            test['success'] for test in results['audio_backchannel_tests']
        )
        
        if audio_backchannel_supported:
            print(f"\n[OK] Audio Back Channel支持:")
            for test in results['audio_backchannel_tests']:
                if test['success']:
                    print(f"  - 成功路径: {test['uri']}")
                    for result in test['results']:
                        if result['status'] == 'success':
                            print(f"  - Transport参数: {result['transport']}")
                    break
        else:
            print(f"\n[ERROR] Audio Back Channel不支持:")
            print(f"  - 所有测试路径和Transport参数均失败")
        
        print(f"{'='*80}\n")
        
        return results


def main():
    """主函数"""
    import argparse
    
    # 命令行参数解析
    parser = argparse.ArgumentParser(description='RTSP音频能力查询脚本')
    parser.add_argument('--ip', default='192.168.5.201', help='摄像机IP地址')
    parser.add_argument('--port', default='554', help='RTSP端口')
    parser.add_argument('--username', default='admin', help='用户名')
    parser.add_argument('--password', default='tfld2023', help='密码')
    
    args = parser.parse_args()
    
    print("\nRTSP音频能力查询脚本")
    print("=" * 80)
    print("用途：")
    print("  1. 查询RTSP支持的OPTIONS方法（是否支持RECORD）")
    print("  2. 查询DESCRIBE获取SDP信息（音频轨道、编码格式）")
    print("  3. 测试SETUP Audio Back Channel（双向语音支持）")
    print("=" * 80)
    
    # 使用命令行参数
    camera_ip = args.ip
    camera_port = args.port
    username = args.username
    password = args.password
    
    print(f"\n查询参数:")
    print(f"  - IP地址: {camera_ip}")
    print(f"  - RTSP端口: {camera_port}")
    print(f"  - 用户名: {username}")
    print(f"  - 密码: {password}")
    
    # 创建查询器
    query = RTSPAudioCapabilityQuery(
        camera_ip=camera_ip,
        camera_port=int(camera_port),
        username=username,
        password=password
    )
    
    # 执行查询
    results = query.query_audio_capabilities()
    
    # 输出JSON结果（可选）
    print("\n完整查询结果（JSON格式）:")
    import json
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()