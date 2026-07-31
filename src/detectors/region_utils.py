"""
Region 生成与检测数据归约工具

核心功能：
- 从分离的 I420 平面（y_plane/u_plane/v_plane）生成检测区域
- 将 region 裁剪并转换为模型输入 tensor
- 对多 region 检测结果进行 NMS 和合并去重

帧格式说明:
- 标准 I420 帧是 (H*3//2, W) 完整 YUV420p 帧
- 本项目解码输出分离的 y_plane(H,W) / u_plane(H/2,W/2) / v_plane(H/2,W/2)
- 通过 merge_yuv_planes 合并为标准 I420 帧后进行 region 裁剪
"""

import logging
import math
from collections import defaultdict
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ========================================
# NMS / 合并 常量
# ========================================
GRID_SIZE = 8
LABEL_NMS_DEFAULT = 0.4
LABEL_CONSOLIDATION_DEFAULT = 0.4
# 标签特定的 NMS / 合并阈值（可按需扩展）
LABEL_NMS_MAP: dict[str, float] = {}
LABEL_CONSOLIDATION_MAP: dict[str, float] = {}


# ========================================
# 基础几何函数
# ========================================
def area(box) -> int:
    """计算矩形面积（含边界+1）"""
    return (box[2] - box[0] + 1) * (box[3] - box[1] + 1)


def intersection(box_a, box_b) -> Optional[list[int]]:
    """返回两个矩形的交集，无交集返回 None"""
    if (
        box_a[2] < box_b[0]
        or box_a[0] > box_b[2]
        or box_a[1] > box_b[3]
        or box_a[3] < box_b[1]
    ):
        return None
    return (
        max(box_a[0], box_b[0]),
        max(box_a[1], box_b[1]),
        min(box_a[2], box_b[2]),
        min(box_a[3], box_b[3]),
    )


def intersection_over_union(box_a, box_b) -> float:
    """计算 IoU"""
    intersect = intersection(box_a, box_b)
    if intersect is None:
        return 0.0
    inter_area = max(0, intersect[2] - intersect[0] + 1) * max(
        0, intersect[3] - intersect[1] + 1
    )
    if inter_area == 0:
        return 0.0
    box_a_area = (box_a[2] - box_a[0] + 1) * (box_a[3] - box_a[1] + 1)
    box_b_area = (box_b[2] - box_b[0] + 1) * (box_b[3] - box_b[1] + 1)
    return inter_area / float(box_a_area + box_b_area - inter_area)


def clipped(obj, frame_shape) -> bool:
    """判断检测目标是否被 region 边界裁剪（用于降低边缘目标置信度）"""
    box = obj[2]
    region = obj[5]
    if (
        (region[0] > 5 and box[0] - region[0] <= 5)
        or (region[1] > 5 and box[1] - region[1] <= 5)
        or (region[2] < frame_shape[1] - 5 and region[2] - box[2] <= 5)
        or (region[3] < frame_shape[0] - 5 and region[3] - box[3] <= 5)
    ):
        return True
    return False


def box_overlaps(b1, b2) -> bool:
    """两矩形是否相交"""
    if b1[2] < b2[0] or b1[0] > b2[2] or b1[1] > b2[3] or b1[3] < b2[1]:
        return False
    return True


def box_inside(b1, b2) -> bool:
    """b2 是否完全在 b1 内部"""
    if b2[0] >= b1[0] and b2[1] >= b1[1] and b2[2] <= b1[2] and b2[3] <= b1[3]:
        return True
    return False


def intersects_any(box_a, boxes) -> bool:
    """box_a 是否与 boxes 中任一矩形相交"""
    for box in boxes:
        if box_overlaps(box_a, box):
            return True
    return False


def inside_any(box_a, boxes) -> bool:
    """box_a 是否被 boxes 中任一矩形包含"""
    for box in boxes:
        if box_inside(box, box_a):
            return True
    return False


