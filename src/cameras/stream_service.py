"""
流管理服务

通过ZLMediaKit代理拉流，实现摄像机的流媒体管理。
流APP固定为"AI"，流ID固定为摄像机ID（UUID）。
"""

import logging
import requests
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


# ========================================
# ZLMediaKit API 工具函数
# ========================================

def call_zlm_api(server_host: str, http_port: int, secret: str, api_path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    调用ZLMediaKit API
    
    Args:
        server_host: ZLM服务器主机地址
        http_port: API端口
        secret: API密钥
        api_path: API路径（如 /index/api/addStreamProxy）
        params: 请求参数
    
    Returns:
        API响应数据
    
    Raises:
        ValueError: API调用失败
    """
    url = f"http://{server_host}:{http_port}{api_path}"
    
    # 添加secret参数
    params['secret'] = secret
    
    try:
        response = requests.post(url, json=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('code') != 0:
            error_msg = data.get('msg', 'Unknown error')
            logger.error(f"ZLMediaKit API调用失败: {url}, 错误: {error_msg}")
            raise ValueError(f"流媒体服务器API调用失败: {error_msg}")
        
        logger.info(f"ZLMediaKit API调用成功: {url}")
        return data
        
    except requests.exceptions.RequestException as e:
        logger.error(f"ZLMediaKit API请求失败: {url}, 错误: {str(e)}")
        raise ValueError(f"流媒体服务器连接失败: {str(e)}")


def start_stream_proxy(server_host: str, http_port: int, secret: str, rtsp_url: str, app: str, stream_id: str) -> Dict[str, Any]:
    """
    在ZLMediaKit上启动流代理
    
    Args:
        server_host: ZLM服务器主机地址
        http_port: API端口
        secret: API密钥
        rtsp_url: RTSP源地址
        app: 应用名（固定为"AI"）
        stream_id: 流ID（摄像机UUID）
    
    Returns:
        API响应数据
    """
    params = {
        'vhost': '__defaultVhost__',
        'app': app,
        'stream': stream_id,
        'url': rtsp_url,
        'enable_hls': True,  # 启用HLS
        'enable_mp4': False,  # 不启用MP4录制
        'rtp_type': 'tcp',  # 使用TCP传输
    }
    
    return call_zlm_api(server_host, http_port, secret, '/index/api/addStreamProxy', params)


def stop_stream_proxy(server_host: str, http_port: int, secret: str, app: str, stream_id: str) -> Dict[str, Any]:
    """
    在ZLMediaKit上停止流
    
    Args:
        server_host: ZLM服务器主机地址
        http_port: API端口
        secret: API密钥
        app: 应用名（固定为"AI"）
        stream_id: 流ID（摄像机UUID）
    
    Returns:
        API响应数据
    """
    params = {
        'schema': 'rtsp',  # 协议类型
        'vhost': '__defaultVhost__',  # 虚拟主机
        'app': app,
        'stream': stream_id,
        'force': True,  # 强制关闭
    }
    
    return call_zlm_api(server_host, http_port, secret, '/index/api/close_stream', params)


def check_stream_online(server_host: str, http_port: int, secret: str, app: str, stream_id: str) -> bool:
    """
    检查流在ZLMediaKit中是否存在
    
    Args:
        server_host: ZLM服务器主机地址
        http_port: API端口
        secret: API密钥
        app: 应用名（固定为"AI"）
        stream_id: 流ID（摄像机UUID）
    
    Returns:
        True表示流存在，False表示流不存在
    """
    params = {
        'schema': 'rtsp',  # 协议类型
        'vhost': '__defaultVhost__',  # 虚拟主机
        'app': app,
        'stream': stream_id,
    }
    
    try:
        result = call_zlm_api(server_host, http_port, secret, '/index/api/isMediaOnline', params)
        # ZLMediaKit返回的数据结构：{code: 0, online: true/false}
        return result.get('online', False)
    except ValueError as e:
        # 如果API返回错误（如流不存在），返回False
        logger.warning(f"检查流状态失败: {str(e)}")
        return False
    except Exception as e:
        logger.warning(f"检查流状态失败: {str(e)}")
        return False


def get_stream_media_info(server_host: str, http_port: int, secret: str, app: str, stream_id: str) -> Dict[str, Any]:
    """
    获取ZLM流的媒体信息（包括原始尺寸）

    Args:
        server_host: ZLM服务器主机地址
        http_port: API端口
        secret: API密钥
        app: 应用名（固定为"AI"）
        stream_id: 流ID（摄像机UUID）

    Returns:
        媒体信息字典，包含：
        - width: 视频宽度（原始尺寸）
        - height: 视频高度（原始尺寸）
        - codec: 视频编码格式
        - fps: 帧率
        - bitrate: 码率
    """
    params = {
        'schema': 'rtsp',
        'vhost': '__defaultVhost__',
        'app': app,
        'stream': stream_id,
    }

    try:
        result = call_zlm_api(server_host, http_port, secret, '/index/api/getMediaInfo', params)

        # 解析 ZLM 返回的媒体信息
        # ZLM 返回格式：{code: 0, data: {tracks: [{type: 'video', codec: 'H264', width: 1920, height: 1080, ...}]}}
        if 'data' in result and 'tracks' in result['data']:
            for track in result['data']['tracks']:
                if track.get('type') == 'video':
                    return {
                        'width': track.get('width', 0),
                        'height': track.get('height', 0),
                        'codec': track.get('codec', 'unknown'),
                        'fps': track.get('fps', 25),
                        'bitrate': track.get('bitrate', 0),
                        'success': True
                    }

        # 如果没有找到视频轨道，返回默认值
        logger.warning(f"未找到视频轨道信息: app={app}, stream={stream_id}")
        return {
            'width': 0,
            'height': 0,
            'codec': 'unknown',
            'fps': 25,
            'bitrate': 0,
            'success': False
        }

    except Exception as e:
        logger.error(f"获取媒体信息失败: {str(e)}")
        return {
            'width': 0,
            'height': 0,
            'codec': 'unknown',
            'fps': 25,
            'bitrate': 0,
            'success': False
        }


def get_stream_play_urls(server_host: str, http_port: int, rtmp_port: int, rtsp_port: int, app: str, stream_id: str, secret: str = "") -> Dict[str, str]:
    """
    获取流的播放地址

    Args:
        server_host: ZLM服务器主机地址
        http_port: API端口（HTTP端口）
        rtmp_port: RTMP端口
        rtsp_port: RTSP端口
        app: 应用名（固定为"AI"）
        stream_id: 流ID（摄像机UUID）
        secret: ZLM API密钥（用于HTTP播放鉴权）

    Returns:
        各种协议的播放地址字典
    """
    # HTTP协议需要添加secret参数进行鉴权
    http_secret_param = f"?secret={secret}" if secret else ""
    ws_secret_param = f"?secret={secret}" if secret else ""
    # WebRTC URL已经有一个?了，所以secret需要用&连接
    rtc_secret_param = f"&secret={secret}" if secret else ""
    
    return {
        'rtmp': f"rtmp://{server_host}:{rtmp_port}/{app}/{stream_id}",
        'rtsp': f"rtsp://{server_host}:{rtsp_port}/{app}/{stream_id}",
        'http-flv': f"http://{server_host}:{http_port}/{app}/{stream_id}.live.flv{http_secret_param}",  # ZLM 直播流需要 .live.flv 格式 + secret鉴权
        'ws-flv': f"ws://{server_host}:80/{app}/{stream_id}.live.flv{ws_secret_param}",  # WebSocket 端口固定为 80 + secret鉴权
        'hls': f"http://{server_host}:{http_port}/{app}/{stream_id}/hls.m3u8{http_secret_param}",  # HLS也需要鉴权
        'rtc': f"http://{server_host}:{http_port}/index/api/webrtc?app={app}&stream={stream_id}&type=play{rtc_secret_param}",  # WebRTC播放地址，使用HTTP API端口
        'rtcs': f"https://{server_host}:{http_port}/index/api/webrtc?app={app}&stream={stream_id}&type=play{rtc_secret_param}",  # WebRTC HTTPS播放地址，使用HTTP API端口
    }


# ========================================
# 流管理服务类
# ========================================

class StreamService:
    """流管理服务"""
    
    # 固定的流APP名称
    STREAM_APP = "AI"
    
    def __init__(self, db_path: str):
        """
        初始化流管理服务
        
        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        logger.info(f"流管理服务初始化完成，数据库路径: {db_path}")
    
    def _get_db_connection(self):
        """获取数据库连接"""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_streaming_server(self, server_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        获取流媒体服务器配置
        
        Args:
            server_id: 服务器ID（可选，如果不指定则返回默认服务器）
        
        Returns:
            流媒体服务器配置字典或None
        """
        conn = self._get_db_connection()
        cursor = conn.cursor()
        
        try:
            if server_id:
                cursor.execute('SELECT * FROM streaming_servers WHERE id = ? AND enabled = 1', (server_id,))
            else:
                # 获取默认服务器
                cursor.execute('SELECT * FROM streaming_servers WHERE is_default = 1 AND enabled = 1 LIMIT 1')
            
            row = cursor.fetchone()
            
            if row:
                server = dict(row)
                # 解析config字段（JSON格式）
                if server.get('config'):
                    try:
                        server['config'] = json.loads(server['config'])
                    except Exception:
                        server['config'] = {}
                else:
                    server['config'] = {}
                
                return server
            
            return None
            
        finally:
            conn.close()
    
    def _get_camera(self, camera_id: str) -> Optional[Dict[str, Any]]:
        """
        获取摄像机信息
        
        Args:
            camera_id: 摄像机ID（UUID）
        
        Returns:
            摄像机信息字典或None
        """
        conn = self._get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT * FROM cameras WHERE id = ?', (camera_id,))
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            
            return None
            
        finally:
            conn.close()
    
    def _update_camera_stream_info(self, camera_id: str, stream_info: Dict[str, Any]):
        """
        更新摄像机的流信息
        
        Args:
            camera_id: 摄像机ID
            stream_info: 流信息字典，包含 rtmp, rtsp, http-flv 等地址
        """
        conn = self._get_db_connection()
        cursor = conn.cursor()
        
        try:
            now = datetime.now().isoformat()
            
            cursor.execute('''
                UPDATE cameras SET
                    rtmp_stream = ?,
                    rtsp_stream = ?,
                    http_stream = ?,
                    ws_stream = ?,
                    rtc_stream = ?,
                    rtcs_stream = ?,
                    status = ?,
                    updated_at = ?
                WHERE id = ?
            ''', (
                stream_info.get('rtmp', ''),
                stream_info.get('rtsp', ''),
                stream_info.get('http-flv', ''),
                stream_info.get('ws-flv', ''),
                stream_info.get('rtc', ''),
                stream_info.get('rtcs', ''),
                stream_info.get('status', 'online'),
                now,
                camera_id
            ))
            
            conn.commit()
            logger.info(f"更新摄像机流信息成功: camera_id={camera_id}")
            
        except Exception as e:
            conn.rollback()
            logger.error(f"更新摄像机流信息失败: {str(e)}")
            raise
            
        finally:
            conn.close()
    
    def start_stream(self, camera_id: str, server_id: Optional[int] = None) -> Dict[str, Any]:
        """
        启动摄像机的流
        
        Args:
            camera_id: 摄像机ID（UUID）
            server_id: 流媒体服务器ID（可选）
        
        Returns:
            启动结果字典
        
        Raises:
            ValueError: 参数错误或启动失败
        """
        try:
            # 获取摄像机信息
            camera = self._get_camera(camera_id)
            if not camera:
                raise ValueError(f"摄像机不存在: {camera_id}")
            
            # 检查是否是视频文件类型
            protocol = camera.get('protocol', 'rtsp')
            if protocol == 'file':
                # 视频文件：不走ZLM代理，返回AI流模式标记
                logger.info(f"视频文件类型，跳过ZLM代理: camera_id={camera_id}")
                
                # 解析视频文件路径
                source = camera.get('source', '')
                file_path = source.replace('file://', '') if source.startswith('file://') else source
                
                return {
                    'success': True,
                    'message': '视频文件，使用AI流模式',
                    'camera_id': camera_id,
                    'stream_type': 'ai_stream',  # 标记为AI流模式
                    'play_mode': 'ai_stream',    # 前端使用AI流播放
                    'file_path': file_path,
                    'camera': camera,
                }
            
            # RTSP流：走ZLM代理
            # 获取RTSP源地址
            rtsp_url = camera.get('source', '')
            if not rtsp_url:
                raise ValueError(f"摄像机没有取流地址: camera_id={camera_id}")
            
            # 获取流媒体服务器配置
            server = self._get_streaming_server(server_id)
            if not server:
                raise ValueError("没有可用的流媒体服务器")
            
            # 提取服务器配置
            server_host = server.get('host', 'localhost')
            http_port = server.get('http_port', 88)
            rtmp_port = server.get('rtmp_port', 1935)
            rtsp_port = server.get('rtsp_port', 554)
            secret = server.get('secret', 'FpvUDVyWlqohv98EEE5Of3UcFhc18Ipt')
            
            # 固定的流参数
            app = self.STREAM_APP
            stream_id = camera_id  # 流ID固定为摄像机ID
            
            logger.info(f"启动流: camera_id={camera_id}, app={app}, stream_id={stream_id}")
            
            # 先检查流是否已经存在
            stream_exists = check_stream_online(server_host, http_port, secret, app, stream_id)
            
            if stream_exists:
                # 流已经存在，直接返回播放地址
                logger.info(f"流已存在于ZLMediaKit中: app={app}, stream_id={stream_id}")
                
                # 生成播放地址
                play_urls = get_stream_play_urls(server_host, http_port, rtmp_port, rtsp_port, app, stream_id, secret)
                
                # 更新摄像机流信息
                self._update_camera_stream_info(camera_id, {
                    'rtmp': play_urls['rtmp'],
                    'rtsp': play_urls['rtsp'],
                    'http-flv': play_urls['http-flv'],
                    'ws-flv': play_urls['ws-flv'],
                    'rtc': play_urls['rtc'],
                    'rtcs': play_urls['rtcs'],
                    'status': 'online'
                })
                
                # 获取更新后的摄像机信息
                updated_camera = self._get_camera(camera_id)
                
                return {
                    'success': True,
                    'message': '流已存在，直接返回播放地址',
                    'camera_id': camera_id,
                    'stream_app': app,
                    'stream_id': stream_id,
                    'stream_type': 'zlm',        # 标记为ZLM流模式
                    'play_mode': 'zlm',          # 前端使用ZLM流播放
                    'server_id': server.get('id'),
                    'server_host': server_host,
                    'play_urls': play_urls,
                    'camera': updated_camera,
                }
            
            # 流不存在，需要启动流代理
            logger.info(f"流不存在，开始启动流代理: app={app}, stream_id={stream_id}")
            
            # 在ZLMediaKit上启动流代理
            try:
                result = start_stream_proxy(server_host, http_port, secret, rtsp_url, app, stream_id)
            except ValueError as e:
                # 如果流已存在，视为成功
                if 'already exists' in str(e).lower():
                    logger.info(f"流已存在于ZLMediaKit中: app={app}, stream_id={stream_id}")
                    result = {'code': 0, 'msg': 'success'}
                else:
                    raise
            
            # 生成播放地址
            play_urls = get_stream_play_urls(server_host, http_port, rtmp_port, rtsp_port, app, stream_id, secret)
            
            # 更新摄像机流信息
            self._update_camera_stream_info(camera_id, {
                'rtmp': play_urls['rtmp'],
                'rtsp': play_urls['rtsp'],
                'http-flv': play_urls['http-flv'],
                'ws-flv': play_urls['ws-flv'],
                'rtc': play_urls['rtc'],
                'rtcs': play_urls['rtcs'],
                'status': 'online'
            })
            
            # 获取更新后的摄像机信息
            updated_camera = self._get_camera(camera_id)
            
            logger.info(f"启动流成功: camera_id={camera_id}, app={app}, stream_id={stream_id}")
            
            return {
                'success': True,
                'message': '启动流成功',
                'camera_id': camera_id,
                'camera_name': camera.get('name', ''),
                'rtsp_url': rtsp_url,
                'stream_app': app,
                'stream_id': stream_id,
                'stream_type': 'zlm',        # 标记为ZLM流模式
                'play_mode': 'zlm',          # 前端使用ZLM流播放
                'server_id': server.get('id'),
                'server_host': server_host,
                'play_urls': play_urls,
                'zlm_response': result,
                'camera': updated_camera,
            }
            
        except Exception as e:
            logger.error(f"启动流失败: {str(e)}", exc_info=True)
            raise
    
    def stop_stream(self, camera_id: str, server_id: Optional[int] = None) -> Dict[str, Any]:
        """
        停止摄像机的流
        
        Args:
            camera_id: 摄像机ID（UUID）
            server_id: 流媒体服务器ID（可选）
        
        Returns:
            停止结果字典
        
        Raises:
            ValueError: 参数错误或停止失败
        """
        try:
            # 获取摄像机信息
            camera = self._get_camera(camera_id)
            if not camera:
                raise ValueError(f"摄像机不存在: {camera_id}")
            
            # 检查是否是视频文件类型
            protocol = camera.get('protocol', 'rtsp')
            if protocol == 'file':
                # 视频文件：不需要停止ZLM流
                logger.info(f"视频文件类型，跳过停止ZLM流: camera_id={camera_id}")
                return {
                    'success': True,
                    'message': '视频文件，无需停止ZLM流',
                    'camera_id': camera_id,
                    'stream_type': 'ai_stream',
                }
            
            # RTSP流：停止ZLM代理
            # 获取流媒体服务器配置
            server = self._get_streaming_server(server_id)
            if not server:
                raise ValueError("没有可用的流媒体服务器")
            
            # 提取服务器配置
            server_host = server.get('host', 'localhost')
            http_port = server.get('http_port', 88)
            rtmp_port = server.get('rtmp_port', 1935)
            rtsp_port = server.get('rtsp_port', 554)
            secret = server.get('secret', server.get('config', {}).get('secret', 'FpvUDVyWlqohv98EEE5Of3UcFhc18Ipt'))
            
            # 固定的流参数
            app = self.STREAM_APP
            stream_id = camera_id
            
            logger.info(f"停止流: camera_id={camera_id}, app={app}, stream_id={stream_id}")
            
            # 检查流是否存在
            stream_exists = check_stream_online(server_host, http_port, secret, app, stream_id)
            
            if not stream_exists:
                # 流不存在，直接返回
                logger.info(f"流不存在: app={app}, stream_id={stream_id}")
                
                # 更新摄像机流信息
                self._update_camera_stream_info(camera_id, {
                    'rtmp': '',
                    'rtsp': '',
                    'http-flv': '',
                    'ws-flv': '',
                    'status': 'offline'
                })
                
                return {
                    'success': True,
                    'message': '流不存在，无需停止',
                    'camera_id': camera_id,
                    'stream_app': app,
                    'stream_id': stream_id,
                }
            
            # 在ZLMediaKit上停止流
            result = stop_stream_proxy(server_host, http_port, secret, app, stream_id)
            
            # 更新摄像机流信息
            self._update_camera_stream_info(camera_id, {
                'rtmp': '',
                'rtsp': '',
                'http-flv': '',
                'ws-flv': '',
                'status': 'offline'
            })
            
            logger.info(f"停止流成功: camera_id={camera_id}, app={app}, stream_id={stream_id}")
            
            return {
                'success': True,
                'message': '停止流成功',
                'camera_id': camera_id,
                'stream_app': app,
                'stream_id': stream_id,
                'server_id': server.get('id'),
                'server_host': server_host,
                'zlm_response': result,
            }
            
        except Exception as e:
            logger.error(f"停止流失败: {str(e)}", exc_info=True)
            raise
    
    def check_stream_status(self, camera_id: str, server_id: Optional[int] = None) -> Dict[str, Any]:
        """
        检查摄像机的流状态
        
        Args:
            camera_id: 摄像机ID（UUID）
            server_id: 流媒体服务器ID（可选）
        
        Returns:
            流状态信息字典
        
        Raises:
            ValueError: 参数错误
        """
        try:
            # 获取摄像机信息
            camera = self._get_camera(camera_id)
            if not camera:
                raise ValueError(f"摄像机不存在: {camera_id}")
            
            # 获取流媒体服务器配置
            server = self._get_streaming_server(server_id)
            if not server:
                raise ValueError("没有可用的流媒体服务器")
            
            # 提取服务器配置
            server_host = server.get('host', 'localhost')
            http_port = server.get('http_port', 88)
            rtmp_port = server.get('rtmp_port', 1935)
            rtsp_port = server.get('rtsp_port', 554)
            secret = server.get('secret', 'FpvUDVyWlqohv98EEE5Of3UcFhc18Ipt')
            
            # 固定的流参数
            app = self.STREAM_APP
            stream_id = camera_id
            
            # 检查流是否存在
            stream_online = check_stream_online(server_host, http_port, secret, app, stream_id)
            
            # 生成播放地址
            play_urls = get_stream_play_urls(server_host, http_port, rtmp_port, rtsp_port, app, stream_id, secret)
            
            return {
                'success': True,
                'camera_id': camera_id,
                'camera_name': camera.get('name', ''),
                'stream_app': app,
                'stream_id': stream_id,
                'stream_online': stream_online,
                'server_id': server.get('id'),
                'server_host': server_host,
                'play_urls': play_urls,
                'camera_status': camera.get('status', 'unknown'),
                'camera': {
                    'id': camera.get('id', ''),
                    'name': camera.get('name', ''),
                    'ip': camera.get('ip', ''),
                    'port': camera.get('port', 80),
                    'status': camera.get('status', 'offline'),
                    'rtmp_stream': camera.get('rtmp_stream', ''),
                    'rtsp_stream': camera.get('rtsp_stream', ''),
                    'http_stream': camera.get('http_stream', ''),
                    'ws_stream': camera.get('ws_stream', ''),
                    'rtc_stream': camera.get('rtc_stream', ''),
                    'rtcs_stream': camera.get('rtcs_stream', ''),
                    'enable_forward': camera.get('enable_forward', False),
                }
            }
            
        except Exception as e:
            logger.error(f"检查流状态失败: {str(e)}", exc_info=True)
            raise
    
    def get_stream_urls(self, camera_id: str, server_id: Optional[int] = None) -> Dict[str, str]:
        """
        获取摄像机的流播放地址
        
        Args:
            camera_id: 摄像机ID（UUID）
            server_id: 流媒体服务器ID（可选）
        
        Returns:
            各种协议的播放地址字典
        
        Raises:
            ValueError: 参数错误
        """
        try:
            # 获取流媒体服务器配置
            server = self._get_streaming_server(server_id)
            if not server:
                raise ValueError("没有可用的流媒体服务器")
            
            # 提取服务器配置
            server_host = server.get('host', 'localhost')
            http_port = server.get('http_port', 88)
            rtmp_port = server.get('port', 1935)
            rtsp_port = server.get('config', {}).get('rtsp_port', 554)
            secret = server.get('secret', server.get('config', {}).get('secret', 'FpvUDVyWlqohv98EEE5Of3UcFhc18Ipt'))
            
            # 固定的流参数
            app = self.STREAM_APP
            stream_id = camera_id
            
            # 生成播放地址
            return get_stream_play_urls(server_host, http_port, rtmp_port, rtsp_port, app, stream_id, secret)
            
        except Exception as e:
            logger.error(f"获取流播放地址失败: {str(e)}", exc_info=True)
            raise


# ========================================
# 全局流管理服务实例
# ========================================

import json

_stream_service: Optional[StreamService] = None


def get_stream_service(db_path: str = 'data/tasks.db') -> StreamService:
    """
    获取全局流管理服务实例
    
    Args:
        db_path: 数据库路径
    
    Returns:
        流管理服务实例
    """
    global _stream_service
    if _stream_service is None:
        _stream_service = StreamService(db_path)
    return _stream_service