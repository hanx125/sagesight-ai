"""
测试PTZ控制API功能

测试云台控制、预置点管理、巡航路径管理等API接口。
"""

import sys
import logging
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_ptz_api():
    """测试PTZ API功能"""
    from src.cameras.camera_manager import get_camera_manager
    
    # 获取摄像机管理器
    manager = get_camera_manager()
    
    # 获取第一个摄像机
    cameras = manager.get_all_cameras()
    
    if not cameras:
        logger.warning("数据库中没有找到摄像机")
        return
    
    camera = cameras[0]
    camera_id = camera.id
    
    logger.info(f"测试摄像机: {camera.name} (ID: {camera_id})")
    logger.info(f"IP地址: {camera.ip}:{camera.port}")
    
    # 测试PTZ控制器获取
    from src.cameras.ptz_api import get_ptz_controller_for_camera
    
    ptz_controller = get_ptz_controller_for_camera(camera_id)
    
    if ptz_controller:
        logger.info("✓ PTZ控制器获取成功")
        
        # 测试获取PTZ能力
        capabilities = ptz_controller.get_capabilities()
        logger.info("✓ PTZ能力获取成功:")
        logger.info(f"  - 连续移动: {capabilities.continuous_move}")
        logger.info(f"  - 相对移动: {capabilities.relative_move}")
        logger.info(f"  - 绝对移动: {capabilities.absolute_move}")
        logger.info(f"  - 变焦: {capabilities.zoom}")
        logger.info(f"  - 预置位: {capabilities.preset_support}")
        logger.info(f"  - 回原点: {capabilities.home_support}")
        
        # 测试获取PTZ状态
        status = ptz_controller.get_status()
        logger.info("✓ PTZ状态获取成功:")
        logger.info(f"  - Pan: {status.pan}")
        logger.info(f"  - Tilt: {status.tilt}")
        logger.info(f"  - Zoom: {status.zoom}")
        logger.info(f"  - 移动状态: {status.move_status}")
        
        # 测试预置位功能（如果支持）
        if capabilities.preset_support:
            logger.info("\n测试预置位功能:")
            
            # 获取预置位列表
            presets = ptz_controller.get_presets()
            logger.info(f"✓ 获取预置位列表成功: {len(presets)} 个预置位")
            
            for preset in presets:
                logger.info(f"  - {preset.name} (Token: {preset.token})")
            
            # 测试创建新预置位
            logger.info("\n测试创建新预置位...")
            new_preset_token = ptz_controller.save_preset("测试预置点")
            logger.info(f"✓ 创建预置位成功: Token={new_preset_token}")
            
            # 测试调用预置位
            if new_preset_token:
                logger.info(f"\n测试调用预置位 {new_preset_token}...")
                ptz_controller.goto_preset(new_preset_token)
                logger.info("✓ 调用预置位成功")
            
            # 测试删除预置位
            if new_preset_token:
                logger.info(f"\n测试删除预置位 {new_preset_token}...")
                ptz_controller.remove_preset(new_preset_token)
                logger.info("✓ 删除预置位成功")
        
        # 测试变焦功能（如果支持）
        if capabilities.zoom:
            logger.info("\n测试变焦功能:")
            logger.info("  执行变焦放大操作...")
            ptz_controller.move([0, 0, 0.3])  # 变焦放大
            logger.info("✓ 变焦操作成功")
            
            # 停止移动
            ptz_controller.stop()
            logger.info("✓ 停止移动成功")
        
        logger.info("\n✓ 所有测试通过")
        
    else:
        logger.warning("✗ PTZ控制器获取失败，该摄像机可能不支持PTZ功能")


if __name__ == "__main__":
    test_ptz_api()