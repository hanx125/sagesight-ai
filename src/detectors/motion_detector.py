"""
运动检测模块

使用背景建模（MOG2）检测运动区域，辅助目标检测和跟踪

特性：
- MOG2 背景建模（自适应阈值）
- 运动区域提取（前景掩码）
- 与目标检测融合（置信度调整）
- 运动历史记录（辅助重识别）
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class MotionRegion:
    """运动区域"""
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    area: int                         # 区域面积（像素）
    center: Tuple[float, float]       # 中心点 (cx, cy)
    intensity: float                  # 运动强度（0-1）


class MotionDetector:
    """
    运动检测器（基于 MOG2 背景建模）
    
    使用场景：
    - 固定摄像头（背景相对稳定）
    - 室外场景（光照变化）
    - 室内场景（人员流动）
    
    不适用场景：
    - 移动摄像头（背景不断变化）
    - 快速光照变化（如闪光灯）
    """
    
    def __init__(
        self,
        history: int = 500,           # 背景建模历史帧数
        var_threshold: float = 16.0,  # 方差阈值（越小越敏感）
        detect_shadows: bool = False, # 是否检测阴影（建议关闭，提高速度）
        min_area: int = 500,          # 最小运动区域面积（像素）
        blur_size: int = 5,           # 模糊核大小（降噪）
        morph_size: int = 5,          # 形态学核大小（填充空洞）
        learning_rate: float = -1,    # 学习率（-1 表示自动）
        motion_history_length: int = 10,  # 运动历史保留帧数
        init_threshold: int = 30,         # 初始化所需帧数
        foreground_threshold: float = 0.3, # 前景占比判定阈值
        max_motion_regions: int = 20,     # 最大运动区域数量
        confidence_boost: float = 0.15,   # 置信度增益
        confidence_penalty: float = 0.05  # 置信度惩罚
    ):
        """
        初始化运动检测器
        
        Args:
            history: 背景建模历史帧数（越大越稳定，但启动慢）
            var_threshold: 方差阈值（越小越敏感，但噪声多）
            detect_shadows: 是否检测阴影（建议关闭）
            min_area: 最小运动区域面积（过滤噪声）
            blur_size: 模糊核大小（降噪）
            morph_size: 形态学核大小（填充空洞）
            learning_rate: 学习率（-1 表示自动，0 表示不更新）
            motion_history_length: 运动历史保留帧数（用于辅助重识别）
            init_threshold: 初始化所需帧数（背景建模预热）
            foreground_threshold: 前景占比判定阈值（判定是否在运动区域内）
            max_motion_regions: 最大运动区域数量（限制过多区域）
            confidence_boost: 置信度增益（检测框在运动区域内时增加）
            confidence_penalty: 置信度惩罚（检测框不在运动区域内时降低）
        """
        # 创建 MOG2 背景建模器
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=history,
            varThreshold=var_threshold,
            detectShadows=detect_shadows
        )
        
        # 保存所有参数（用于 reset 方法）
        self.history = history
        self.var_threshold = var_threshold
        self.detect_shadows = detect_shadows
        self.min_area = min_area
        self.blur_size = blur_size
        self.morph_size = morph_size
        self.learning_rate = learning_rate
        self.motion_history_length = motion_history_length
        self.init_threshold = init_threshold
        self.foreground_threshold = foreground_threshold
        self.max_motion_regions = max_motion_regions
        self.confidence_boost = confidence_boost
        self.confidence_penalty = confidence_penalty
        
        # 运动历史（用于辅助重识别）
        self.motion_history: List[MotionRegion] = []
        
        # 前景掩码（用于可视化）
        self.foreground_mask: Optional[np.ndarray] = None
        
        # 初始化标志（需要预热）
        self.is_initialized = False
        self.init_frame_count = 0
    
    def detect(self, frame: np.ndarray) -> List[MotionRegion]:
        """
        检测运动区域
        
        Args:
            frame: 输入帧（BGR 格式）
        
        Returns:
            运动区域列表
        """
        # 应用背景建模
        self.foreground_mask = self.bg_subtractor.apply(
            frame, 
            learningRate=self.learning_rate
        )
        
        # 初始化计数
        self.init_frame_count += 1
        if self.init_frame_count >= self.init_threshold:
            self.is_initialized = True
        
        # 如果未初始化，返回空列表
        if not self.is_initialized:
            return []
        
        # 后处理：降噪 + 填充空洞
        # 1. 模糊（降噪）
        if self.blur_size > 0:
            self.foreground_mask = cv2.GaussianBlur(
                self.foreground_mask, 
                (self.blur_size, self.blur_size), 
                0
            )
        
        # 2. 二值化（阈值化）
        _, self.foreground_mask = cv2.threshold(
            self.foreground_mask, 
            127, 255, 
            cv2.THRESH_BINARY
        )
        
        # 3. 形态学操作（填充空洞）
        if self.morph_size > 0:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, 
                (self.morph_size, self.morph_size)
            )
            self.foreground_mask = cv2.morphologyEx(
                self.foreground_mask, 
                cv2.MORPH_CLOSE, 
                kernel
            )
            self.foreground_mask = cv2.morphologyEx(
                self.foreground_mask, 
                cv2.MORPH_OPEN, 
                kernel
            )
        
        # 快速预检：统计前景像素数量，如果极少则直接返回空（跳过昂贵的轮廓查找）
        foreground_pixel_count = cv2.countNonZero(self.foreground_mask)
        if foreground_pixel_count < self.min_area:
            # 前景像素太少，不可能有有效运动区域
            self.motion_history.extend([])
            return []
        
        # 查找轮廓
        contours, _ = cv2.findContours(
            self.foreground_mask, 
            cv2.RETR_EXTERNAL, 
            cv2.CHAIN_APPROX_SIMPLE
        )
        
        # 提取运动区域
        motion_regions = []
        for contour in contours:
            # 计算面积
            area = cv2.contourArea(contour)
            
            # 过滤小区域（噪声）
            if area < self.min_area:
                continue
            
            # 计算边界框
            x, y, w, h = cv2.boundingRect(contour)
            x1, y1, x2, y2 = x, y, x + w, y + h
            
            # 计算中心点
            cx = x + w / 2
            cy = y + h / 2
            
            # 计算运动强度（面积占比）
            frame_area = frame.shape[0] * frame.shape[1]
            intensity = area / frame_area
            
            # 创建运动区域
            motion_regions.append(MotionRegion(
                bbox=(x1, y1, x2, y2),
                area=area,
                center=(cx, cy),
                intensity=intensity
            ))
        
        # 限制运动区域数量（避免过多区域影响性能）
        if len(motion_regions) > self.max_motion_regions:
            # 按面积排序，保留最大的区域
            motion_regions.sort(key=lambda r: r.area, reverse=True)
            motion_regions = motion_regions[:self.max_motion_regions]
        
        # 更新运动历史
        self.motion_history.extend(motion_regions)
        if len(self.motion_history) > self.motion_history_length:
            self.motion_history = self.motion_history[-self.motion_history_length:]
        
        return motion_regions
    
    def is_in_motion_region(self, bbox: Tuple[float, float, float, float]) -> bool:
        """
        检查检测框是否在运动区域内
        
        Args:
            bbox: 检测框 (x1, y1, x2, y2)
        
        Returns:
            是否在运动区域内
        """
        if self.foreground_mask is None:
            return False
        
        x1, y1, x2, y2 = bbox
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        
        # 边界检查
        h, w = self.foreground_mask.shape
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(0, min(x2, w - 1))
        y2 = max(0, min(y2, h - 1))
        
        # 提取检测框区域
        roi = self.foreground_mask[y1:y2, x1:x2]
        
        # 计算前景像素占比
        if roi.size == 0:
            return False
        
        foreground_ratio = np.sum(roi > 0) / roi.size
        
        # 阈值：如果超过配置的前景占比阈值，则认为在运动区域内
        return foreground_ratio > self.foreground_threshold
    
    def get_motion_confidence_boost(self, bbox: Tuple[float, float, float, float]) -> float:
        """
        获取运动置信度增益
        
        Args:
            bbox: 棰测框 (x1, y1, x2, y2)
        
        Returns:
            置信度增益（0.0 - 0.2）
        """
        if not self.is_initialized or self.foreground_mask is None:
            return 0.0
        
        x1, y1, x2, y2 = bbox
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        
        # 边界检查
        h, w = self.foreground_mask.shape
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(0, min(x2, w - 1))
        y2 = max(0, min(y2, h - 1))
        
        # 提取检测框区域
        roi = self.foreground_mask[y1:y2, x1:x2]
        
        if roi.size == 0:
            return 0.0
        
        # 计算前景像素占比
        foreground_ratio = np.sum(roi > 0) / roi.size
        
        # 置信度增益：前景占比越高，增益越大
        # 使用配置的 confidence_boost 作为最大增益（避免过度影响）
        boost = foreground_ratio * self.confidence_boost
        
        return boost
    
    def get_nearby_motion_regions(
        self, 
        center: Tuple[float, float], 
        radius: float
    ) -> List[MotionRegion]:
        """
        获取附近的运动区域（辅助重识别）
        
        Args:
            center: 中心点 (cx, cy)
            radius: 搜索半径（像素）
        
        Returns:
            附近的运动区域列表
        """
        cx, cy = center
        
        nearby_regions = []
        for region in self.motion_history:
            # 计算距离
            dist = np.sqrt((region.center[0] - cx)**2 + (region.center[1] - cy)**2)
            
            # 如果在搜索半径内，添加到列表
            if dist <= radius:
                nearby_regions.append(region)
        
        return nearby_regions
    
    def visualize(self, frame: np.ndarray) -> np.ndarray:
        """
        可视化运动区域
        
        Args:
            frame: 输入帧（BGR 格式）
        
        Returns:
            可视化帧（叠加运动区域）
        """
        if self.foreground_mask is None:
            return frame
        
        # 创建叠加图像
        overlay = frame.copy()
        
        # 将前景掩码叠加到图像上（红色）
        overlay[self.foreground_mask > 0] = [0, 0, 255]
        
        # 混合原图和叠加图像
        result = cv2.addWeighted(frame, 0.7, overlay, 0.3, 0)
        
        return result
    
    def reset(self):
        """重置背景建模器"""
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=self.history,
            varThreshold=self.var_threshold,
            detectShadows=self.detect_shadows
        )
        self.is_initialized = False
        self.init_frame_count = 0
        self.motion_history = []
        self.foreground_mask = None


def benchmark():
    """性能测试"""
    import time
    
    print("\n" + "="*70)
    print("运动检测器性能测试")
    print("="*70)
    
    # 创建测试帧
    frame = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
    
    detector = MotionDetector()
    
    # 预热
    print("\n预热（30 帧）...")
    for i in range(30):
        detector.detect(frame)
    
    # 性能测试
    print("\n性能测试（100 帧）...")
    start_time = time.time()
    for i in range(100):
        motion_regions = detector.detect(frame)
    
    elapsed = time.time() - start_time
    fps = 100 / elapsed
    avg_time = elapsed / 100 * 1000
    
    print(f"  FPS: {fps:.1f}")
    print(f"  平均延迟：{avg_time:.2f}ms")
    print("="*70)


if __name__ == "__main__":
    benchmark()