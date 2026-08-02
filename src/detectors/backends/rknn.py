"""
RKNN 检测器后端

使用瑞芯微 NPU 进行推理。
适用于：
- RK3588 / RK3566 / RK3568 / RV1103 / RV1106 等瑞芯微平台
- 边缘设备推理（NPU 加速）
- 低功耗、高性能推理

特性：
- NPU 加速推理（比 CPU 快 10-20 倍）
- 支持多核 NPU（RK3588 双核 NPU，core_mask 控制）
- YOLO11 RKNN 专用后处理（DFL 解码，6 张量输出）
- 输入格式：(1, H, W, 3) uint8 NHWC（归一化已烘焙进 .rknn）

注意：
- 需要 RKNN 模型文件（.rknn）
- 需要 rknn-toolkit-lite2 Python 包（板端推理）
- 仅在瑞芯微平台上可用
"""

import os
import sys
import time
from typing import List, Dict, Optional, Tuple

import numpy as np

from src.detectors.base import BaseDetector, Detection, DetectorConfig
import logging

logger = logging.getLogger(__name__)

# RKNN Executor 导入（仅瑞芯微平台可用）
RKNN_EXECUTOR_AVAILABLE = False
try:
    from src.utils.rknn_executor import RKNNExecutor, is_rknn_available
    RKNN_EXECUTOR_AVAILABLE = True
    logger.debug(f"[RKNNDetector] RKNN Executor 已加载")
except ImportError:
    logger.debug(f"[RKNNDetector] RKNN Executor 未安装（仅瑞芯微平台可用）")


