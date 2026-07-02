"""
更新摄像机的PTZ能力标志

根据实际检测结果，更新数据库中的support_move和support_zoom字段。
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


def update_camera_ptz_capabilities():
    """更新摄像机的PTZ能力标志"""
    if not ONVIF_AVAILABLE:
        logger.error("ONVIF库未安装，无法检测PTZ能力")
        return
    
    # 获取摄像机管理器
    manager = get_camera_manager()
    
    # 获取所有摄像机
    cameras = manager.get_all_cameras()
    
    if not cameras:
        logger.warning("数据库中没有找到摄像机")
        return
    
    logger.info(f"找到 {len(cameras)} 个摄像机")
    
    # 检查每个摄像机的PTZ能力
    for camera in cameras:
        logger.info("=" * 80)
        logger.info(f"检查摄像机: {camera.name}")
        logger.info(f"IP地址: {camera.ip}:{camera.port}")
        
        # 检查是否有ONVIF连接信息
        if not camera.ip or not camera.port:
            logger.warning(f"摄像机 {camera.name} 缺少ONVIF连接信息")
            continue
        
        if not camera.username:
            logger.warning(f"摄像机 {camera.name} 缺少用户名")
            continue
        
        # 连接摄像机并检测PTZ能力
        try:
            logger.info(f"连接摄像机...")
            client = OnvifCameraClient(
                ip=camera.ip,
                port=camera.port,
                username=camera.username,
                password=camera.password
            )
            
            # 检查PTZ控制器是否存在
            if hasattr(client, '_ptz_controller') and client._ptz_controller:
                ptz_controller = client._ptz_controller
                
                # 获取PTZ能力
                capabilities = ptz_controller.get_capabilities()
                
                # 更新数据库
                support_move = capabilities.continuous_move or capabilities.relative_move or capabilities.absolute_move
                support_zoom = capabilities.zoom
                
                logger.info(f"检测结果:")
                logger.info(f"  - 支持移动: {support_move}")
                logger.info(f"  - 支持变焦: {support_zoom}")
                
                # 更新摄像机信息
                if support_move != camera.support_move or support_zoom != camera.support_zoom:
                    # 使用update_camera方法，传入字典参数
                    updates = {
                        'support_move': support_move,
                        'support_zoom': support_zoom
                    }
                    manager.update_camera(camera.id, updates)
                    
                    logger.info(f"✓ 已更新摄像机 {camera.name} 的PTZ能力标志")
                else:
                    logger.info(f"  PTZ能力标志无需更新")
                
            else:
                logger.warning(f"摄像机 {camera.name} 不支持PTZ功能")
                
                # 更新数据库（如果之前标记为支持）
                if camera.support_move or camera.support_zoom:
                    updates = {
                        'support_move': False,
                        'support_zoom': False
                    }
                    manager.update_camera(camera.id, updates)
                    logger.info(f"✓ 已更新摄像机 {camera.name} 的PTZ能力标志为不支持")
                
        except Exception as e:
            logger.error(f"检测摄像机 {camera.name} PTZ能力失败: {e}")
    
    logger.info("\n所有摄像机PTZ能力检测完成")


if __name__ == "__main__":
    update_camera_ptz_capabilities()