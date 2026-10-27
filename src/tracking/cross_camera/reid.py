"""ReID 外观特征推理后端

职责单一：把一张目标裁剪图变成 L2 归一化的特征向量，两张图的相似度就是点积。

为什么用 onnxruntime 而不是 PyTorch：
    部署目标是 RK3588（aarch64），装 PyTorch 要 1GB+ 且拖 CUDA 依赖。
    onnxruntime 只有几十 MB，CPU 推理一条轨迹一次完全够用，
    将来要上 NPU 也只需换 provider（RKNN 的 onnx 路线）。

模型从哪来：
    tools/export_reid_onnx.py 导出，默认 ResNet18 + GeM。
    模型文件缺失、onnxruntime 未安装、推理异常 —— 一律降级返回 None，
    由上层退回颜色直方图，绝不因为特征模块拖垮主流程。

线程安全：
    单例 + 懒加载 + 锁。融合服务是单线程，但 API/重建接口可能并发调用，
    所以推理入口加锁，避免 onnxruntime session 并发 run 的未定义行为。
"""

import logging
import os
import threading
import zlib
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# ImageNet 归一化（与导出脚本保持一致，改一处要改两处）
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)


def _hash_mean(mu: np.ndarray) -> int:
    """均值指纹：同一份 mu 算出同一个数，写进描述子防止不同均值特征混比"""
    if mu is None:
        return 0
    arr = np.asarray(mu, dtype=np.float32).reshape(-1)
    return int(zlib.crc32(np.round(arr, 5).tobytes()) & 0x7FFFFFFF)


