"""
跨相机跟踪

把同一目标在不同相机上的轨迹片段（事件）串成一条身份轨迹。

模块划分：
- config    配置读写（config/cross_camera.yaml）
- features  外观特征提取与相似度（事件快照裁剪 + HSV 直方图）
- matcher   候选打分与关联决策（时空门控 + 外观 + 唯一性判断）
- store     身份/关联表读写
- fusion_service  后台融合线程（轮询新事件 → 关联 → 落库）
- api       HTTP 接口（轨迹列表 / 详情 / 配置）
"""

from src.tracking.cross_camera.config import (
    get_cross_camera_config,
    save_cross_camera_config,
    reload_cross_camera_config,
    is_cross_camera_enabled,
)

__all__ = [
    'get_cross_camera_config',
    'save_cross_camera_config',
    'reload_cross_camera_config',
    'is_cross_camera_enabled',
]
