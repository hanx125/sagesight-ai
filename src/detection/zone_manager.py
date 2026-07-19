"""
Zone 区域管理模块

提供区域定义、检测和管理功能：
- Zone 类：定义多边形区域
- ZoneManager 类：管理多个区域
- 区域进入/离开检测
- 必需区域过滤
- 徘徊检测（借鉴 Frigate）

使用场景：
- 固定摄像头的区域监控
- 入侵检测（进入/离开区域）
- 事件过滤（必需区域）
- 徘徊检测（区域停留）

参考：
- Frigate object_processing.py
"""

import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Set
from enum import Enum


class ZoneEventType(Enum):
    """区域事件类型"""
    ENTER = "enter"  # 进入区域
    LEAVE = "leave"  # 离开区域
    STAY = "stay"  # 停留在区域
    LOITERING = "loitering"  # 徘徊（长时间停留）


@dataclass
class ZoneConfig:
    """区域配置"""
    name: str  # 区域名称
    polygon: List[Tuple[int, int]]  # 多边形顶点（顺时针或逆时针）
    required: bool = False  # 是否为必需区域（用于事件过滤）
    color: Tuple[int, int, int] = (255, 0, 0)  # 可视化颜色（BGR）
    min_area: int = 0  # 最小区域面积（过滤小区域）
    
    # ========================================
    # 新增：徘徊检测配置（借鉴 Frigate）
    # ========================================
    loitering_time: int = 30  # 徘徊判定时间（帧数，默认30帧≈1秒）
    enable_loitering_detection: bool = False  # 是否启用徘徊检测
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "name": self.name,
            "polygon": self.polygon,
            "required": self.required,
            "color": self.color,
            "min_area": self.min_area,
            "loitering_time": self.loitering_time,
            "enable_loitering_detection": self.enable_loitering_detection
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ZoneConfig':
        """从字典创建"""
        return cls(
            name=data["name"],
            polygon=[tuple(p) for p in data["polygon"]],
            required=data.get("required", False),
            color=tuple(data.get("color", (255, 0, 0))),
            min_area=data.get("min_area", 0),
            loitering_time=data.get("loitering_time", 30),
            enable_loitering_detection=data.get("enable_loitering_detection", False)
        )


class Zone:
    """
    区域类（多边形区域）
    
    功能：
    - 定义多边形区域
    - 检测点是否在区域内
    - 检测边界框是否在区域内
    - 可视化区域
    """
    
    def __init__(self, config: ZoneConfig):
        """
        初始化区域
        
        Args:
            config: 区域配置
        """
        self.config = config
        self.name = config.name
        self.polygon = np.array(config.polygon, dtype=np.int32)
        self.required = config.required
        self.color = config.color
        self.min_area = config.min_area
        
        # ========================================
        # 新增：徘徊检测配置（借鉴 Frigate）
        # ========================================
        self.loitering_time = config.loitering_time
        self.enable_loitering_detection = config.enable_loitering_detection
        
        # 计算区域面积
        self.area = cv2.contourArea(self.polygon)
        
        print(f"[Zone-{self.name}] 创建成功")
        print(f"[Zone-{self.name}]   顶点数: {len(self.polygon)}")
        print(f"[Zone-{self.name}]   面积: {self.area:.0f} 像素²")
        print(f"[Zone-{self.name}]   必需区域: {self.required}")
        print(f"[Zone-{self.name}]   徘徊检测: {self.enable_loitering_detection} (阈值: {self.loitering_time} 帧)")
    
    def contains_point(self, point: Tuple[float, float]) -> bool:
        """
        检测点是否在区域内
        
        Args:
            point: 点坐标 (x, y)
            
        Returns:
            bool: 点是否在区域内
        """
        # 使用 OpenCV 的 pointPolygonTest 函数
        # 返回值：正数（内部）、0（边界）、负数（外部）
        result = cv2.pointPolygonTest(self.polygon, point, False)
        return result >= 0
    
    def contains_bbox(self, bbox: Tuple[int, int, int, int]) -> bool:
        """
        检测边界框是否在区域内（使用中心点）
        
        Args:
            bbox: 边界框 (x1, y1, x2, y2)
            
        Returns:
            bool: 边界框是否在区域内
        """
        # 计算边界框中心点
        center_x = (bbox[0] + bbox[2]) / 2
        center_y = (bbox[1] + bbox[3]) / 2
        
        return self.contains_point((center_x, center_y))
    
    def contains_bbox_any_corner(self, bbox: Tuple[int, int, int, int]) -> bool:
        """
        检测边界框任意角点是否在区域内（更严格的检测）
        
        Args:
            bbox: 边界框 (x1, y1, x2, y2)
            
        Returns:
            bool: 任意角点是否在区域内
        """
        # 检测四个角点
        corners = [
            (bbox[0], bbox[1]),  # 左上
            (bbox[2], bbox[1]),  # 右上
            (bbox[2], bbox[3]),  # 右下
            (bbox[0], bbox[3])   # 左下
        ]
        
        for corner in corners:
            if self.contains_point(corner):
                return True
        
        return False
    
    def contains_bbox_all_corners(self, bbox: Tuple[int, int, int, int]) -> bool:
        """
        检测边界框所有角点是否在区域内（最严格的检测）
        
        Args:
            bbox: 边界框 (x1, y1, x2, y2)
            
        Returns:
            bool: 所有角点是否在区域内
        """
        # 检测四个角点
        corners = [
            (bbox[0], bbox[1]),  # 左上
            (bbox[2], bbox[1]),  # 右上
            (bbox[2], bbox[3]),  # 右下
            (bbox[0], bbox[3])   # 左下
        ]
        
        for corner in corners:
            if not self.contains_point(corner):
                return False
        
        return True
    
    def draw(self, frame: np.ndarray, alpha: float = 0.3) -> np.ndarray:
        """
        在帧上绘制区域
        
        Args:
            frame: 输入帧
            alpha: 透明度 (0-1)
            
        Returns:
            np.ndarray: 绘制后的帧
        """
        # 创建覆盖层
        overlay = frame.copy()
        
        # 绘制填充的多边形
        cv2.fillPoly(overlay, [self.polygon], self.color)
        
        # 绘制边框
        cv2.polylines(overlay, [self.polygon], True, self.color, 2)
        
        # 添加文本标签
        M = cv2.moments(self.polygon)
        if M["m00"] != 0:
            cX = int(M["m10"] / M["m00"])
            cY = int(M["m01"] / M["m00"])
            cv2.putText(overlay, self.name, (cX - 20, cY), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        # 混合覆盖层和原始帧
        result = cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)
        
        return result


