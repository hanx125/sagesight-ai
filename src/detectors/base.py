"""
基础检测器模块

定义检测器的统一接口和数据结构，包含配置类和基础检测器。

配置管理：
- DetectorConfig：检测器配置数据结构
- load_detector_config()：从 YAML 文件加载配置
- 所有配置参数在 YAML 文件中定义，不硬编码默认值
"""

import logging
import numpy as np
import yaml
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Literal
from dataclasses import dataclass
import time

from src.detectors.model_input_probe import resolve_input_hw

logger = logging.getLogger(__name__)


# ============================================================================
# 检测器配置数据结构
# ============================================================================

@dataclass
class DetectorConfig:
    """
    YOLO 检测器配置（数据结构定义）

    所有默认值从 YAML 文件加载，此文件只定义结构。
    使用 load_detector_config() 函数加载配置。
    """

    # 模型配置
    model_path: str = ""
    model_type: str = "yolov11n"

    # 推理后端配置
    backend: Literal["onnx_cpu", "onnx_cuda", "onnx_tensorrt", "tensorrt", "rknn"] = "onnx_cuda"

    # TensorRT 配置
    tensorrt_engine_path: str = ""
    tensorrt_engine_cache_path: str = "./trt_cache"
    engine_cache_path: str = "./trt_cache"  # 别名，兼容性
    tensorrt_lib_path: str = ""
    use_fp16: bool = True

    # RKNN 配置
    rknn_target: str = "rk3588"
    rknn_device_id: str = ""
    rknn_core_mask: int = 0

    # 推理配置
    conf_threshold: float = 0.25
    iou_threshold: float = 0.45
    min_box_area: float = 0.0

    # GPU 配置
    device_id: int = 0
    gpu_mem_limit: int = 0

    # 类别配置
    class_names: Tuple[str, ...] = ()
    target_classes: Optional[List[str]] = None  # 目标检测类别（可选）
    labels_file: str = ""  # 标签文件路径（可选）

    # 输入配置
    input_width: int = 640
    input_height: int = 640


def load_detector_config(config_path: str = "config/detector_config.yaml") -> DetectorConfig:
    """
    从 YAML 文件加载检测器配置
    
    Args:
        config_path: YAML 配置文件路径
    
    Returns:
        DetectorConfig 配置对象
    
    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置参数无效
    """
    # 检查文件存在
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    # 加载 YAML
    with open(config_file, 'r', encoding='utf-8') as f:
        config_dict = yaml.safe_load(f)
    
    # 提取 detector 部分
    detector_config = config_dict.get('detector', {})
    
    # 处理 class_names（如果是列表，转换为元组）
    if 'class_names' in detector_config and isinstance(detector_config['class_names'], list):
        detector_config['class_names'] = tuple(detector_config['class_names'])
    
    # 创建配置对象
    return DetectorConfig(**detector_config)


# ============================================================================
# 检测结果数据结构
# ============================================================================


@dataclass
class Detection:
    """单个检测结果"""
    bbox: Tuple[float, float, float, float]  # (x1, y1, x2, y2)
    confidence: float
    class_id: int
    class_name: str
    track_id: Optional[int] = None  # 跟踪ID（可选）


