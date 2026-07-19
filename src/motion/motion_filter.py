"""运动区域过滤工具

用于合并、过滤、扩展运动区域，优化检测性能。
"""

import numpy as np
from typing import List, Tuple
from .motion_detector import MotionRegion


class MotionFilter:
    """运动区域过滤器
    
    功能：
        - 合并重叠的运动区域
        - 扩展运动区域（覆盖更多潜在目标）
        - 过滤小区域（减少噪声）
        - 限制区域数量（防止过多检测）
    """
    
    @staticmethod
    def merge_overlapping_regions(
        regions: List[MotionRegion],
        overlap_threshold: float = 0.3
    ) -> List[MotionRegion]:
        """合并重叠的运动区域
        
        Args:
            regions: 运动区域列表
            overlap_threshold: 重叠阈值（0-1）
        
        Returns:
            合并后的运动区域列表
        """
        if len(regions) <= 1:
            return regions
        
        # 按面积排序（大区域优先）
        regions = sorted(regions, key=lambda r: r.area, reverse=True)
        
        merged = []
        while regions:
            # 取出第一个区域
            current = regions.pop(0)
            current_bbox = current.bbox
            
            # 检查是否与其他区域重叠
            i = 0
            while i < len(regions):
                other_bbox = regions[i].bbox
                
                # 计算重叠面积
                x1 = max(current_bbox[0], other_bbox[0])
                y1 = max(current_bbox[1], other_bbox[1])
                x2 = min(current_bbox[2], other_bbox[2])
                y2 = min(current_bbox[3], other_bbox[3])
                
                if x1 < x2 and y1 < y2:
                    overlap_area = (x2 - x1) * (y2 - y1)
                    current_area = (current_bbox[2] - current_bbox[0]) * (current_bbox[3] - current_bbox[1])
                    other_area = (other_bbox[2] - other_bbox[0]) * (other_bbox[3] - other_bbox[1])
                    
                    # 如果重叠比例超过阈值，合并区域
                    if overlap_area / min(current_area, other_area) > overlap_threshold:
                        # 合并边界框
                        merged_bbox = (
                            min(current_bbox[0], other_bbox[0]),
                            min(current_bbox[1], other_bbox[1]),
                            max(current_bbox[2], other_bbox[2]),
                            max(current_bbox[3], other_bbox[3])
                        )
                        current_bbox = merged_bbox
                        
                        # 移除已合并的区域
                        regions.pop(i)
                        continue
                
                i += 1
            
            # 更新当前区域的边界框
            merged_region = MotionRegion(
                bbox=current_bbox,
                area=(current_bbox[2] - current_bbox[0]) * (current_bbox[3] - current_bbox[1]),
                center=(
                    (current_bbox[0] + current_bbox[2]) / 2,
                    (current_bbox[1] + current_bbox[3]) / 2
                ),
                intensity=current.intensity
            )
            merged.append(merged_region)
        
        return merged
    
    @staticmethod
    def expand_regions(
        regions: List[MotionRegion],
        frame_shape: Tuple[int, int],
        expand_ratio: float = 0.1
    ) -> List[MotionRegion]:
        """扩展运动区域（覆盖更多潜在目标）
        
        Args:
            regions: 运动区域列表
            frame_shape: 帧形状 (height, width)
            expand_ratio: 扩展比例（0-1）
        
        Returns:
            扩展后的运动区域列表
        """
        expanded = []
        frame_height, frame_width = frame_shape[:2]
        
        for region in regions:
            x1, y1, x2, y2 = region.bbox
            w = x2 - x1
            h = y2 - y1
            
            # 计算扩展量
            expand_w = int(w * expand_ratio)
            expand_h = int(h * expand_ratio)
            
            # 扩展边界框（限制在帧范围内）
            new_x1 = max(0, x1 - expand_w)
            new_y1 = max(0, y1 - expand_h)
            new_x2 = min(frame_width, x2 + expand_w)
            new_y2 = min(frame_height, y2 + expand_h)
            
            # 创建扩展后的区域
            expanded_region = MotionRegion(
                bbox=(new_x1, new_y1, new_x2, new_y2),
                area=(new_x2 - new_x1) * (new_y2 - new_y1),
                center=(
                    (new_x1 + new_x2) / 2,
                    (new_y1 + new_y2) / 2
                ),
                intensity=region.intensity
            )
            expanded.append(expanded_region)
        
        return expanded
    
    @staticmethod
    def filter_small_regions(
        regions: List[MotionRegion],
        min_area: int = 500
    ) -> List[MotionRegion]:
        """过滤小区域（减少噪声）
        
        Args:
            regions: 运动区域列表
            min_area: 最小面积（像素）
        
        Returns:
            过滤后的运动区域列表
        """
        return [r for r in regions if r.area >= min_area]
    
    @staticmethod
    def limit_region_count(
        regions: List[MotionRegion],
        max_count: int = 10
    ) -> List[MotionRegion]:
        """限制运动区域数量（防止过多检测）
        
        Args:
            regions: 运动区域列表
            max_count: 最大数量
        
        Returns:
            限制后的运动区域列表
        """
        if len(regions) <= max_count:
            return regions
        
        # 按面积排序，只保留最大的区域
        return sorted(regions, key=lambda r: r.area, reverse=True)[:max_count]
    
    @staticmethod
    def calculate_union_bbox(
        regions: List[MotionRegion]
    ) -> Tuple[int, int, int, int]:
        """计算所有运动区域的联合边界框
        
        Args:
            regions: 运动区域列表
        
        Returns:
            联合边界框 (x1, y1, x2, y2)
        """
        if not regions:
            return (0, 0, 0, 0)
        
        x1_values = [r.bbox[0] for r in regions]
        y1_values = [r.bbox[1] for r in regions]
        x2_values = [r.bbox[2] for r in regions]
        y2_values = [r.bbox[3] for r in regions]
        
        return (
            min(x1_values),
            min(y1_values),
            max(x2_values),
            max(y2_values)
        )