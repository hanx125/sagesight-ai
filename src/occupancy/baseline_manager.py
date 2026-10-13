"""
基线管理

基线为通道级数据：同一相机同一画面共用一张基线，多区域各自按多边形取 ROI。
落盘路径:
- data/baselines/{channel_id}.jpg          基线灰度图
- data/baselines/{channel_id}.json         元数据 {captured_at, frame_shape, task_id, camera_id}

跨进程控制通道（文件标记，与 AI 流标记同一模式）:
- data/baseline_requests/{task_id}_{channel_id}.flag   拍摄请求（API 写，管线消费）
- data/baseline_status/{channel_id}.json               运行时状态摘要（管线 5s 回写，API 读）
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 基线采集帧数：连取多帧逐像素取中值，消除路过的行人与瞬时噪声
BASELINE_CAPTURE_FRAMES = 5

# 项目根目录（src/occupancy/ 上两级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASELINE_DIR = os.path.join(PROJECT_ROOT, 'data', 'baselines')
REQUEST_DIR = os.path.join(PROJECT_ROOT, 'data', 'baseline_requests')
STATUS_DIR = os.path.join(PROJECT_ROOT, 'data', 'baseline_status')


class BaselineManager:
    """基线存取与拍摄请求/状态回写（全静态方法，无实例状态）"""

    # ========================================
    # 基线文件
    # ========================================

    @staticmethod
    def _image_path(channel_id) -> str:
        return os.path.join(BASELINE_DIR, f'{channel_id}.jpg')

    @staticmethod
    def _meta_path(channel_id) -> str:
        return os.path.join(BASELINE_DIR, f'{channel_id}.json')

    @staticmethod
    def exists(channel_id) -> bool:
        """基线文件是否已存在"""
        return os.path.isfile(BaselineManager._image_path(channel_id))

    @staticmethod
    def load(channel_id) -> Optional[np.ndarray]:
        """加载基线 Y 平面 (H, W) uint8，不存在或读取失败返回 None"""
        try:
            img = cv2.imread(BaselineManager._image_path(channel_id), cv2.IMREAD_GRAYSCALE)
            if img is None:
                return None
            return img
        except Exception as e:
            logger.warning(f"基线读取失败 channel={channel_id}: {e}")
            return None

    @staticmethod
    def meta(channel_id) -> Optional[dict]:
        """读取基线元数据，不存在返回 None"""
        try:
            path = BaselineManager._meta_path(channel_id)
            if not os.path.isfile(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"基线元数据读取失败 channel={channel_id}: {e}")
            return None

    @staticmethod
    def save(channel_id, baseline_y: np.ndarray, meta: dict):
        """保存基线灰度图与元数据"""
        os.makedirs(BASELINE_DIR, exist_ok=True)
        cv2.imwrite(BaselineManager._image_path(channel_id), baseline_y)
        with open(BaselineManager._meta_path(channel_id), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    # ========================================
    # 基线采集
    # ========================================

    @staticmethod
    def capture_median(y_planes: List[np.ndarray]) -> np.ndarray:
        """多帧 Y 平面逐像素取中值，返回 (H, W) uint8"""
        stack = np.stack(y_planes, axis=0)
        return np.median(stack, axis=0).astype(np.uint8)

    # ========================================
    # 拍摄请求标记（API 写 → 管线消费）
    # ========================================

    @staticmethod
    def _request_path(task_id, channel_id) -> str:
        return os.path.join(REQUEST_DIR, f'{task_id}_{channel_id}.flag')

    @staticmethod
    def request_capture(task_id, channel_id):
        """写入拍摄请求标记（主进程 API 调用）"""
        os.makedirs(REQUEST_DIR, exist_ok=True)
        path = BaselineManager._request_path(task_id, channel_id)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(datetime.now().isoformat())

    @staticmethod
    def consume_capture_request(task_id, channel_id) -> bool:
        """检测并消费拍摄请求标记（管线进程调用，存在即删除）"""
        path = BaselineManager._request_path(task_id, channel_id)
        if not os.path.isfile(path):
            return False
        try:
            os.remove(path)
        except OSError:
            pass
        return True

    # ========================================
    # 运行时状态回写（管线 5s 写 → API 读）
    # ========================================

    @staticmethod
    def save_status(channel_id, status: dict):
        """回写通道运行时状态摘要（含各区域状态机状态）"""
        try:
            os.makedirs(STATUS_DIR, exist_ok=True)
            path = os.path.join(STATUS_DIR, f'{channel_id}.json')
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(status, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:
            logger.debug(f"基线状态回写失败 channel={channel_id}: {e}")

    @staticmethod
    def load_status(channel_id) -> Optional[dict]:
        """读取通道运行时状态摘要，不存在返回 None"""
        try:
            path = os.path.join(STATUS_DIR, f'{channel_id}.json')
            if not os.path.isfile(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"基线状态读取失败 channel={channel_id}: {e}")
            return None