class BaseDetector:
    """
    检测器基类
    
    所有检测器后端必须实现以下接口：
    - initialize(): 初始化检测器
    - load_model(model_path): 加载模型
    - infer(input_tensor): 执行推理
    - detect(input_tensor): 执行检测（字典格式）
    - unload_model(): 卸载模型
    - get_inference_stats(): 获取统计信息
    """
    
    def __init__(self, config):
        """
        初始化检测器基类
        
        Args:
            config: 检测器配置对象
        """
        self.config = config
        
        # 性能统计
        self._inference_count = 0
        self._total_inference_time = 0.0
        
        # 输入信息（子类需要设置）
        self.input_name: Optional[str] = None
        self.input_shape: Optional[Tuple[int, int, int, int]] = None

    def verify_model_input_size(self, layout: str = 'nchw') -> Optional[Tuple[int, int]]:
        """
        校验已加载模型的真实输入尺寸与配置是否一致

        模型文件的输入形状在导出时固定，而预处理张量尺寸由配置（算法库 input_size）决定。
        两者不一致时推理会因输入形状不匹配而失败，任务表现为完全无检测结果。
        此处统一校验并给出明确提示，避免仅看到推理异常而无从定位。

        Args:
            layout: 'nchw'（ONNX / TensorRT）或 'nhwc'（RKNN）

        Returns:
            (height, width)；模型为动态尺寸或形状非法时返回 None
        """
        hw = resolve_input_hw(self.input_shape, layout)
        if hw is None:
            return None

        model_h, model_w = hw
        cfg_h = int(getattr(self.config, 'input_height', 0) or 0)
        cfg_w = int(getattr(self.config, 'input_width', 0) or 0)
        if (model_h, model_w) != (cfg_h, cfg_w):
            logger.error(
                f"[{type(self).__name__}] 模型输入尺寸与配置不一致："
                f"模型={model_w}x{model_h}，配置={cfg_w}x{cfg_h}。"
                f"预处理按配置尺寸构造张量，推理将因输入形状不匹配失败。"
                f"请将算法库中的“输入尺寸”改为 {model_w}x{model_h} 后重启任务。"
            )
        return hw
    
    def initialize(self):
        """初始化检测器（加载模型）"""
        raise NotImplementedError("子类必须实现 initialize() 方法")
    
    def load_model(self, model_path: str):
        """加载模型"""
        raise NotImplementedError("子类必须实现 load_model() 方法")
    
    def infer(self, input_tensor: np.ndarray) -> List[Detection]:
        """执行推理"""
        raise NotImplementedError("子类必须实现 infer() 方法")
    
    def detect(self, input_tensor: np.ndarray) -> Dict:
        """
        执行检测（字典格式）
        
        Args:
            input_tensor: 预处理后的张量 (1, 3, H, W) float32
        
        Returns:
            字典格式的检测结果
        """
        # 执行推理
        detections = self.infer(input_tensor)
        
        # 转换为字典格式
        detection_list = []
        for det in detections:
            detection_dict = {
                'class_id': det.class_id,
                'class_name': det.class_name,
                'confidence': det.confidence,
                'bbox': det.bbox
            }
            if det.track_id is not None:
                detection_dict['track_id'] = det.track_id
            detection_list.append(detection_dict)
        
        # 返回字典格式
        return {
            'detections': detection_list,
            'inference_time': self.get_inference_stats().get('last_inference_time', 0),
            'total_objects': len(detection_list)
        }
    
    def unload_model(self):
        """卸载模型"""
        raise NotImplementedError("子类必须实现 unload_model() 方法")
    
    def get_inference_stats(self) -> Dict:
        """获取推理统计信息"""
        avg_time = (
            self._total_inference_time / self._inference_count
            if self._inference_count > 0 else 0
        )
        
        return {
            'inference_count': self._inference_count,
            'total_time': self._total_inference_time,
            'avg_inference_time_ms': avg_time * 1000,
            'last_inference_time': self._last_inference_time if hasattr(self, '_last_inference_time') else 0,
            'fps': 1.0 / avg_time if avg_time > 0 else 0
        }
    
    def reset_stats(self):
        """重置统计信息"""
        self._inference_count = 0
        self._total_inference_time = 0.0
        self._last_inference_time = 0.0
    
    def _update_stats(self, inference_time: float):
        """
        更新统计信息（子类调用）
        
        Args:
            inference_time: 推理时间（秒）
        """
        self._inference_count += 1
        self._total_inference_time += inference_time
        self._last_inference_time = inference_time
    
    def _postprocess_yolo(
        self,
        output: np.ndarray,
        img_height: int,
        img_width: int,
        conf_threshold: float,
        iou_threshold: float,
        class_names: Tuple[str, ...],
        min_box_area: float = 10.0
    ) -> List[Detection]:
        """
        YOLO后处理：解码 + NMS + 坐标反归一化
        
        Args:
            output: 模型输出
            img_height: 输入图像高度
            img_width: 输入图像宽度
            conf_threshold: 置信度阈值
            iou_threshold: NMS IoU阈值
            class_names: 类别名称列表
            min_box_area: 最小检测框面积
        
        Returns:
            检测结果列表
        """
        import cv2
        
        # 解析模型输出
        # YOLOv8/YOLO11 ONNX 导出格式（C = 类别数）：
        #   - 原始格式：(1, C+4, N)，特征维在前，需转置
        #   - 转置格式：(1, N, C+4)，锚点维在前，直接取 output[0]
        # 注意：不能硬编码 84（COCO 80 类），单类别模型 C+4=5
        expected_feature_dim = len(class_names) + 4

        if output.ndim == 3:
            # PlateDetect（车牌检测）格式：(1, N, 14)：
            #   14 列 = x1,y1,x2,y2（模型输入空间）+ conf + plate_type + 4 关键点(x,y)
            #   与标准 YOLO 的区别：无类别分数列，坐标已是 xyxy
            if output.shape[2] == 14 and expected_feature_dim != 14:
                return self._postprocess_plate_detect(
                    output[0], img_height, img_width, conf_threshold,
                    iou_threshold, class_names, min_box_area
                )
            if output.shape[1] == expected_feature_dim:
                # 格式 (1, C+4, N) → 转置为 (N, C+4)
                output = np.transpose(output[0], (1, 0))
            elif output.shape[2] == expected_feature_dim:
                # 格式 (1, N, C+4) → 直接取 output[0]
                output = output[0]
            else:
                # 形状不匹配，无法安全解析
                return []
        elif output.ndim != 2:
            # 不支持的输出维度
            return []
        
        # 健壮性检查：确保output有足够的列
        if output.shape[1] < 5:
            return []
        
        # 提取检测信息
        boxes = output[:, :4]  # 边界框 (x, y, w, h)
        scores = output[:, 4:]  # 类别置信度
        
        # 计算每个框的最大置信度和对应类别
        max_class_confs = np.max(scores, axis=1)  # (N,)
        class_ids = np.argmax(scores, axis=1)  # (N,)
        
        # 应用置信度阈值
        valid_mask = max_class_confs > conf_threshold
        
        if not np.any(valid_mask):
            return []
        
        boxes = boxes[valid_mask]
        max_class_confs = max_class_confs[valid_mask]
        class_ids = class_ids[valid_mask]
        
        # 将 [x, y, w, h] 转换为 [x1, y1, x2, y2]
        # YOLO 输出是中心点坐标 + 宽高
        x_center = boxes[:, 0]
        y_center = boxes[:, 1]
        w = boxes[:, 2]
        h = boxes[:, 3]
        
        x1 = x_center - w / 2
        y1 = y_center - h / 2
        x2 = x_center + w / 2
        y2 = y_center + h / 2
        
        # 将坐标限制在输入图像范围内
        x1 = np.clip(x1, 0, img_width)
        y1 = np.clip(y1, 0, img_height)
        x2 = np.clip(x2, 0, img_width)
        y2 = np.clip(y2, 0, img_height)
        
        # 过滤过小的检测框（面积过滤）
        areas = (x2 - x1) * (y2 - y1)
        valid_area_mask = areas >= min_box_area
        
        if not np.any(valid_area_mask):
            return []
        
        x1 = x1[valid_area_mask]
        y1 = y1[valid_area_mask]
        x2 = x2[valid_area_mask]
        y2 = y2[valid_area_mask]
        max_class_confs = max_class_confs[valid_area_mask]
        class_ids = class_ids[valid_area_mask]
        
        # 应用 NMS（使用 OpenCV 加速）
        indices = self._nms_yolo(x1, y1, x2, y2, max_class_confs, class_ids, iou_threshold)
        
        # 构建检测结果
        detections = []
        for idx in indices:
            class_id = int(class_ids[idx])
            
            # 获取类别名称
            if class_id < len(class_names):
                class_name = class_names[class_id]
            else:
                class_name = f"class_{class_id}"
            
            detection = Detection(
                bbox=(float(x1[idx]), float(y1[idx]), float(x2[idx]), float(y2[idx])),
                confidence=float(max_class_confs[idx]),
                class_id=class_id,
                class_name=class_name
            )
            detections.append(detection)

        return detections

    def _postprocess_plate_detect(
        self,
        rows: np.ndarray,
        img_height: int,
        img_width: int,
        conf_threshold: float,
        iou_threshold: float,
        class_names: Tuple[str, ...],
        min_box_area: float = 10.0
    ) -> List[Detection]:
        """
        PlateDetect（车牌检测）专用后处理

        模型输出 (N, 14)：x1,y1,x2,y2 + conf + plate_type + 4 关键点(x,y)。
        与标准 YOLO 的区别：无类别分数列、坐标已是 xyxy（非中心点+宽高）。
        预处理为直接缩放（无 letterbox），坐标已处于模型输入空间，
        由调用方统一还原到原图；关键点与车牌类型本平台不使用，直接丢弃。

        Args:
            rows: (N, 14) 模型输出
            img_height, img_width: 模型输入尺寸（坐标空间）
            conf_threshold: 置信度阈值
            iou_threshold: NMS IoU 阈值
            class_names: 类别名称列表（单类 plate）
            min_box_area: 最小检测框面积

        Returns:
            检测结果列表（坐标为模型输入尺寸空间）
        """
        if rows.ndim != 2 or rows.shape[1] < 5:
            return []

        scores = rows[:, 4].astype(np.float32)
        valid_mask = scores > conf_threshold
        if not np.any(valid_mask):
            return []

        rows = rows[valid_mask]
        scores = scores[valid_mask]

        x1 = np.clip(rows[:, 0], 0, img_width)
        y1 = np.clip(rows[:, 1], 0, img_height)
        x2 = np.clip(rows[:, 2], 0, img_width)
        y2 = np.clip(rows[:, 3], 0, img_height)

        # 面积过滤
        areas = (x2 - x1) * (y2 - y1)
        valid_area_mask = areas >= min_box_area
        if not np.any(valid_area_mask):
            return []

        x1 = x1[valid_area_mask]
        y1 = y1[valid_area_mask]
        x2 = x2[valid_area_mask]
        y2 = y2[valid_area_mask]
        scores = scores[valid_area_mask]
        class_ids = np.zeros(len(scores), dtype=np.int64)  # 单类 plate

        indices = self._nms_yolo(x1, y1, x2, y2, scores, class_ids, iou_threshold)

        detections = []
        for idx in indices:
            class_id = int(class_ids[idx])
            if class_id < len(class_names):
                class_name = class_names[class_id]
            else:
                class_name = f"class_{class_id}"

            detections.append(Detection(
                bbox=(float(x1[idx]), float(y1[idx]), float(x2[idx]), float(y2[idx])),
                confidence=float(scores[idx]),
                class_id=class_id,
                class_name=class_name
            ))
        return detections

    def _nms_yolo(
        self,
        x1: np.ndarray,
        y1: np.ndarray,
        x2: np.ndarray,
        y2: np.ndarray,
        scores: np.ndarray,
        class_ids: np.ndarray,
        iou_threshold: float
    ) -> np.ndarray:
        """
        非极大值抑制（NMS）- 使用 OpenCV 加速
        
        Args:
            x1, y1, x2, y2: 边界框坐标
            scores: 置信度
            class_ids: 类别 ID
            iou_threshold: NMS IoU阈值
        
        Returns:
            保留的框的索引
        """
        import cv2
        
        # 使用 cv2.dnn.NMSBoxes 加速（按类别分别 NMS）
        unique_classes = np.unique(class_ids)
        keep_indices = []
        
        for cls in unique_classes:
            cls_mask = class_ids == cls
            cls_indices = np.where(cls_mask)[0]
            
            # 构建边界框列表 [[x, y, w, h], ...]
            boxes = []
            for idx in cls_indices:
                boxes.append([float(x1[idx]), float(y1[idx]),
                             float(x2[idx] - x1[idx]), float(y2[idx] - y1[idx])])
            
            cls_scores = scores[cls_mask].astype(np.float32)
            
            # 使用 OpenCV NMS
            nms_indices = cv2.dnn.NMSBoxes(
                boxes,
                cls_scores.tolist(),
                score_threshold=0.0,  # 已经过滤过阈值，这里设为 0
                nms_threshold=iou_threshold
            )
            
            # 转换为原始索引
            if len(nms_indices) > 0:
                if isinstance(nms_indices, np.ndarray):
                    nms_indices = nms_indices.flatten()
                for nms_idx in nms_indices:
                    keep_indices.append(cls_indices[nms_idx])
        
        return np.array(keep_indices)