"""
差分分析

以基线 Y 平面为参照，对当前帧指定多边形区域做差分，输出差异面积占比与
差异主连通域外接框。纯函数无状态，可独立单测。

计算流程:
1. ROI 裁剪:   多边形外接矩形（含 4px 边距）
2. 亮度归一化: 吸收白平衡漂移/色彩范围差等全局亮度偏移
3. 绝对差分 + 二值化
4. 多边形掩膜: 只统计区域内的像素
5. 形态学:     开运算去孤立噪点 → 闭运算连片
6. 面积占比:   差异像素数 / 多边形像素数
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

# 像素差分二值化阈值（灰度 0-255）
DIFF_THRESHOLD = 25

# ROI 裁剪时多边形外接矩形的外扩边距（像素）
ROI_PADDING = 4

# 亮度归一化缩放的最低标准差：基线/当前帧纹理过弱（低于该值）时跳过对比度
# 缩放仅做均值平移，避免纯色场景下真实物体差异被缩放抹除
MIN_NORMALIZE_STD = 4.0

# 亮度偏移校正的最大幅度（灰度级）：小漂移在此范围内被吸收；
# 超过该幅度的全局偏移（昼夜切换/开灯）保留差异，供上层判定为全局突变
MAX_MEAN_SHIFT = 15.0


@dataclass
class DiffResult:
    """单区域差分结果"""
    area_ratio: float                # 差异像素占多边形像素的比例（0~1+）
    bbox: Optional[List[int]]        # 差异主连通域外接框（全帧坐标 [x1,y1,x2,y2]），无差异为 None


class DiffAnalyzer:
    """差分分析器（无状态，逐区域调用 analyze）"""

    def __init__(self, diff_threshold: int = DIFF_THRESHOLD):
        self.diff_threshold = diff_threshold

    def analyze(
        self,
        current_y: np.ndarray,
        baseline_y: np.ndarray,
        polygon_px: List[Tuple[int, int]],
    ) -> DiffResult:
        """计算当前帧相对基线在指定多边形内的差异

        Args:
            current_y:  当前帧 Y 平面 (H, W) uint8
            baseline_y: 基线 Y 平面 (H, W) uint8，形状与当前帧一致
            polygon_px: 多边形顶点（全帧像素坐标）

        Returns:
            DiffResult(area_ratio, bbox)
        """
        frame_h, frame_w = current_y.shape[:2]

        # 1. 多边形外接矩形 + 边距，裁剪 ROI
        xs = [int(p[0]) for p in polygon_px]
        ys = [int(p[1]) for p in polygon_px]
        x1 = max(0, min(xs) - ROI_PADDING)
        y1 = max(0, min(ys) - ROI_PADDING)
        x2 = min(frame_w, max(xs) + ROI_PADDING)
        y2 = min(frame_h, max(ys) + ROI_PADDING)
        if x2 <= x1 or y2 <= y1:
            return DiffResult(area_ratio=0.0, bbox=None)

        current_roi = current_y[y1:y2, x1:x2].astype(np.float32)
        baseline_roi = baseline_y[y1:y2, x1:x2].astype(np.float32)

        # 2. 亮度归一化: 吸收小幅全局亮度漂移，保留物体差异与大幅环境突变
        #    - 偏移量用中值估计（局部物体不影响中值），且限制最大校正幅度，
        #      避免昼夜切换等大偏移被无差别吸收而漏判全局突变
        #    - 对比度缩放仅在双方纹理充分时启用，避免纯色场景缩放抹除物体
        median_b = float(np.median(baseline_roi))
        median_c = float(np.median(current_roi))
        offset = float(np.clip(median_b - median_c, -MAX_MEAN_SHIFT, MAX_MEAN_SHIFT))
        current_roi = current_roi + offset

        std_b = float(baseline_roi.std())
        std_c = float(current_roi.std())
        if std_c > 1.0 and std_b >= MIN_NORMALIZE_STD and std_b != std_c:
            scale = std_b / std_c
            current_roi = (current_roi - median_b) * scale + median_b
        current_roi = np.clip(current_roi, 0, 255)

        # 3. 绝对差分 + 二值化
        diff = cv2.absdiff(current_roi.astype(np.uint8), baseline_roi.astype(np.uint8))
        mask_diff = (diff > self.diff_threshold).astype(np.uint8)

        # 4. 多边形掩膜（ROI 坐标系）
        poly_mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
        poly_pts = np.array(
            [[(int(p[0]) - x1, int(p[1]) - y1) for p in polygon_px]],
            dtype=np.int32,
        )
        cv2.fillPoly(poly_mask, poly_pts, 1)
        mask_diff &= poly_mask

        poly_pixels = int(poly_mask.sum())
        if poly_pixels <= 0:
            return DiffResult(area_ratio=0.0, bbox=None)

        # 5. 形态学: 开运算去孤立噪点 → 闭运算连片
        mask_diff = cv2.morphologyEx(mask_diff, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask_diff = cv2.morphologyEx(mask_diff, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

        area_ratio = float(mask_diff.sum()) / poly_pixels

        # 6. 差异主连通域外接框（取面积最大的连通域，转全帧坐标）
        bbox = None
        if mask_diff.any():
            num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask_diff, connectivity=8)
            if num_labels > 1:
                # 跳过背景（label 0），取面积最大的连通域
                areas = stats[1:, cv2.CC_STAT_AREA]
                largest = 1 + int(np.argmax(areas))
                rx = int(stats[largest, cv2.CC_STAT_LEFT])
                ry = int(stats[largest, cv2.CC_STAT_TOP])
                rw = int(stats[largest, cv2.CC_STAT_WIDTH])
                rh = int(stats[largest, cv2.CC_STAT_HEIGHT])
                bbox = [x1 + rx, y1 + ry, x1 + rx + rw, y1 + ry + rh]

        return DiffResult(area_ratio=area_ratio, bbox=bbox)
