"""
Zone 区域检测管理器

核心职责:
- 管理多个 ZoneConfig 区域配置
- 判断目标（边界框中心点或底边中心点）是否在某个区域内
- 提供区域绘制方法（供 AI 视频流使用）

坐标体系:
- 配置存储使用归一化坐标 [0-1]，polygon_type="normalized"
- 运行时按帧尺寸转换为像素坐标进行计算
"""

import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ZoneConfig:
    """单个 Zone 区域配置"""

    name: str
    polygon: List[List[float]]  # 归一化坐标 [[x1,y1], [x2,y2], ...]，范围 [0-1]
    color: List[int] = field(default_factory=lambda: [0, 255, 0])  # RGB 颜色
    required: bool = False  # 是否为必需区域
    enable_loitering_detection: bool = False  # 是否启用徘徊检测
    loitering_time: int = 30  # 徘徊阈值（秒）
    polygon_type: str = "normalized"

    @classmethod
    def from_dict(cls, data: dict) -> "ZoneConfig":
        """从字典创建 ZoneConfig"""
        return cls(
            name=data.get("name", "未命名区域"),
            polygon=data.get("polygon", []),
            color=data.get("color", [0, 255, 0]),
            required=data.get("required", False),
            enable_loitering_detection=data.get("enable_loitering_detection", False),
            loitering_time=data.get("loitering_time", 30),
            polygon_type=data.get("polygon_type", "normalized"),
        )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "name": self.name,
            "polygon": self.polygon,
            "color": self.color,
            "required": self.required,
            "enable_loitering_detection": self.enable_loitering_detection,
            "loitering_time": self.loitering_time,
            "polygon_type": self.polygon_type,
        }

    def get_pixel_polygon(self, frame_width: int, frame_height: int) -> np.ndarray:
        """将归一化坐标转换为像素坐标

        Args:
            frame_width: 帧宽度（像素）
            frame_height: 帧高度（像素）

        Returns:
            像素坐标数组，shape=(N, 2)，dtype=int32
        """
        pts = []
        for p in self.polygon:
            if self.polygon_type == "normalized":
                px = int(p[0] * frame_width)
                py = int(p[1] * frame_height)
            else:
                px = int(p[0])
                py = int(p[1])
            pts.append([px, py])
        return np.array(pts, dtype=np.int32)


class ZoneManager:
    """Zone 区域检测管理器

    管理多个 ZoneConfig，提供目标与区域的关系判断。
    在 CameraState 中使用，每帧检查跟踪目标与区域的关系。
    """

    def __init__(self, zones_config: list, frame_shape: tuple = (640, 640)):
        """
        Args:
            zones_config: Zone 配置列表（字典格式）
            frame_shape: 帧形状 (height, width)，用于坐标转换
        """
        self.frame_shape = frame_shape
        self.frame_height, self.frame_width = frame_shape[:2]

        self.zones: List[ZoneConfig] = []
        for zc in zones_config or []:
            try:
                self.zones.append(ZoneConfig.from_dict(zc))
            except Exception as e:
                logger.warning(f"解析 ZoneConfig 失败: {e}, data={zc}")

        logger.debug(f"[ZoneManager] 初始化: {len(self.zones)} 个区域")

    def _point_in_polygon(self, point: Tuple[float, float], polygon: np.ndarray) -> bool:
        """射线法判断点是否在多边形内部

        Args:
            point: (x, y) 像素坐标
            polygon: 像素坐标数组 shape=(N, 2)

        Returns:
            是否在多边形内部
        """
        x, y = point
        n = len(polygon)
        inside = False

        j = n - 1
        for i in range(n):
            xi, yi = polygon[i][0], polygon[i][1]
            xj, yj = polygon[j][0], polygon[j][1]

            if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            ):
                inside = not inside
            j = i

        return inside

    def get_object_zones(self, bbox: list) -> List[str]:
        """判断目标所在的区域

        使用边界框底边中心点（脚步位置）作为判断依据，
        因为目标的脚通常接触地面，更能反映其真实位置。

        Args:
            bbox: 边界框 [x1, y1, x2, y2]（像素坐标）

        Returns:
            区域名称列表（可能为空）
        """
        if not self.zones or len(bbox) < 4:
            return []

        # 使用底边中心点作为目标位置
        foot_x = (bbox[0] + bbox[2]) / 2.0
        foot_y = bbox[3]  # 底边 y 坐标
        point = (foot_x, foot_y)

        zones_in = []
        for zone in self.zones:
            pixel_polygon = zone.get_pixel_polygon(self.frame_width, self.frame_height)
            if len(pixel_polygon) < 3:
                continue
            if self._point_in_polygon(point, pixel_polygon):
                zones_in.append(zone.name)

        return zones_in

    def draw_zones(self, frame: np.ndarray, active_zones: set = None) -> np.ndarray:
        """在帧上绘制区域框（供 AI 视频流使用）

        Args:
            frame: RGB 帧
            active_zones: 当前有目标的区域名称集合（用于高亮）

        Returns:
            绘制后的帧
        """
        import cv2

        active_zones = active_zones or set()
        frame_h, frame_w = frame.shape[:2]

        for zone in self.zones:
            pixel_polygon = zone.get_pixel_polygon(frame_w, frame_h)
            if len(pixel_polygon) < 3:
                continue

            # 颜色（配置中为 RGB，OpenCV 使用 BGR）
            color_rgb = zone.color if zone.color else [0, 255, 0]
            color_bgr = tuple(reversed(color_rgb))

            # 活跃区域高亮（提高填充透明度）
            is_active = zone.name in active_zones
            fill_alpha = 0.4 if is_active else 0.2
            border_thickness = 3 if is_active else 2

            # 半透明填充
            overlay = frame.copy()
            cv2.fillPoly(overlay, [pixel_polygon], color_bgr)
            frame = cv2.addWeighted(overlay, fill_alpha, frame, 1 - fill_alpha, 0)

            # 边框
            cv2.polylines(frame, [pixel_polygon], True, color_bgr, border_thickness)

            # 区域名称（绘制在重心位置）
            moments = cv2.moments(pixel_polygon)
            if moments["m00"] != 0:
                cx = int(moments["m10"] / moments["m00"])
                cy = int(moments["m01"] / moments["m00"])
                # 中文文字支持
                try:
                    from src.utils.font_utils import draw_utf8_label_on_rgb

                    label = zone.name
                    if is_active:
                        label = f"[活跃] {zone.name}"
                    draw_utf8_label_on_rgb(
                        frame, label, (cx - 30, cy - 10),
                        font_size=14, text_color_rgb=(255, 255, 255)
                    )
                except ImportError:
                    cv2.putText(
                        frame, zone.name, (cx - 30, cy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1
                    )

        return frame

    def get_zone(self, name: str) -> Optional[ZoneConfig]:
        """按名称获取 ZoneConfig"""
        for zone in self.zones:
            if zone.name == name:
                return zone
        return None

    def get_loitering_zones(self) -> List[ZoneConfig]:
        """获取所有启用了徘徊检测的区域"""
        return [z for z in self.zones if z.enable_loitering_detection]