# ========================================
# Region 计算
# ========================================
def calculate_region(
    frame_shape, xmin, ymin, xmax, ymax, model_size, multiplier=2
):
    """
    根据边界框计算正方形 region（边长可被4整除，不小于 model_size）

    Returns:
        (x_offset, y_offset, x_offset+size, y_offset+size)
    """
    # size 取最长边的 multiplier 倍，且能被4整除
    size = int((max(xmax - xmin, ymax - ymin) * multiplier) // 4 * 4)
    if size < model_size:
        size = model_size

    # 以边界框中心为 region 中心
    x_offset = int((xmax - xmin) / 2.0 + xmin - size / 2.0)
    if x_offset < 0:
        x_offset = 0
    elif x_offset > (frame_shape[1] - size):
        x_offset = max(0, (frame_shape[1] - size))

    y_offset = int((ymax - ymin) / 2.0 + ymin - size / 2.0)
    if y_offset < 0:
        y_offset = 0
    elif y_offset > (frame_shape[0] - size):
        y_offset = max(0, (frame_shape[0] - size))

    return (x_offset, y_offset, x_offset + size, y_offset + size)


def get_yuv_crop(frame_shape, crop):
    """
    计算 YUV420p 帧中 region 的 Y/U/V 通道裁剪坐标

    frame_shape: (H*3//2, W) 完整 I420 帧的 shape
    crop: (x1, y1, x2, y2) 原图坐标
    """
    frame_height = frame_shape[0] // 3 * 2  # 即 H
    frame_width = frame_shape[1]  # 即 W

    uv_width = frame_width // 2
    uv_height = frame_height // 4

    uv_x_offset = crop[0] // 2
    uv_y_offset = crop[1] // 4

    uv_crop_width = (crop[2] - crop[0]) // 2
    uv_crop_height = (crop[3] - crop[1]) // 4

    y = (crop[0], crop[1], crop[0] + uv_crop_width * 2, crop[1] + uv_crop_height * 4)

    u1 = (
        0 + uv_x_offset,
        frame_height + uv_y_offset,
        0 + uv_x_offset + uv_crop_width,
        frame_height + uv_y_offset + uv_crop_height,
    )
    u2 = (
        uv_width + uv_x_offset,
        frame_height + uv_y_offset,
        uv_width + uv_x_offset + uv_crop_width,
        frame_height + uv_y_offset + uv_crop_height,
    )
    v1 = (
        0 + uv_x_offset,
        frame_height + uv_height + uv_y_offset,
        0 + uv_x_offset + uv_crop_width,
        frame_height + uv_height + uv_y_offset + uv_crop_height,
    )
    v2 = (
        uv_width + uv_x_offset,
        frame_height + uv_height + uv_y_offset,
        uv_width + uv_x_offset + uv_crop_width,
        frame_height + uv_height + uv_y_offset + uv_crop_height,
    )

    return y, u1, u2, v1, v2


def yuv_crop_and_resize(frame, region, height=None):
    """
    从 YUV420p 帧裁剪 region 并保持长宽比填充为正方形

    frame: (H*3//2, W) 完整 I420 帧
    region: (x1, y1, x2, y2)
    Returns: (size + size//2, size) 的 I420 帧（size 为4的倍数）
    """
    h = frame.shape[0] // 3 * 2
    width = frame.shape[1]

    # region 可能超出帧边界，计算实际裁剪框
    crop_x1 = max(0, region[0])
    crop_y1 = max(0, region[1])
    crop_x2 = min(width, region[2])
    crop_y2 = min(h, region[3])
    crop_box = (crop_x1, crop_y1, crop_x2, crop_y2)

    y, u1, u2, v1, v2 = get_yuv_crop(frame.shape, crop_box)

    # region 起点在帧外时，裁剪帧内需要偏移
    y_channel_x_offset = abs(min(0, region[0]))
    y_channel_y_offset = abs(min(0, region[1]))

    uv_channel_x_offset = y_channel_x_offset // 2
    uv_channel_y_offset = y_channel_y_offset // 4

    # 构造正方形 I420 输出帧（size 为4的倍数）
    size = (region[3] - region[1]) // 4 * 4
    yuv_cropped_frame = np.zeros((size + size // 2, size), np.uint8)
    # 填充默认值：Y=16（黑），UV=128（无色）
    yuv_cropped_frame[:] = 128
    yuv_cropped_frame[0:size, 0:size] = 16

    # 拷贝 Y 通道
    yuv_cropped_frame[
        y_channel_y_offset : y_channel_y_offset + y[3] - y[1],
        y_channel_x_offset : y_channel_x_offset + y[2] - y[0],
    ] = frame[y[1] : y[3], y[0] : y[2]]

    uv_crop_width = u1[2] - u1[0]
    uv_crop_height = u1[3] - u1[1]

    # 拷贝 u1
    yuv_cropped_frame[
        size + uv_channel_y_offset : size + uv_channel_y_offset + uv_crop_height,
        0 + uv_channel_x_offset : 0 + uv_channel_x_offset + uv_crop_width,
    ] = frame[u1[1] : u1[3], u1[0] : u1[2]]

    # 拷贝 u2
    yuv_cropped_frame[
        size + uv_channel_y_offset : size + uv_channel_y_offset + uv_crop_height,
        size // 2 + uv_channel_x_offset : size // 2 + uv_channel_x_offset + uv_crop_width,
    ] = frame[u2[1] : u2[3], u2[0] : u2[2]]

    # 拷贝 v1
    yuv_cropped_frame[
        size + size // 4 + uv_channel_y_offset : size + size // 4 + uv_channel_y_offset + uv_crop_height,
        0 + uv_channel_x_offset : 0 + uv_channel_x_offset + uv_crop_width,
    ] = frame[v1[1] : v1[3], v1[0] : v1[2]]

    # 拷贝 v2
    yuv_cropped_frame[
        size + size // 4 + uv_channel_y_offset : size + size // 4 + uv_channel_y_offset + uv_crop_height,
        size // 2 + uv_channel_x_offset : size // 2 + uv_channel_x_offset + uv_crop_width,
    ] = frame[v2[1] : v2[3], v2[0] : v2[2]]

    return yuv_cropped_frame


def yuv_region_2_rgb(frame, region):
    """从 YUV420p 帧裁剪 region 并转为 RGB"""
    yuv_cropped_frame = yuv_crop_and_resize(frame, region)
    return cv2.cvtColor(yuv_cropped_frame, cv2.COLOR_YUV2RGB_I420)


# ========================================
# I420 平面合并（本项目适配）
# ========================================
def merge_yuv_planes(y_plane, u_plane, v_plane, frame_shape):
    """
    将分离的 I420 平面合并为标准 I420 帧 (H*3//2, W)

    本项目解码器输出分离的 y_plane(H,W) / u_plane(H/2,W/2) / v_plane(H/2,W/2)
    cv2.COLOR_YUV2RGB_I420 期望 (H*3//2, W) 布局：
      - yuv[0:H, 0:W] = Y
      - yuv[H:H+H/4, 0:W] = U(H/2,W/2).reshape(H/4, W)
      - yuv[H+H/4:H+H/2, 0:W] = V(H/2,W/2).reshape(H/4, W)

    Args:
        y_plane: (H, W) 灰度图
        u_plane: (H/2, W/2) U 色度平面
        v_plane: (H/2, W/2) V 色度平面
        frame_shape: (H, W)

    Returns:
        (H + H//2, W) uint8 标准 I420 帧
    """
    H, W = frame_shape
    yuv_frame = np.zeros((H + H // 2, W), dtype=np.uint8)
    yuv_frame[:H, :W] = y_plane
    # U(H/2, W/2) reshape 为 (H/4, W)：前 H/4 行放左半，后 H/4 行放右半
    # U(V) 的 u1/u2 布局，cv2.COLOR_YUV2RGB_I420 可正确转换
    yuv_frame[H : H + H // 4, :W] = u_plane.reshape(H // 4, W)
    yuv_frame[H + H // 4 : H + H // 2, :W] = v_plane.reshape(H // 4, W)
    return yuv_frame


def create_tensor_input(yuv_frame, region, model_size=(640, 640)):
    """
    从 I420 帧裁剪 region 并生成模型输入 tensor

    Args:
        yuv_frame: (H*3//2, W) 标准 I420 帧（由 merge_yuv_planes 生成）
        region: (x1, y1, x2, y2) 原图坐标
        model_size: (height, width) 模型输入尺寸

    Returns:
        (1, model_h, model_w, 3) uint8 RGB tensor
    """
    cropped_frame = yuv_region_2_rgb(yuv_frame, region)

    # 缩放到模型输入尺寸
    if cropped_frame.shape != (model_size[0], model_size[1], 3):
        cropped_frame = cv2.resize(
            cropped_frame,
            dsize=(model_size[1], model_size[0]),
            interpolation=cv2.INTER_LINEAR,
        )

    # 扩展维度：模型期望 [1, H, W, 3]
    return np.expand_dims(cropped_frame, axis=0)


# ========================================
# Region 聚类生成
# ========================================
def get_min_region_size(model_config) -> int:
    """获取最小 region 尺寸（取模型宽高的最大值）"""
    # 兼容 dict 或对象
    if isinstance(model_config, dict):
        return max(model_config.get("input_height", 640), model_config.get("input_width", 640))
    return max(getattr(model_config, "input_height", 640), getattr(model_config, "input_width", 640))


def get_cluster_boundary(box, min_region):
    """计算 box 的最大聚类边界（box 占 region 10% 面积）"""
    box_width = box[2] - box[0]
    box_height = box[3] - box[1]
    max_region_area = abs(box_width * box_height) / 0.1
    max_region_size = max(min_region, int(math.sqrt(max_region_area)))

    centroid = (box_width / 2 + box[0], box_height / 2 + box[1])

    max_x_dist = int(max_region_size - box_width / 2 * 1.1)
    max_y_dist = int(max_region_size - box_height / 2 * 1.1)

    return [
        int(centroid[0] - max_x_dist),
        int(centroid[1] - max_y_dist),
        int(centroid[0] + max_x_dist),
        int(centroid[1] + max_y_dist),
    ]


def get_cluster_candidates(frame_shape, min_region, boxes):
    """
    对 box 列表进行聚类，返回候选簇（每个簇是 box 索引列表）

    聚类规则：box 的最大 region 边界内的其他 box 可聚为一簇，
    且合并后各 box 面积不小于 region 面积的 5%
    """
    cluster_candidates = []
    used_boxes = []
    for current_index, b in enumerate(boxes):
        if current_index in used_boxes:
            continue
        cluster = [current_index]
        used_boxes.append(current_index)
        cluster_boundary = get_cluster_boundary(b, min_region)
        for compare_index, compare_box in enumerate(boxes):
            if compare_index in used_boxes:
                continue
            if not box_inside(cluster_boundary, compare_box):
                continue
            potential_cluster = cluster + [compare_index]
            cluster_region = get_cluster_region(
                frame_shape, min_region, potential_cluster, boxes
            )
            should_cluster = True
            if (cluster_region[2] - cluster_region[0]) > min_region:
                for b in potential_cluster:
                    box = boxes[b]
                    if area(box) / area(cluster_region) < 0.05:
                        should_cluster = False
                        break
            if should_cluster:
                cluster.append(compare_index)
                used_boxes.append(compare_index)
        cluster_candidates.append(cluster)

    unique = {tuple(sorted(c)) for c in cluster_candidates}
    return [list(tup) for tup in unique]


def get_cluster_region(frame_shape, min_region, cluster, boxes):
    """根据簇内所有 box 的并集计算正方形 region"""
    min_x = frame_shape[1]
    min_y = frame_shape[0]
    max_x = 0
    max_y = 0
    for b in cluster:
        min_x = min(boxes[b][0], min_x)
        min_y = min(boxes[b][1], min_y)
        max_x = max(boxes[b][2], max_x)
        max_y = max(boxes[b][3], max_y)
    return calculate_region(
        frame_shape, min_x, min_y, max_x, max_y, min_region, multiplier=1.35
    )


def get_startup_regions(
    frame_shape, region_min_size, region_grid=None
):
    """
    获取启动扫描区域（基于历史 region grid）

    若无 region_grid（首次启动），返回空列表（启动后由运动检测驱动）
    """
    if not region_grid:
        return []
    all_cells = np.concatenate(region_grid).flat
    startup_cells = sorted(all_cells, key=lambda c: len(c.get("sizes", [])), reverse=True)[
        0:8
    ]
    regions = []
    for cell in startup_cells:
        if not cell.get("sizes"):
            break
        x = frame_shape[1] / GRID_SIZE * (0.5 + cell["x"])
        y = frame_shape[0] / GRID_SIZE * (0.5 + cell["y"])
        size = cell["mean"] * frame_shape[1]
        regions.append(
            calculate_region(
                frame_shape,
                x - size / 2,
                y - size / 2,
                x + size / 2,
                y + size / 2,
                region_min_size,
                multiplier=1,
            )
        )
    return regions


# ========================================
# 检测结果归约
# ========================================
def reduce_detections(frame_shape, all_detections):
    """
    对多 region 的检测结果进行 NMS + 合并去重

    all_detections: [(label, score, (x1,y1,x2,y2), area, ratio, region), ...]
    Returns: 去重后的检测列表
    """

    def reduce_overlapping_detections(detections):
        """对同类别的重叠框做 NMS"""
        detected_object_groups = defaultdict(lambda: [])
        for detection in detections:
            detected_object_groups[detection[0]].append(detection)

        selected_objects = []
        for group in detected_object_groups.values():
            label = group[0][0]
            boxes = [
                (o[2][0], o[2][1], o[2][2] - o[2][0], o[2][3] - o[2][1]) for o in group
            ]
            # 边缘裁剪的目标降低置信度到 0.6，避免被 NMS 误丢
            confidences = [0.6 if clipped(o, frame_shape) else o[1] for o in group]

            indices = cv2.dnn.NMSBoxes(
                boxes, confidences, 0.5, LABEL_NMS_MAP.get(label, LABEL_NMS_DEFAULT)
            )

            for index in indices:
                index = index if isinstance(index, np.int32) else index[0]
                selected_objects.append(group[index])

        return selected_objects

    def get_consolidated_object_detections(detections):
        """丢弃重叠过高的检测（小目标被大目标包含）"""
        detected_object_groups = defaultdict(lambda: [])
        for detection in detections:
            detected_object_groups[detection[0]].append(detection)

        consolidated_detections = []
        for group in detected_object_groups.values():
            if len(group) == 1:
                consolidated_detections.append(group[0])
                continue

            sorted_by_area = sorted(group, key=lambda g: g[3])

            for current_detection_idx in range(0, len(sorted_by_area)):
                current_detection = sorted_by_area[current_detection_idx]
                current_label = current_detection[0]
                current_box = current_detection[2]
                overlap = 0
                for to_check_idx in range(
                    min(current_detection_idx + 1, len(sorted_by_area)),
                    len(sorted_by_area),
                ):
                    to_check = sorted_by_area[to_check_idx][2]
                    # 面积比 < 5% 不比较（避免大目标压制远处小目标）
                    if area(current_box) / area(to_check) < 0.05:
                        continue
                    intersect_box = intersection(current_box, to_check)
                    if intersect_box is not None and area(intersect_box) / area(
                        current_box
                    ) > LABEL_CONSOLIDATION_MAP.get(
                        current_label, LABEL_CONSOLIDATION_DEFAULT
                    ):
                        overlap = 1
                        break
                if overlap == 0:
                    consolidated_detections.append(sorted_by_area[current_detection_idx])

        return consolidated_detections

    return get_consolidated_object_detections(reduce_overlapping_detections(all_detections))


# ========================================
# 目标过滤
# ========================================
def is_object_filtered(obj, objects_to_track, object_filters):
    """
    根据目标类别/面积/比例/置信度/掩码过滤

    obj: (label, score, box, area, ratio, region)
    objects_to_track: 要跟踪的类别列表
    object_filters: {label: {min_area, max_area, min_score, min_ratio, max_ratio, mask}}
    """
    object_name = obj[0]
    object_score = obj[1]
    object_box = obj[2]
    object_area = obj[3]
    object_ratio = obj[4]

    if object_name not in objects_to_track:
        return True

    if object_name in object_filters:
        obj_settings = object_filters[object_name]
        if obj_settings.get("min_area", 0) > object_area:
            return True
        if obj_settings.get("max_area", float("inf")) < object_area:
            return True
        if obj_settings.get("min_score", 0) > object_score:
            return True
        if obj_settings.get("min_ratio", 0) > object_ratio:
            return True
        if obj_settings.get("max_ratio", float("inf")) < object_ratio:
            return True

        mask = obj_settings.get("mask")
        if mask is not None:
            object_xmin = object_box[0]
            object_xmax = object_box[2]
            object_ymax = object_box[3]
            y_location = min(int(object_ymax), len(mask) - 1)
            x_location = min(
                int((object_xmax + object_xmin) / 2.0), len(mask[0]) - 1
            )
            if mask[y_location][x_location] == 0:
                return True

    return False
