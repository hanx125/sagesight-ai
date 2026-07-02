"""
查询摄像机的PTZ能力

连接数据库中的摄像机，查询其PTZ功能。
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


def check_ptz_capabilities(camera_id: str = None):
    """
    查询摄像机的PTZ能力
    
    Args:
        camera_id: 摄像机ID，如果不指定则查询所有摄像机
    """
    if not ONVIF_AVAILABLE:
        logger.error("ONVIF库未安装，无法查询PTZ能力")
        logger.info("请安装: pip install onvif")
        return
    
    # 获取摄像机管理器
    manager = get_camera_manager()
    
    # 获取摄像机列表
    if camera_id:
        cameras = [manager.get_camera(camera_id)]
    else:
        cameras = manager.get_all_cameras()
    
    if not cameras:
        logger.warning("数据库中没有找到摄像机")
        return
    
    logger.info(f"找到 {len(cameras)} 个摄像机")
    
    # 查询每个摄像机的PTZ能力
    for camera in cameras:
        logger.info("=" * 80)
        logger.info(f"摄像机: {camera.name} (ID: {camera.id})")
        logger.info(f"IP地址: {camera.ip}:{camera.port}")
        logger.info(f"用户名: {camera.username}")
        logger.info(f"源地址: {camera.source}")
        logger.info(f"PTZ地址: {camera.ptz_address}")
        logger.info("=" * 80)
        
        # 检查是否有ONVIF连接信息
        if not camera.ip or not camera.port:
            logger.warning(f"摄像机 {camera.name} 缺少ONVIF连接信息（IP/端口）")
            continue
        
        if not camera.username:
            logger.warning(f"摄像机 {camera.name} 缺少用户名")
            continue
        
        # 连接摄像机
        try:
            logger.info(f"正在连接摄像机 {camera.ip}:{camera.port}...")
            client = OnvifCameraClient(
                ip=camera.ip,
                port=camera.port,
                username=camera.username,
                password=camera.password
            )
            
            # 获取设备信息
            device_info = client.get_info()
            logger.info(f"制造商: {device_info.get('manufacturer', 'N/A')}")
            logger.info(f"型号: {device_info.get('model', 'N/A')}")
            logger.info(f"固件版本: {device_info.get('firmware_version', 'N/A')}")
            
            # 检查PTZ控制器是否存在
            if hasattr(client, '_ptz_controller') and client._ptz_controller:
                logger.info("\n✓ PTZ控制器已初始化")
                
                # 获取PTZ能力
                capabilities = client._ptz_controller.get_capabilities()
                logger.info("\nPTZ能力检测结果:")
                logger.info(f"  连续移动 (Continuous Move): {'✓ 支持' if capabilities.continuous_move else '✗ 不支持'}")
                logger.info(f"  相对移动 (Relative Move): {'✓ 支持' if capabilities.relative_move else '✗ 不支持'}")
                logger.info(f"  绝对移动 (Absolute Move): {'✓ 支持' if capabilities.absolute_move else '✗ 不支持'}")
                logger.info(f"  变焦 (Zoom): {'✓ 支持' if capabilities.zoom else '✗ 不支持'}")
                logger.info(f"  预置位 (Presets): {'✓ 支持' if capabilities.preset_support else '✗ 不支持'}")
                logger.info(f"  回原点 (Home): {'✓ 支持' if capabilities.home_support else '✗ 不支持'}")
                
                # 获取当前PTZ状态
                try:
                    status = client._ptz_controller.get_status()
                    logger.info("\n当前PTZ状态:")
                    logger.info(f"  Pan位置: {status.pan}")
                    logger.info(f"  Tilt位置: {status.tilt}")
                    logger.info(f"  Zoom位置: {status.zoom}")
                    logger.info(f"  移动状态: {status.move_status}")
                    logger.info(f"  是否正在移动: {status.is_moving}")
                except Exception as e:
                    logger.warning(f"无法获取PTZ状态: {e}")
                
                # 尝试获取预置位列表
                if capabilities.preset_support:
                    try:
                        presets = client._ptz_controller.get_presets()
                        logger.info("\n预置位列表:")
                        if presets:
                            for preset in presets:
                                logger.info(f"  - {preset.name} (Token: {preset.token})")
                        else:
                            logger.info("  暂无预置位")
                    except Exception as e:
                        logger.warning(f"无法获取预置位列表: {e}")
                
            else:
                logger.warning("\n✗ PTZ控制器未初始化，该摄像机可能不支持PTZ功能")
                
        except Exception as e:
            logger.error(f"连接摄像机 {camera.name} 失败: {e}")
            logger.error(f"错误详情: {type(e).__name__}")
        
        logger.info("\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="查询摄像机的PTZ能力")
    parser.add_argument('--camera-id', type=str, help='指定摄像机ID')
    parser.add_argument('--list', action='store_true', help='列出所有摄像机')
    
    args = parser.parse_args()
    
    if args.list:
        # 只列出摄像机信息
        manager = get_camera_manager()
        cameras = manager.get_all_cameras()
        
        print("\n摄像机列表:")
        print("=" * 80)
        for camera in cameras:
            print(f"ID: {camera.id}")
            print(f"名称: {camera.name}")
            print(f"IP: {camera.ip}:{camera.port}")
            print(f"用户名: {camera.username}")
            print(f"PTZ地址: {camera.ptz_address}")
            print(f"支持移动: {camera.support_move}")
            print(f"支持变焦: {camera.support_zoom}")
            print("-" * 80)
    else:
        # 查询PTZ能力
        check_ptz_capabilities(args.camera_id)