class ZoneManager:
    """
    区域管理器
    
    功能：
    - 管理多个区域
    - 检测对象进入/离开区域
    - 生成区域事件
    - 必需区域过滤
    """
    
    def __init__(self):
        """初始化区域管理器"""
        self.zones: Dict[str, Zone] = {}  # zone_name -> Zone
        self.required_zones: Set[str] = set()  # 必需区域集合
        
        # 跟踪对象所在的区域
        # track_id -> set(zone_names)
        self.object_zones: Dict[int, Set[str]] = {}
        
        # ========================================
        # 新增：徘徊检测（借鉴 Frigate）
        # ========================================
        # 对象在区域内的停留帧数
        # track_id -> zone_name -> frame_count
        self.object_zone_frames: Dict[int, Dict[str, int]] = {}
        
        # 已发送徘徊警报的对象
        # track_id -> set(zone_names)
        self.loitering_objects: Dict[int, Set[str]] = {}
        
        print("[ZoneManager] 初始化完成")
    
    def add_zone(self, config: ZoneConfig):
        """
        添加区域
        
        Args:
            config: 区域配置
        """
        zone = Zone(config)
        self.zones[config.name] = zone
        
        if config.required:
            self.required_zones.add(config.name)
        
        print(f"[ZoneManager] 添加区域: {config.name} (必需: {config.required})")
    
    def add_zones_from_config(self, zones_config: List[dict]):
        """
        从配置批量添加区域
        
        Args:
            zones_config: 区域配置列表
        """
        for zone_data in zones_config:
            config = ZoneConfig.from_dict(zone_data)
            self.add_zone(config)
        
        print(f"[ZoneManager] 共添加 {len(self.zones)} 个区域")
        print(f"[ZoneManager] 必需区域: {self.required_zones}")
    
    def check_object_zones(self, track_id: int, bbox: Tuple[int, int, int, int]) -> Dict[str, ZoneEventType]:
        """
        检查对象所在的区域（包含徘徊检测）
        
        参考 Frigate 的 zone_loitering 逻辑：
        - 对象在区域内停留超过阈值帧数，触发徘徊事件
        
        Args:
            track_id: 跟踪 ID
            bbox: 边界框 (x1, y1, x2, y2)
            
        Returns:
            Dict[str, ZoneEventType]: 区域事件 {zone_name: event_type}
        """
        events = {}
        
        # 获取对象当前所在的区域
        current_zones = set()
        for zone_name, zone in self.zones.items():
            if zone.contains_bbox(bbox):
                current_zones.add(zone_name)
        
        # 获取对象之前所在的区域
        previous_zones = self.object_zones.get(track_id, set())
        
        # 检测进入区域
        entered_zones = current_zones - previous_zones
        for zone_name in entered_zones:
            events[zone_name] = ZoneEventType.ENTER
        
        # 检测离开区域
        left_zones = previous_zones - current_zones
        for zone_name in left_zones:
            events[zone_name] = ZoneEventType.LEAVE
            
            # 清除徘徊计数（离开区域）
            if track_id in self.object_zone_frames:
                if zone_name in self.object_zone_frames[track_id]:
                    del self.object_zone_frames[track_id][zone_name]
            
            # 清除徘徊标记
            if track_id in self.loitering_objects:
                self.loitering_objects[track_id].discard(zone_name)
        
        # 检测停留在区域
        stayed_zones = current_zones & previous_zones
        for zone_name in stayed_zones:
            events[zone_name] = ZoneEventType.STAY
            
            # ========================================
            # 新增：徘徊检测（借鉴 Frigate）
            # ========================================
            zone = self.zones[zone_name]
            
            # 检查是否启用徘徊检测
            if zone.enable_loitering_detection:
                # 更新停留帧数
                if track_id not in self.object_zone_frames:
                    self.object_zone_frames[track_id] = {}
                
                if zone_name not in self.object_zone_frames[track_id]:
                    self.object_zone_frames[track_id][zone_name] = 0
                
                self.object_zone_frames[track_id][zone_name] += 1
                
                # 检查是否达到徘徊阈值
                frame_count = self.object_zone_frames[track_id][zone_name]
                
                if frame_count >= zone.loitering_time:
                    # 初始化徘徊对象集合
                    if track_id not in self.loitering_objects:
                        self.loitering_objects[track_id] = set()
                    
                    # 如果还没有发送过徘徊警报
                    if zone_name not in self.loitering_objects[track_id]:
                        events[zone_name] = ZoneEventType.LOITERING
                        self.loitering_objects[track_id].add(zone_name)
                        
                        print(f"[ZoneManager] 对象 {track_id} 在区域 {zone_name} 徘徊 (帧数: {frame_count})")
        
        # 更新对象所在的区域
        self.object_zones[track_id] = current_zones
        
        return events
    
    def get_object_zones(self, track_id: int) -> Set[str]:
        """
        获取对象当前所在的区域
        
        Args:
            track_id: 跟踪 ID
            
        Returns:
            Set[str]: 区域名称集合
        """
        return self.object_zones.get(track_id, set())
    
    def is_in_required_zones(self, track_id: int) -> bool:
        """
        检测对象是否在必需区域内（用于事件过滤）
        
        Args:
            track_id: 跟踪 ID
            
        Returns:
            bool: 是否在必需区域内
        """
        if not self.required_zones:
            # 没有定义必需区域，总是返回 True
            return True
        
        object_zones = self.get_object_zones(track_id)
        
        # 对象必须至少在一个必需区域内
        return len(object_zones & self.required_zones) > 0
    
    def should_emit_event(self, track_id: int, bbox: Tuple[int, int, int, int]) -> bool:
        """
        判断是否应该发送事件（基于必需区域）
        
        参考 Frigate 的 should_save_snapshot 逻辑：
        - 如果定义了必需区域，对象必须至少进入一个必需区域
        - 否则，总是发送事件
        
        Args:
            track_id: 跟踪 ID
            bbox: 边界框 (x1, y1, x2, y2)
            
        Returns:
            bool: 是否应该发送事件
        """
        if not self.required_zones:
            # 没有定义必需区域，总是发送事件
            return True
        
        # 检查对象是否在必需区域内
        return self.is_in_required_zones(track_id)
    
    def remove_object(self, track_id: int):
        """
        移除对象（跟踪结束时调用）
        
        Args:
            track_id: 跟踪 ID
        """
        if track_id in self.object_zones:
            del self.object_zones[track_id]
    
    def clear_all_objects(self):
        """清除所有对象"""
        self.object_zones.clear()
    
    def draw_zones(self, frame: np.ndarray, alpha: float = 0.3) -> np.ndarray:
        """
        在帧上绘制所有区域
        
        Args:
            frame: 输入帧
            alpha: 透明度 (0-1)
            
        Returns:
            np.ndarray: 绘制后的帧
        """
        result = frame.copy()
        
        for zone in self.zones.values():
            result = zone.draw(result, alpha)
        
        return result
    
    def get_stats(self) -> dict:
        """
        获取统计信息
        
        Returns:
            dict: 统计信息
        """
        return {
            "total_zones": len(self.zones),
            "required_zones": len(self.required_zones),
            "tracked_objects": len(self.object_zones),
            "zones": list(self.zones.keys()),
            "required_zones_list": list(self.required_zones)
        }


def create_default_zones() -> ZoneManager:
    """
    创建默认区域管理器（示例）
    
    Returns:
        ZoneManager: 区域管理器实例
    """
    manager = ZoneManager()
    
    # 示例：创建入口区域
    entrance_config = ZoneConfig(
        name="entrance",
        polygon=[(100, 100), (200, 100), (200, 200), (100, 200)],
        required=True
    )
    manager.add_zone(entrance_config)
    
    # 示例：创建停车区域
    parking_config = ZoneConfig(
        name="parking",
        polygon=[(300, 100), (400, 100), (400, 200), (300, 200)],
        required=False
    )
    manager.add_zone(parking_config)
    
    return manager