class ReIDBackend:
    """ONNX ReID 模型封装（进程内单例）"""

    def __init__(self):
        self._lock = threading.RLock()
        self._session = None
        self._input_name = ''
        self._out_dim = 0
        self._input_w = 128
        self._input_h = 256
        self._model_path = ''
        self._num_threads = 2
        self._load_failed = False
        self._warned = False
        self._mean = None        # (1, D) float32，特征中心化用的参考均值
        self._mean_hash = 0

    # ---------- 加载 ----------

    def configure(self, model_path: str, input_width: int = 128,
                  input_height: int = 256, num_threads: int = 2,
                  mean_path: str = '') -> None:
        """设定模型参数；路径变化时下次用时重新加载

        Args:
            mean_path: 特征中心化均值文件（.npz）。留空则自动找
                       <模型同名>.mu.npz；不存在就不做中心化。
        """
        path = self._resolve(model_path)
        with self._lock:
            if path == self._model_path and self._session is not None:
                return
            self._model_path = path
            self._input_w = max(32, int(input_width))
            self._input_h = max(32, int(input_height))
            self._num_threads = max(1, int(num_threads))
            self._session = None
            self._load_failed = False
            self._warned = False
            self._mean = None
            self._mean_hash = 0
            self._mean_path = self._resolve(mean_path) if mean_path else (
                str(Path(path).with_suffix('.mu.npz')) if path else ''
            )

    @staticmethod
    def _resolve(model_path: str) -> str:
        if not model_path:
            return ''
        p = Path(model_path)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        return str(p)

    def _ensure_session(self) -> bool:
        """懒加载模型，失败只记一次警告（避免刷屏）"""
        with self._lock:
            if self._session is not None:
                return True
            if self._load_failed:
                return False
            if not self._model_path or not os.path.exists(self._model_path):
                if not self._warned:
                    logger.warning(
                        f"[CrossCamera] ReID 模型不存在: {self._model_path or '(未配置)'}，"
                        f"外观特征降级为颜色直方图"
                    )
                    self._warned = True
                self._load_failed = True
                return False
            try:
                import onnxruntime as ort
            except ImportError:
                if not self._warned:
                    logger.warning(
                        "[CrossCamera] 未安装 onnxruntime，外观特征降级为颜色直方图"
                        "（pip install onnxruntime）"
                    )
                    self._warned = True
                self._load_failed = True
                return False

            try:
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = self._num_threads
                opts.inter_op_num_threads = 1
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                session = ort.InferenceSession(
                    self._model_path, opts, providers=['CPUExecutionProvider']
                )
                inp = session.get_inputs()[0]
                shape = inp.shape
                # 模型自带固定尺寸时以模型为准，避免喂错尺寸
                if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
                    self._input_h, self._input_w = int(shape[2]), int(shape[3])
                self._session = session
                self._input_name = inp.name
                out_shape = session.get_outputs()[0].shape
                self._out_dim = int(out_shape[-1]) if isinstance(out_shape[-1], int) else 0
                self._load_mean()
                logger.info(
                    f"[CrossCamera] ReID 模型已加载: {os.path.basename(self._model_path)} "
                    f"输入 {self._input_w}x{self._input_h} 维度 {self._out_dim or '?'}"
                    f"{'（已中心化）' if self._mean is not None else ''}"
                )
                return True
            except Exception as e:
                logger.warning(f"[CrossCamera] ReID 模型加载失败（降级直方图）: {e}")
                self._load_failed = True
                return False

    # ---------- 属性 ----------

    def _load_mean(self) -> None:
        """加载特征中心化均值

        为什么必须中心化：
            ImageNet 主干学的是"整张图是什么"，输出里有一大块所有图像共享的
            共模成分（都像自然图像）。实测未中心化时，16 张无关监控画面之间
            的余弦相似度均值 0.849、最低也有 0.728，完全无法区分目标；
            减去均值后不同画面掉到 -0.037，可分间距从 0.15 拉到 1.04。
            没有这个步骤，ReID 反而不如颜色直方图好用。
        """
        path = getattr(self, '_mean_path', '')
        if not path or not os.path.exists(path):
            return
        try:
            data = np.load(path)
            mu = np.asarray(data['mu'], dtype=np.float32).reshape(1, -1)
            if self._out_dim and mu.shape[1] != self._out_dim:
                logger.warning(
                    f"[CrossCamera] 均值文件维度 {mu.shape[1]} 与模型 {self._out_dim} 不符，忽略"
                )
                return
            self._mean = mu
            self._mean_hash = int(data['hash']) if 'hash' in data else _hash_mean(mu)
        except Exception as e:
            logger.warning(f"[CrossCamera] 读取均值文件失败（不做中心化）: {e}")

    @property
    def ready(self) -> bool:
        return self._ensure_session()

    @property
    def out_dim(self) -> int:
        return self._out_dim

    @property
    def mean_hash(self) -> int:
        """中心化均值指纹；写进描述子头，保证不同均值算出的特征不会混比"""
        self._ensure_session()
        return int(self._mean_hash)

    @property
    def input_size(self):
        return self._input_w, self._input_h

    # ---------- 推理 ----------

    def _preprocess(self, crops: List[np.ndarray]) -> np.ndarray:
        """裁剪图列表 → NCHW float32 张量（RGB，ImageNet 归一化）"""
        batch = np.empty((len(crops), 3, self._input_h, self._input_w), dtype=np.float32)
        for i, crop in enumerate(crops):
            if crop is None or crop.size == 0:
                continue
            img = crop
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
            elif img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
            img = cv2.resize(img, (self._input_w, self._input_h),
                             interpolation=cv2.INTER_LINEAR)
            # 输入约定是 0~255 的 uint8；若上游给了 float 就按 0~255 理解
            if img.dtype != np.uint8:
                img = np.clip(img, 0, 255).astype(np.uint8)
            x = img.astype(np.float32) / 255.0
            x = (x - _MEAN) / _STD
            batch[i] = np.transpose(x, (2, 0, 1))
        return batch

    def embed(self, crops: List[np.ndarray]) -> Optional[np.ndarray]:
        """批量提取特征

        Args:
            crops: 目标裁剪图列表（RGB uint8）

        Returns:
            (N, D) L2 归一化 float32；不可用时返回 None
        """
        if not crops:
            return None
        if not self._ensure_session():
            return None
        try:
            batch = self._preprocess(crops)
            with self._lock:
                out = self._session.run(None, {self._input_name: batch})[0]
            feats = np.asarray(out, dtype=np.float32)
            if feats.ndim > 2:
                feats = feats.reshape(feats.shape[0], -1)
            if feats.shape[0] != len(crops):
                logger.debug(f"[CrossCamera] ReID 输出行数不符: {feats.shape[0]} != {len(crops)}")
                return None
            # 中心化：减掉所有画面共享的共模成分，把目标差异放大
            if self._mean is not None and self._mean.shape[1] == feats.shape[1]:
                feats = feats - self._mean
            norms = np.linalg.norm(feats, axis=1, keepdims=True)
            norms[norms < 1e-6] = 1.0
            return (feats / norms).astype(np.float32)
        except Exception as e:
            logger.debug(f"[CrossCamera] ReID 推理失败: {e}")
            return None

    def embed_one(self, crop: np.ndarray) -> Optional[np.ndarray]:
        feats = self.embed([crop])
        return None if feats is None else feats[0]


# 进程内单例
_BACKEND = ReIDBackend()


def get_backend() -> ReIDBackend:
    return _BACKEND


def reset_backend() -> None:
    """测试用：清掉已加载的模型"""
    global _BACKEND
    _BACKEND = ReIDBackend()
