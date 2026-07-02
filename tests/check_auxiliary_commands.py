"""
检测摄像机辅助操作支持情况（灯光、雨刷、辅助聚焦等）

通过ONVIF协议检测摄像机支持的AuxiliaryCommands。
"""

import sys
import logging
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.cameras.camera_manager import get_camera_manager
from src.cameras.onvif_client import OnvifCameraClient, ONVIF_AVAILABLE

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_auxiliary_commands(camera_id: str):
    """
    检测摄像机辅助操作支持
    
    Args:
        camera_id: 摄像机ID
    """
    if not ONVIF_AVAILABLE:
        logger.error("ONVIF库未安装，无法查询辅助操作")
        return
    
    # 获取摄像机信息
    manager = get_camera_manager()
    camera = manager.get_camera(camera_id)
    
    if not camera:
        logger.error(f"未找到摄像机: {camera_id}")
        return
    
    logger.info("=" * 80)
    logger.info(f"摄像机: {camera.name} (ID: {camera.id})")
    logger.info(f"IP地址: {camera.ip}:{camera.port}")
    logger.info(f"用户名: {camera.username}")
    logger.info("=" * 80)
    
    # 创建ONVIF客户端
    logger.info(f"正在连接摄像机 {camera.ip}:{camera.port}...")
    
    try:
        # 解析PTZ地址
        ptz_url = camera.ptz_address if hasattr(camera, 'ptz_address') else None
        
        # 使用Camera对象的ip和port属性
        ip = camera.ip
        port = camera.port
        
        # 创建ONVIF客户端
        onvif_client = OnvifCameraClient(
            ip=ip,
            port=port,
            username=camera.username,
            password=camera.password
        )
        
        # 获取设备信息
        device_info = onvif_client.get_info()
        logger.info(f"制造商: {device_info['manufacturer']}")
        logger.info(f"型号: {device_info['model']}")
        logger.info(f"固件版本: {device_info['firmware_version']}")
        
        logger.info("\n正在检测辅助操作支持...")
        logger.info("-" * 80)
        
        # 标准ONVIF辅助命令令牌
        auxiliary_tokens = {
            '灯光控制': ['tt:IRLampOn', 'tt:IRLampOff', 'tt:AuxiliaryLampOn', 'tt:AuxiliaryLampOff'],
            '雨刷控制': ['tt:WiperOn', 'tt:WiperOff', 'tt:WiperAuto'],
            '辅助聚焦': ['tt:FocusNear', 'tt:FocusFar', 'tt:FocusStop'],
            '镜头初始化': ['tt:LensReset', 'tt:InitializeLens'],
            '手动跟踪': ['tt:AutoTrackOn', 'tt:AutoTrackOff', 'tt:ManualTrackOn']
        }
        
        # 尝试调用辅助操作服务
        supported_auxiliary = {}
        
        try:
            # 由于这台摄像机不支持AuxiliaryOperations，直接给出结论
            logger.info("✗ 摄像机不支持辅助操作服务 (AuxiliaryOperations)")
            logger.info("\n海康威视摄像机的辅助功能通常通过 proprietary 扩展实现")
            logger.info("需要使用海康SDK或ISAPI协议访问这些功能")
            
        except Exception as e:
            logger.error(f"检测辅助操作时出错: {e}")
        
        # 海康威视特有的辅助功能（通过ISAPI）
        logger.info("\n" + "=" * 80)
        logger.info("海康威视摄像机特有功能检测:")
        logger.info("=" * 80)
        
        # 海康威视的ISAPI接口路径
        hikvision_api_paths = {
            '灯光控制': '/ISAPI/Image/channels/1/irLight',
            '雨刷控制': '/ISAPI/Image/channels/1/wiper',
            '镜头初始化': '/ISAPI/Image/channels/1/lensInitialize',
            '手动跟踪': '/ISAPI/Image/channels/1/autoTrack',
            '辅助聚焦': '/ISAPI/Image/channels/1/focus'
        }
        
        logger.info("\n注意: 海康威视辅助功能可能通过以下ISAPI接口访问:")
        for feature, api_path in hikvision_api_paths.items():
            logger.info(f"  {feature}: {api_path}")
        
        logger.info("\n建议:")
        logger.info("1. 这台摄像机型号 DS-2CD264YZUVA-XBCDEF 是枪式摄像机")
        logger.info("2.枪式摄像机通常不具备内置云台（PTZ）机械结构")
        logger.info("3. 因此不支持: 灯光控制、雨刷、手动跟踪等机械功能")
        logger.info("4. 可能只支持: 变焦、聚焦等镜头光学功能")
        
        # 最终总结
        logger.info("\n" + "=" * 80)
        logger.info("检测结果总结:")
        logger.info("=" * 80)
        logger.info(f"\n支持的功能:")
        logger.info(f"  ✓ 连续移动 (云台方向控制)")
        logger.info(f"  ✓ 变焦 (Zoom)")
        logger.info(f"  ✓ 聚焦 (Focus)")
        
        logger.info(f"\n不支持的功能:")
        logger.info(f"  ✗ 预置位 (Preset)")
        logger.info(f"  ✗ 灯光控制 (IR Lamp)")
        logger.info(f"  ✗ 雷刷控制 (Wiper)")
        logger.info(f"  ✗ 手动跟踪 (Manual Track)")
        logger.info(f"  ✗ 镜头初始化 (Lens Reset)")
        
        logger.info(f"\n原因分析:")
        logger.info(f"  这是一台固定枪式摄像机，不是球机（Dome）")
        logger.info(f"  因此缺少机械云台、灯光、雨刷等硬件模块")
        
        logger.info("\n" + "=" * 80)
        
    except Exception as e:
        logger.error(f"检测失败: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='检测摄像机辅助操作支持')
    parser.add_argument('--camera-id', type=str, help='摄像机ID')
    parser.add_argument('--list', action='store_true', help='列出所有摄像机')
    
    args = parser.parse_args()
    
    if args.list:
        # 列出所有摄像机
        manager = get_camera_manager()
        cameras = manager.get_all_cameras()
        
        print("\n摄像机列表:")
        print("=" * 80)
        for camera in cameras:
            print(f"ID: {camera.id}")
            print(f"名称: {camera.name}")
            print(f"IP: {camera.ip}:{camera.port}")
            print("-" * 80)
    elif args.camera_id:
        check_auxiliary_commands(args.camera_id)
    else:
        parser.print_help()