class RKNNDetector(BaseDetector):
    """
    RKNN 检测器

    使用瑞芯微 NPU 进行推理。

    支持的平台：
    - RK3588：双核 NPU，6 TOPS 算力
    - RK3566 / RK3568：单核 NPU，1 TOPS 算力
    - RV1103 / RV1106：单核 NPU，0.5 TOPS 算力

    数据流：
    - 输入：(1, H, W, 3) uint8 NHWC（RGB），归一化已烘焙进 .rknn 模型
    - 输出：6 个张量（3 尺度 × {box_dfl, class_conf}），经 DFL 解码 + NMS 得到检测框
    """

    def __init__(self, config: DetectorConfig):
        """
        初始化 RKNN 检测器

        Args:
            config: 检测器配置对象
        """
        super().__init__(config)

        # RKNN Executor
        self._rknn_executor = None

        # RKNN 配置
        self._rknn_target = config.rknn_target
        self._rknn_device_id = config.rknn_device_id
        self._rknn_core_mask = config.rknn_core_mask

        logger.debug(f"[RKNNDetector] 初始化完成")
        logger.debug(f"[RKNNDetector]   目标平台: {self._rknn_target}")
        logger.debug(f"[RKNNDetector]   核心掩码: {self._rknn_core_mask}")

    def initialize(self):
        """初始化检测器（加载模型）"""
        try:
            logger.debug(f"[RKNNDetector] 正在初始化检测器...")
            logger.debug(f"[RKNNDetector]   模型路径: {self.config.model_path}")
            logger.debug(f"[RKNNDetector]   目标平台: {self._rknn_target}")
            sys.stdout.flush()

            self.load_model()

            logger.debug(f"[RKNNDetector] 检测器初始化完成")
            sys.stdout.flush()
        except Exception as e:
            logger.debug(f"[RKNNDetector] 检测器初始化失败: {e}")
            sys.stdout.flush()
            raise

    def load_model(self, model_path: str = None):
        """
        加载 RKNN 模型

        Args:
            model_path: 模型路径（可选，覆盖配置中的路径）

        Raises:
            ImportError: RKNN Executor 未安装
            FileNotFoundError: 模型文件不存在
            ValueError: 模型文件格式错误
            RuntimeError: 模型加载失败
        """
        # 检查 RKNN Executor 是否可用
        if not RKNN_EXECUTOR_AVAILABLE:
            raise ImportError(
                "RKNN Executor 未安装，无法使用 RKNN 后端。\n\n"
                "请在瑞芯微平台上安装 rknn-toolkit-lite2:\n"
                "  pip install rknn-toolkit-lite2"
            )

        # 使用配置中的模型路径或参数指定的路径
        model_path = model_path or self.config.model_path

        if not model_path:
            raise FileNotFoundError("RKNN 模型路径未指定")

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"RKNN 模型文件不存在：{model_path}")

        if not model_path.endswith('.rknn'):
            raise ValueError(f"模型文件必须是 RKNN 格式（.rknn）：{model_path}")

        try:
            # 创建 RKNN Executor
            self._rknn_executor = RKNNExecutor(
                model_path=model_path,
                target=self._rknn_target,
                device_id=self._rknn_device_id,
                core_mask=self._rknn_core_mask,
            )

            # 输入形状（NHWC，统一接口存为 NCHW 形式以便 get_info 展示）
            self.input_shape = (1, 3, self.config.input_height, self.config.input_width)

            logger.debug(f"[RKNNDetector] RKNN 模型加载成功：{model_path}")
            logger.debug(f"  目标平台：{self._rknn_target}")
            logger.debug(f"  输入格式（NHWC）：(1, {self.config.input_height}, {self.config.input_width}, 3) uint8")
            logger.debug(f"  提示：RKNN 推理使用 NPU 加速")

        except Exception as e:
            raise RuntimeError(f"RKNN 模型加载失败：{e}")

    def infer(self, input_tensor: np.ndarray) -> List[Detection]:
        """
        执行推理（RKNN NPU）

        输入张量为 (1, H, W, 3) uint8 NHWC（RGB），与 create_tensor_input_hw 输出一致。
        归一化（/255）已烘焙进 .rknn 模型，此处直接传 uint8。

        Args:
            input_tensor: (1, H, W, 3) uint8 NHWC RGB

        Returns:
            检测结果列表（坐标为模型输入尺寸空间，由调用方加 region 偏移）
        """
        if self._rknn_executor is None:
            raise RuntimeError("RKNN 模型未加载，请先调用 load_model()")

        try:
            start_time = time.time()

            # RKNN YOLO 模型输入为 4 维 (1, H, W, 3) uint8 NHWC（RGB）
            # create_tensor_input_hw 输出为 (1, H, W, 3)，直接使用；
            # 若调用方传入 3 维 (H, W, 3)，则补 batch 维。
            if input_tensor.ndim == 4 and input_tensor.shape[0] == 1:
                rknn_input = input_tensor
            elif input_tensor.ndim == 3:
                rknn_input = np.expand_dims(input_tensor, axis=0)
            else:
                raise ValueError(
                    f"输入张量格式错误：期望 (1, H, W, 3) 或 (H, W, 3)，得到 {input_tensor.shape}"
                )

            # 执行 RKNN 推理 → 返回 6 个张量
            outputs = self._rknn_executor.run(rknn_input)

            # YOLO11 RKNN 专用后处理（DFL 解码 + NMS）
            # rknn_input 为 4 维 (1, H, W, 3)，H/W 在 axis 1/2
            img_height = rknn_input.shape[1]
            img_width = rknn_input.shape[2]
            detections = self._postprocess_yolo11_rknn(
                outputs,
                conf_threshold=self.config.conf_threshold,
                iou_threshold=self.config.iou_threshold,
                class_names=self.config.class_names,
                min_box_area=self.config.min_box_area,
                img_height=img_height,
                img_width=img_width,
            )

            # 更新统计信息
            inference_time = time.time() - start_time
            self._update_stats(inference_time)

            return detections

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.debug(f"[RKNNDetector] 推理失败：{e}")
            logger.debug(f"[RKNNDetector] 错误详情:\n{error_details}")
            raise RuntimeError(f"推理失败：{e}")

    # ========================================
    # YOLO11 RKNN 后处理（移植自 rknn_model_zoo yolo11.py）
    # ========================================
    @staticmethod
    def _dfl(position: np.ndarray) -> np.ndarray:
        """Distribution Focal Loss 解码（numpy 实现，无 torch 依赖）

        Args:
            position: (n, c, h, w)，c = 4 * mc

        Returns:
            (n, 4, h, w) 解码后的边框偏移
        """
        n, c, h, w = position.shape
        p_num = 4
        mc = c // p_num
        y = position.reshape(n, p_num, mc, h, w).astype(np.float32)

        # softmax over mc dim (axis=2)
        y_max = np.max(y, axis=2, keepdims=True)
        e = np.exp(y - y_max)
        y = e / np.sum(e, axis=2, keepdims=True)

        # 加权求和：sum(softmax * range(mc))
        acc = np.arange(mc, dtype=np.float32).reshape(1, 1, mc, 1, 1)
        y = np.sum(y * acc, axis=2)  # (n, p_num, h, w)
        return y

    @classmethod
    def _box_process(cls, position: np.ndarray, img_w: int, img_h: int) -> np.ndarray:
        """单分支边框解码：DFL + grid 还原 → xyxy（模型输入坐标空间）

        Args:
            position: (1, 4*mc, grid_h, grid_w) box_dfl 输出
            img_w, img_h: 模型输入宽高（用于 stride 计算）

        Returns:
            (1, 4, grid_h, grid_w) xyxy 坐标
        """
        grid_h, grid_w = position.shape[2], position.shape[3]
        col, row = np.meshgrid(np.arange(0, grid_w), np.arange(0, grid_h))
        col = col.reshape(1, 1, grid_h, grid_w)
        row = row.reshape(1, 1, grid_h, grid_w)
        grid = np.concatenate((col, row), axis=1)
        stride = np.array([img_h // grid_h, img_w // grid_w], dtype=np.float32).reshape(1, 2, 1, 1)

        position = cls._dfl(position)
        box_xy = grid + 0.5 - position[:, 0:2, :, :]
        box_xy2 = grid + 0.5 + position[:, 2:4, :, :]
        xyxy = np.concatenate((box_xy * stride, box_xy2 * stride), axis=1)
        return xyxy

    @staticmethod
    def _filter_boxes(boxes, box_confidences, box_class_probs, conf_threshold):
        """置信度阈值过滤

        score = max(class_probs) * objectness（RKNN YOLO11 objectness=1）
        """
        box_confidences = box_confidences.reshape(-1)
        class_max_score = np.max(box_class_probs, axis=-1)
        classes = np.argmax(box_class_probs, axis=-1)

        _class_pos = np.where(class_max_score * box_confidences >= conf_threshold)
        scores = (class_max_score * box_confidences)[_class_pos]
        boxes = boxes[_class_pos]
        classes = classes[_class_pos]

        return boxes, classes, scores

    @staticmethod
    def _nms_boxes(boxes, scores, iou_threshold):
        """单类 NMS"""
        x = boxes[:, 0]
        y = boxes[:, 1]
        w = boxes[:, 2] - boxes[:, 0]
        h = boxes[:, 3] - boxes[:, 1]

        areas = w * h
        order = scores.argsort()[::-1]

        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            xx1 = np.maximum(x[i], x[order[1:]])
            yy1 = np.maximum(y[i], y[order[1:]])
            xx2 = np.minimum(x[i] + w[i], x[order[1:]] + w[order[1:]])
            yy2 = np.minimum(y[i] + h[i], y[order[1:]] + h[order[1:]])

            w1 = np.maximum(0.0, xx2 - xx1 + 0.00001)
            h1 = np.maximum(0.0, yy2 - yy1 + 0.00001)
            inter = w1 * h1
            ovr = inter / (areas[i] + areas[order[1:]] - inter)
            inds = np.where(ovr <= iou_threshold)[0]
            order = order[inds + 1]
        return np.array(keep)

    def _postprocess_yolo11_rknn(
        self,
        input_data,
        conf_threshold: float,
        iou_threshold: float,
        class_names: Tuple[str, ...],
        min_box_area: float,
        img_height: int,
        img_width: int,
    ) -> List[Detection]:
        """
        YOLO11 RKNN 后处理：6 张量 → DFL 解码 → 阈值过滤 → 按类 NMS

        Args:
            input_data: RKNN 输出张量列表（6 个：3 尺度 × {box_dfl, class_conf}）
            conf_threshold: 置信度阈值
            iou_threshold: NMS IoU 阈值
            class_names: 类别名称元组
            min_box_area: 最小检测框面积（像素）
            img_height, img_width: 模型输入高/宽（坐标空间）

        Returns:
            检测结果列表（坐标为模型输入尺寸空间）
        """
        # 兼容单 ndarray 输出
        if isinstance(input_data, np.ndarray):
            input_data = [input_data]
        if not isinstance(input_data, (list, tuple)) or len(input_data) == 0:
            return []

        boxes_list, scores_list, classes_conf_list = [], [], []
        default_branch = 3
        pair_per_branch = len(input_data) // default_branch
        if pair_per_branch == 0:
            return []

        for i in range(default_branch):
            boxes_list.append(
                self._box_process(input_data[pair_per_branch * i], img_width, img_height)
            )
            classes_conf_list.append(input_data[pair_per_branch * i + 1])
            # objectness 置 1（RKNN YOLO11 已合并 objectness）
            scores_list.append(
                np.ones_like(
                    input_data[pair_per_branch * i + 1][:, :1, :, :], dtype=np.float32
                )
            )

        def sp_flatten(_in):
            ch = _in.shape[1]
            _in = _in.transpose(0, 2, 3, 1)
            return _in.reshape(-1, ch)

        boxes_list = [sp_flatten(_v) for _v in boxes_list]
        classes_conf_list = [sp_flatten(_v) for _v in classes_conf_list]
        scores_list = [sp_flatten(_v) for _v in scores_list]

        boxes = np.concatenate(boxes_list)
        classes_conf = np.concatenate(classes_conf_list)
        scores = np.concatenate(scores_list)

        # 阈值过滤
        boxes, classes, scores = self._filter_boxes(
            boxes, scores, classes_conf, conf_threshold
        )

        if len(boxes) == 0:
            return []

        # 按类 NMS
        nboxes, nclasses, nscores = [], [], []
        for c in set(classes.tolist()):
            inds = np.where(classes == c)
            b = boxes[inds]
            c_arr = classes[inds]
            s = scores[inds]
            keep = self._nms_boxes(b, s, iou_threshold)
            if len(keep) != 0:
                nboxes.append(b[keep])
                nclasses.append(c_arr[keep])
                nscores.append(s[keep])

        if not nclasses:
            return []

        boxes = np.concatenate(nboxes)
        classes = np.concatenate(nclasses)
        scores = np.concatenate(nscores)

        # 构造 Detection，应用面积过滤
        detections: List[Detection] = []
        for box, score, cl in zip(boxes, scores, classes):
            x1, y1, x2, y2 = [float(_b) for _b in box]
            if (x2 - x1) * (y2 - y1) < min_box_area:
                continue
            cl = int(cl)
            class_name = class_names[cl] if cl < len(class_names) else f"class_{cl}"
            detections.append(Detection(
                bbox=(x1, y1, x2, y2),
                confidence=float(score),
                class_id=cl,
                class_name=class_name,
            ))
        return detections

    def unload_model(self):
        """卸载模型，释放 RKNN Executor 和 NPU 资源"""
        if self._rknn_executor is not None:
            self._rknn_executor.release()
            self._rknn_executor = None
        self.input_shape = None
        logger.debug("[RKNNDetector] 模型已卸载")

    def get_info(self) -> Dict:
        """获取检测器信息"""
        info = {
            'backend': 'rknn',
            'model_path': self.config.model_path,
            'target_platform': self._rknn_target,
            'device_id': self._rknn_device_id,
            'core_mask': self._rknn_core_mask,
            'input_shape': self.input_shape,
            'class_names': self.config.class_names,
            'conf_threshold': self.config.conf_threshold,
            'iou_threshold': self.config.iou_threshold,
        }
        stats = self.get_inference_stats()
        info.update(stats)
        return info
