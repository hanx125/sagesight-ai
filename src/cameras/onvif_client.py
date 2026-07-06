"""
ONVIF客户端

实现ONVIF协议的摄像机发现、连接和信息获取。
参考 VIDEO 项目 onvif_service.py 实现。
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Any, Iterable

# 导入PTZ服务
from .ptz_service import PTZController, get_ptz_service_manager

logger = logging.getLogger(__name__)

# 尝试导入ONVIF库
try:
    from onvif import ONVIFCamera, ONVIFError
    ONVIF_AVAILABLE = True
except ImportError:
    ONVIF_AVAILABLE = False
    logger.warning("onvif库未安装，ONVIF功能不可用。请安装: pip install onvif")


class OnvifCameraClient:
    """ONVIF摄像机客户端"""
    _connection_pool = {}

    def __new__(cls, ip: str, port: int, username: str, password: str):
        if not ONVIF_AVAILABLE:
            raise RuntimeError("ONVIF库未安装，无法连接摄像机")
        
        key = f"{ip}:{port}:{username}"
        if key not in cls._connection_pool:
            instance = super().__new__(cls)
            instance._init_camera(ip, port, username, password)
            cls._connection_pool[key] = instance
        return cls._connection_pool[key]

    def _init_camera(self, ip: str, port: int, username: str, password: str):
        """初始化摄像机连接"""
        try:
            self._camera = ONVIFCamera(ip, port, username, password, adjust_time=True)
            self._ip = ip
            self._port = port
            self._username = username
            self._password = password

            # 获取设备基础信息
            self._media_svc = self._camera.create_media_service()
            self._media_profile = self._media_svc.GetProfiles()[0]
            self._media_token = self._media_profile.token

            # 初始化PTZ控制器
            try:
                ptz_manager = get_ptz_service_manager()
                ptz_config_token = self._media_profile.PTZConfiguration.token if hasattr(self._media_profile, 'PTZConfiguration') else None
                
                if ptz_config_token:
                    self._ptz_controller = ptz_manager.create_ptz_controller(
                        self._camera, 
                        self._media_token, 
                        ptz_config_token
                    )
                else:
                    self._ptz_controller = None
                    logger.warning("PTZ configuration not available")
                    
            except Exception as e:
                self._ptz_controller = None
                logger.warning(f"PTZ service not available: {e}")

            # 获取MAC地址
            try:
                self._mac = self._camera.devicemgmt.GetNetworkInterfaces()[0].Info.HwAddress
            except Exception:
                self._mac = ""
                logger.warning("Failed to get MAC address")

            logger.info(f"ONVIF摄像机连接成功: {ip}:{port}")
            
        except ONVIFError as e:
            error_msg = str(e).removeprefix('Unknown error: ')
            raise RuntimeError(f'ONVIF协议错误: {error_msg}')
        except Exception as e:
            raise RuntimeError(f'连接异常: {str(e)}')

    def get_info(self) -> Dict[str, Any]:
        """获取摄像机完整信息字典"""
        dev_info = self._camera.devicemgmt.GetDeviceInformation()

        # 获取视频流URL
        stream_uri = self._media_svc.GetStreamUri({
            'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}},
            'ProfileToken': self._media_token
        }).Uri

        # 处理认证信息嵌入
        if self._username:
            # 对密码中的特殊字符进行URL编码
            from urllib.parse import quote
            auth = f"{quote(self._username)}:{quote(self._password)}"
            rtsp_url = f"rtsp://{auth}@{stream_uri.split('//')[1]}"
        else:
            rtsp_url = stream_uri

        # 获取PTZ服务地址
        ptz_address = ""
        try:
            # 从设备服务中获取PTZ服务地址
            services = self._camera.devicemgmt.GetServices({'IncludeCapability': False})
            for service in services:
                if 'ptz' in service.Namespace.lower():
                    ptz_address = service.XAddr
                    break
        except Exception as e:
            logger.warning(f"获取PTZ服务地址失败: {e}")

        # 检测音频对讲能力
        support_audio_talk = self._check_audio_backchannel_support()

        return {
            'ip': self._ip,
            'port': self._port,
            'username': self._username,
            'password': self._password,
            'mac': self._mac,
            'manufacturer': dev_info.Manufacturer,
            'model': dev_info.Model,
            'firmware_version': dev_info.FirmwareVersion,
            'serial_number': dev_info.SerialNumber,
            'hardware_id': dev_info.HardwareId,
            'source': rtsp_url,
            'support_move': self._ptz_controller is not None,
            'support_zoom': self._ptz_controller is not None and self._ptz_controller.get_capabilities().zoom,
            'ptz_address': ptz_address,  # PTZ服务地址
            'support_audio_talk': support_audio_talk  # 音频对讲支持（自动检测）
        }

    def _check_audio_backchannel_support(self) -> bool:
        """检测摄像机是否支持音频对讲（Audio Back Channel）

        Returns:
            bool: 是否支持音频对讲
        """
        try:
            # 尝试获取音频编码器配置
            media_service = self._camera.create_media_service()
            profiles = media_service.GetProfiles()

            # 检查是否有音频编码器配置
            for profile in profiles:
                if hasattr(profile, 'AudioEncoderConfiguration'):
                    audio_config = profile.AudioEncoderConfiguration
                    if audio_config:
                        logger.info(f"[ONVIF] 检测到音频编码器配置，支持音频对讲")
                        return True

            # 尝试获取音频源配置
            try:
                audio_sources = media_service.GetAudioSources()
                if audio_sources:
                    logger.info(f"[ONVIF] 检测到音频源，支持音频对讲")
                    return True
            except Exception as audio_err:
                logger.warning(f"[ONVIF] GetAudioSources失败: {audio_err}")

            # 尝试检查Audio Back Channel配置选项
            try:
                # 获取音频编码器配置选项
                audio_encoder_options = media_service.GetAudioEncoderConfigurationOptions()
                if audio_encoder_options:
                    logger.info(f"[ONVIF] 检测到音频编码器选项，支持音频对讲")
                    return True
            except Exception as encoder_err:
                logger.warning(f"[ONVIF] GetAudioEncoderConfigurationOptions失败: {encoder_err}")

            logger.info(f"[ONVIF] 摄像机不支持音频对讲")
            return False

        except Exception as e:
            logger.warning(f"[ONVIF] 音频对讲检测失败: {e}")
            return False

    def move(self, translation: Iterable[float], speed: Optional[Iterable[float]] = None):
        """执行PTZ移动操作
        
        Args:
            translation: 移动向量 [pan, tilt, zoom]
            speed: 移动速度 [pan_speed, tilt_speed, zoom_speed]（可选）
        """
        if not self._ptz_controller:
            logger.warning("PTZ not supported")
            return

        try:
            self._ptz_controller.move(translation, speed)
        except Exception as e:
            logger.error(f"Move command failed: {e}")

    def stop_move(self):
        """停止PTZ移动"""
        if self._ptz_controller:
            self._ptz_controller.stop()

    def get_ptz_status(self):
        """获取PTZ状态"""
        if self._ptz_controller:
            return self._ptz_controller.get_status()
        return None

    def get_ptz_capabilities(self):
        """获取PTZ能力"""
        if self._ptz_controller:
            return self._ptz_controller.get_capabilities()
        return None

    def get_presets(self):
        """获取所有预置位"""
        if self._ptz_controller:
            return self._ptz_controller.get_presets()
        return []

    def save_position(self, name: str) -> Optional[str]:
        """保存当前位置为预置位"""
        if self._ptz_controller:
            return self._ptz_controller.save_preset(name)
        return None

    def goto_position(self, token: str, speed: Optional[Iterable[float]] = None):
        """移动到指定预置位
        
        Args:
            token: 预置位令牌
            speed: 移动速度（可选）
        """
        if self._ptz_controller:
            self._ptz_controller.goto_preset(token, speed)

    def remove_preset(self, token: str):
        """删除预置位"""
        if self._ptz_controller:
            self._ptz_controller.remove_preset(token)

    def goto_home(self, speed: Optional[Iterable[float]] = None):
        """回到原点位置"""
        if self._ptz_controller:
            self._ptz_controller.goto_home(speed)

    def set_home(self):
        """设置当前位置为原点"""
        if self._ptz_controller:
            self._ptz_controller.set_home()

    def get_stream_uri(self, protocol: str = 'RTSP') -> str:
        """获取视频流URL"""
        return self._media_svc.GetStreamUri({
            'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': protocol}},
            'ProfileToken': self._media_token
        }).Uri

    def get_snapshot_uri(self) -> str:
        """获取截图URL"""
        try:
            return self._media_svc.GetSnapshotUri({'ProfileToken': self._media_token}).Uri
        except Exception:
            logger.warning("Snapshot URI not available")
            return ""


def discover_onvif_cameras(timeout: int = 5) -> list:
    """发现网络中的ONVIF设备
    
    Args:
        timeout: 发现超时时间（秒）
    
    Returns:
        发现的设备列表
    """
    if not ONVIF_AVAILABLE:
        logger.warning("ONVIF库未安装，无法发现设备")
        return []
    
    try:
        from wsdiscovery import WSDiscovery, Scope
        import re
        
        wsd = WSDiscovery()
        wsd.start()
        
        onvif_cameras = []
        
        try:
            services = wsd.searchServices(
                scopes=[Scope("onvif://www.onvif.org/Profile")],
                timeout=timeout
            )
            
            for svc in services:
                try:
                    # 提取IP地址和端口
                    xaddrs_list = [str(addr) for addr in svc.getXAddrs()]
                    
                    # 从第一个xaddr中提取IP和端口
                    ip_match = None
                    port_match = None
                    
                    if xaddrs_list:
                        first_xaddr = xaddrs_list[0]
                        
                        # 提取IP地址（支持IPv4）
                        ip_search = re.search(r'(\d+\.\d+\.\d+\.\d+)', first_xaddr)
                        if ip_search:
                            ip_match = ip_search.group(1)
                        
                        # 提取端口（如果存在）
                        port_search = re.search(r':(\d+)', first_xaddr)
                        if port_search:
                            port_match = int(port_search.group(1))
                        else:
                            # 如果没有明确端口，根据协议推断
                            if first_xaddr.startswith('https://'):
                                port_match = 443
                            elif first_xaddr.startswith('http://'):
                                port_match = 80
                            else:
                                port_match = 80  # 默认ONVIF端口
                    
                    if ip_match:
                        onvif_cameras.append({
                            'ip': ip_match,
                            'port': port_match,  # 添加端口字段
                            'name': 'ONVIF Camera',
                            'xaddrs': xaddrs_list
                        })
                        
                except Exception as e:
                    logger.warning(f"解析设备信息失败: {e}")
                    continue
                    
        finally:
            wsd.stop()
            
        logger.info(f"发现 {len(onvif_cameras)} 个ONVIF设备")
        return onvif_cameras
        
    except ImportError:
        logger.warning("wsdiscovery库未安装，无法发现设备。请安装: pip install wsdiscovery")
        return []
    except Exception as e:
        logger.error(f"ONVIF设备发现失败: {e}")
        return []

def connect_onvif_camera(ip: str, port: int, username: str, password: str, timeout: int = 10) -> Dict:
    """连接ONVIF摄像机并获取信息
    
    Args:
        ip: IP地址
        port: ONVIF端口
        username: 用户名
        password: 密码
        timeout: 连接超时时间（秒）
    
    Returns:
        摄像机信息字典
    """
    if not ONVIF_AVAILABLE:
        raise RuntimeError("ONVIF库未安装")
    
    def connect():
        return OnvifCameraClient(ip, port, username, password)
    
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(connect)
        try:
            client = future.result(timeout=timeout)
            return client.get_info()
        except TimeoutError:
            raise RuntimeError('设备连接超时，请检查网络连接')
        except Exception as e:
            raise RuntimeError(f'连接失败: {str(e)}')