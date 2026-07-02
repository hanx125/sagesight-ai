"""
ONVIF PTZ服务

实现ONVIF PTZ控制功能，包括：
- PTZ能力检测
- PTZ移动控制（连续移动、相对移动）
- PTZ预置位管理
- PTZ状态查询

参考 VIDEO 项目 ptz_service.py 实现。
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Dict, Optional, Any, Iterable, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# 尝试导入ONVIF库
try:
    from onvif import ONVIFCamera
    ONVIF_AVAILABLE = True
except ImportError:
    ONVIF_AVAILABLE = False
    logger.warning("onvif库未安装，PTZ功能不可用。请安装: pip install onvif")


# ========================================
# PTZ数据类定义
# ========================================

@dataclass
class PTZPreset:
    """PTZ预置位数据类"""
    token: str  # 预置位令牌
    name: str  # 预置位名称
    position: Optional[Dict] = None  # 位置信息（Pan, Tilt, Zoom）
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'token': self.token,
            'name': self.name,
            'position': self.position,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


@dataclass
class PTZCapabilities:
    """PTZ能力数据类"""
    continuous_move: bool = False  # 是否支持连续移动
    relative_move: bool = False  # 是否支持相对移动
    absolute_move: bool = False  # 是否支持绝对移动
    zoom: bool = False  # 是否支持变焦
    preset_support: bool = False  # 是否支持预置位
    home_support: bool = False  # 是否支持回到原点
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'continuous_move': self.continuous_move,
            'relative_move': self.relative_move,
            'absolute_move': self.absolute_move,
            'zoom': self.zoom,
            'preset_support': self.preset_support,
            'home_support': self.home_support
        }


@dataclass
class PTZStatus:
    """PTZ状态数据类"""
    pan: float = 0.0  # 当前Pan位置
    tilt: float = 0.0  # 当前Tilt位置
    zoom: float = 0.0  # 当前Zoom位置
    is_moving: bool = False  # 是否正在移动
    move_status: str = "IDLE"  # 移动状态：IDLE, MOVING, STOPPED
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'pan': self.pan,
            'tilt': self.tilt,
            'zoom': self.zoom,
            'is_moving': self.is_moving,
            'move_status': self.move_status
        }


# ========================================
# PTZ控制器
# ========================================

class PTZController:
    """统一PTZ控制策略实现"""
    _executor = ThreadPoolExecutor(max_workers=4)

    def __init__(self, ptz_svc: Any, media_token: str, ptz_config_token: str):
        """
        初始化PTZ控制器
        
        Args:
            ptz_svc: PTZ服务对象
            media_token: Media Profile令牌
            ptz_config_token: PTZ配置令牌
        """
        self._ptz_svc = ptz_svc
        self._media_token = media_token
        self._ptz_config_token = ptz_config_token
        self._capabilities = self._detect_capabilities()
        logger.info(f"PTZ capabilities detected: {self._capabilities}")

    def _detect_capabilities(self) -> PTZCapabilities:
        """动态检测设备支持的PTZ功能"""
        capabilities = PTZCapabilities()
        
        try:
            # 获取PTZ配置选项
            conf = self._ptz_svc.GetConfigurationOptions({'ConfigurationToken': self._ptz_config_token})
            spaces = conf.Spaces
            
            # 检测移动能力
            capabilities.continuous_move = bool(spaces.ContinuousPanTiltVelocitySpace)
            capabilities.relative_move = bool(spaces.RelativePanTiltTranslationSpace)
            capabilities.absolute_move = bool(spaces.AbsolutePanTiltPositionSpace)
            
            # 检测变焦能力
            capabilities.zoom = bool(
                spaces.ContinuousZoomVelocitySpace or 
                spaces.RelativeZoomTranslationSpace or 
                spaces.AbsoluteZoomPositionSpace
            )
            
            # 检测预置位支持
            try:
                presets = self._ptz_svc.GetPresets({'ProfileToken': self._media_token})
                capabilities.preset_support = bool(presets)
            except Exception:
                capabilities.preset_support = False
            
            # 检测原点支持
            try:
                self._ptz_svc.GotoHome({'ProfileToken': self._media_token})
                capabilities.home_support = True
            except Exception:
                capabilities.home_support = False
                
        except Exception as e:
            logger.error(f"Capability detection failed: {e}")
            
        return capabilities

    def get_capabilities(self) -> PTZCapabilities:
        """获取PTZ能力"""
        return self._capabilities

    def get_status(self) -> PTZStatus:
        """获取当前PTZ状态"""
        status = PTZStatus()
        
        try:
            # 获取当前状态
            ptz_status = self._ptz_svc.GetStatus({'ProfileToken': self._media_token})
            
            if ptz_status.Position:
                if ptz_status.Position.PanTilt:
                    status.pan = float(ptz_status.Position.PanTilt.x)
                    status.tilt = float(ptz_status.Position.PanTilt.y)
                if ptz_status.Position.Zoom:
                    status.zoom = float(ptz_status.Position.Zoom.x)
            
            # 获取移动状态
            if ptz_status.MoveStatus:
                status.move_status = str(ptz_status.MoveStatus)
                status.is_moving = status.move_status == 'MOVING'
                
        except Exception as e:
            logger.error(f"Get PTZ status failed: {e}")
            
        return status

    def move(self, translation: Iterable[float], speed: Optional[Iterable[float]] = None):
        """
        执行PTZ移动操作
        
        Args:
            translation: 移动向量 [pan, tilt, zoom]
            speed: 移动速度 [pan_speed, tilt_speed, zoom_speed]（可选）
        """
        if not any(translation):
            return

        # 根据设备能力选择移动方式
        if self._capabilities.continuous_move:
            self._execute_threaded(self._continuous_move, translation, speed)
        elif self._capabilities.relative_move:
            self._relative_move(translation, speed)
        elif self._capabilities.absolute_move:
            self._absolute_move(translation, speed)
        else:
            logger.warning("No PTZ move method available")

    def _execute_threaded(self, func, *args):
        """使用线程池执行任务"""
        future = self._executor.submit(func, *args)
        try:
            future.result(timeout=10)
        except TimeoutError:
            logger.warning("PTZ operation timed out")

    def _generate_vector(self, values: Iterable[float]) -> Optional[Dict]:
        """生成PTZ控制向量"""
        vector = {}
        
        # Pan-Tilt向量
        if len(values) >= 2 and any(values[:2]):
            vector['PanTilt'] = {
                'x': float(values[0]),
                'y': float(values[1])
            }
        
        # Zoom向量
        if len(values) >= 3 and values[2]:
            vector['Zoom'] = {'x': float(values[2])}
            
        return vector or None

    def _continuous_move(self, translation: Iterable[float], speed: Optional[Iterable[float]] = None):
        """
        连续移动实现
        
        Args:
            translation: 移动向量
            speed: 移动速度
        """
        # 计算移动持续时间
        duration = max(map(abs, translation))
        if duration == 0:
            return
            
        # 缩放移动向量
        scaled = [x / duration for x in translation]
        
        # 生成速度向量
        velocity = self._generate_vector(scaled)
        if not velocity:
            return
        
        try:
            # 开始连续移动
            self._ptz_svc.ContinuousMove({
                'ProfileToken': self._media_token,
                'Velocity': velocity
            })
            
            # 等待移动完成
            time.sleep(duration)
            
            # 停止移动
            self._ptz_svc.Stop({
                'ProfileToken': self._media_token,
                'PanTilt': True,
                'Zoom': True
            })
            
            logger.info(f"Continuous move completed: {translation}")
            
        except Exception as e:
            logger.error(f"ContinuousMove failed: {e}")
            # 确保停止移动
            try:
                self._ptz_svc.Stop({
                    'ProfileToken': self._media_token,
                    'PanTilt': True,
                    'Zoom': True
                })
            except Exception:
                pass

    def _relative_move(self, translation: Iterable[float], speed: Optional[Iterable[float]] = None):
        """
        相对移动实现
        
        Args:
            translation: 移动向量
            speed: 移动速度
        """
        vector = self._generate_vector(translation)
        if not vector:
            return
        
        # 生成速度向量
        speed_vector = self._generate_vector(speed if speed else [1.0, 1.0, 1.0])
        
        try:
            self._ptz_svc.RelativeMove({
                'ProfileToken': self._media_token,
                'Translation': vector,
                'Speed': speed_vector
            })
            
            logger.info(f"Relative move completed: {translation}")
            
        except Exception as e:
            logger.error(f"RelativeMove failed: {e}")

    def _absolute_move(self, translation: Iterable[float], speed: Optional[Iterable[float]] = None):
        """
        绝对移动实现
        
        Args:
            translation: 目标位置向量
            speed: 移动速度
        """
        vector = self._generate_vector(translation)
        if not vector:
            return
        
        # 生成速度向量
        speed_vector = self._generate_vector(speed if speed else [1.0, 1.0, 1.0])
        
        try:
            self._ptz_svc.AbsoluteMove({
                'ProfileToken': self._media_token,
                'Position': vector,
                'Speed': speed_vector
            })
            
            logger.info(f"Absolute move completed: {translation}")
            
        except Exception as e:
            logger.error(f"AbsoluteMove failed: {e}")

    def stop(self):
        """停止所有PTZ移动"""
        try:
            self._ptz_svc.Stop({
                'ProfileToken': self._media_token,
                'PanTilt': True,
                'Zoom': True
            })
            
            logger.info("PTZ movement stopped")
            
        except Exception as e:
            logger.error(f"Stop PTZ failed: {e}")

    # ========================================
    # 预置位管理
    # ========================================

    def get_presets(self) -> List[PTZPreset]:
        """获取所有预置位"""
        presets = []
        
        try:
            response = self._ptz_svc.GetPresets({'ProfileToken': self._media_token})
            
            if response:
                for preset in response:
                    ptz_preset = PTZPreset(
                        token=preset.token,
                        name=preset.Name if hasattr(preset, 'Name') else f"Preset_{preset.token}",
                        position={
                            'pan': float(preset.PanTilt.x) if preset.PanTilt else 0.0,
                            'tilt': float(preset.PanTilt.y) if preset.PanTilt else 0.0,
                            'zoom': float(preset.Zoom.x) if preset.Zoom else 0.0
                        } if hasattr(preset, 'PanTilt') or hasattr(preset, 'Zoom') else None
                    )
                    presets.append(ptz_preset)
                    
        except Exception as e:
            logger.error(f"Get presets failed: {e}")
            
        return presets

    def save_preset(self, name: str) -> Optional[str]:
        """
        保存当前位置为预置位
        
        Args:
            name: 预置位名称
            
        Returns:
            预置位令牌
        """
        try:
            response = self._ptz_svc.SetPreset({
                'ProfileToken': self._media_token,
                'PresetName': name
            })
            
            # 处理不同的返回格式
            if hasattr(response, 'PresetToken'):
                preset_token = response.PresetToken
            elif isinstance(response, str):
                preset_token = response
            else:
                preset_token = str(response)
            
            logger.info(f"Preset saved: {name} -> {preset_token}")
            return preset_token
            
        except Exception as e:
            logger.error(f"Save preset failed: {e}")
            return None

    def goto_preset(self, token: str, speed: Optional[Iterable[float]] = None):
        """
        移动到指定预置位
        
        Args:
            token: 预置位令牌
            speed: 移动速度
        """
        try:
            speed_vector = self._generate_vector(speed if speed else [1.0, 1.0, 1.0])
            
            self._ptz_svc.GotoPreset({
                'ProfileToken': self._media_token,
                'PresetToken': token,
                'Speed': speed_vector
            })
            
            logger.info(f"Goto preset: {token}")
            
        except Exception as e:
            logger.error(f"Goto preset failed: {e}")

    def remove_preset(self, token: str):
        """
        删除预置位
        
        Args:
            token: 预置位令牌
        """
        try:
            self._ptz_svc.RemovePreset({
                'ProfileToken': self._media_token,
                'PresetToken': token
            })
            
            logger.info(f"Preset removed: {token}")
            
        except Exception as e:
            logger.error(f"Remove preset failed: {e}")

    # ========================================
    # 原点管理
    # ========================================

    def goto_home(self, speed: Optional[Iterable[float]] = None):
        """
        回到原点位置
        
        Args:
            speed: 移动速度
        """
        if not self._capabilities.home_support:
            logger.warning("Home position not supported")
            return
        
        try:
            speed_vector = self._generate_vector(speed if speed else [1.0, 1.0, 1.0])
            
            self._ptz_svc.GotoHome({
                'ProfileToken': self._media_token,
                'Speed': speed_vector
            })
            
            logger.info("Goto home position")
            
        except Exception as e:
            logger.error(f"Goto home failed: {e}")

    def set_home(self):
        """设置当前位置为原点"""
        try:
            self._ptz_svc.SetHomePosition({'ProfileToken': self._media_token})
            
            logger.info("Home position set")
            
        except Exception as e:
            logger.error(f"Set home position failed: {e}")


# ========================================
# PTZ服务管理器
# ========================================

class PTZServiceManager:
    """PTZ服务管理器"""
    
    def __init__(self):
        """初始化PTZ服务管理器"""
        self._ptz_controllers: Dict[str, PTZController] = {}
    
    def create_ptz_controller(self, camera: ONVIFCamera, media_token: str, ptz_config_token: str) -> Optional[PTZController]:
        """
        创建PTZ控制器
        
        Args:
            camera: ONVIF摄像机对象
            media_token: Media Profile令牌
            ptz_config_token: PTZ配置令牌
            
        Returns:
            PTZ控制器对象
        """
        if not ONVIF_AVAILABLE:
            logger.warning("ONVIF库未安装，无法创建PTZ控制器")
            return None
        
        try:
            # 创建PTZ服务
            ptz_svc = camera.create_ptz_service()
            
            # 创建PTZ控制器
            controller = PTZController(ptz_svc, media_token, ptz_config_token)
            
            # 缓存控制器 - 尝试获取hostname，如果失败则使用连接时的IP
            try:
                hostname_info = camera.devicemgmt.GetHostname()
                hostname = hostname_info.Name if hasattr(hostname_info, 'Name') else "unknown"
            except Exception:
                # 如果无法获取hostname，使用存储的IP地址
                hostname = getattr(camera, '_ip', 'unknown')
            
            key = f"{hostname}:{camera.port}"
            self._ptz_controllers[key] = controller
            
            return controller
            
        except Exception as e:
            logger.error(f"Create PTZ controller failed: {e}")
            return None
    
    def get_ptz_controller(self, camera_key: str) -> Optional[PTZController]:
        """
        获取PTZ控制器
        
        Args:
            camera_key: 摄像机标识（IP:Port）
            
        Returns:
            PTZ控制器对象
        """
        return self._ptz_controllers.get(camera_key)
    
    def remove_ptz_controller(self, camera_key: str):
        """
        移除PTZ控制器
        
        Args:
            camera_key: 摄像机标识
        """
        if camera_key in self._ptz_controllers:
            del self._ptz_controllers[camera_key]
            logger.info(f"PTZ controller removed: {camera_key}")


# ========================================
# 全局PTZ服务管理器实例
# ========================================

_ptz_service_manager: Optional[PTZServiceManager] = None


def get_ptz_service_manager() -> PTZServiceManager:
    """获取全局PTZ服务管理器实例"""
    global _ptz_service_manager
    if _ptz_service_manager is None:
        _ptz_service_manager = PTZServiceManager()
    return _ptz_service_manager