"""
基于Frigate的轻量级运动检测器

功能：判断当前帧是否有运动（用于跳过无运动帧的预处理）

特点：
- 基于帧差法（比MOG2快5倍）
- 使用avg_delta平滑噪声（避免误检测）
- 只返回True/False（是否有运动）
- 不返回具体运动区域（减少计算开销）
"""

import cv2
import numpy as np
import time


class LightweightMotionDetector:
    """
    轻量级运动检测器（基于Frigate实现）
    
    只判断"是否有运动"，不返回具体运动区域
    """
    
    def __init__(
        self,
        frame_shape=(640, 640),
        motion_height=180,  # 运动检测分辨率
        threshold=25,       # 运动阈值
        delta_alpha=0.2,    # 平均帧差更新速率
        frame_alpha=0.01,   # 背景更新速率（Frigate默认）
        improve_contrast=False,  # 是否增强对比度（默认禁用以提升性能）
    ):
        """
        初始化运动检测器
        
        Args:
            frame_shape: 帧形状 (height, width)
            motion_height: 运动检测分辨率（高度）
            threshold: 运动阈值（0-255）
            delta_alpha: 平均帧差更新速率（0-1）
            frame_alpha: 背景更新速率（0-1）
            improve_contrast: 是否增强对比度
        """
        self.frame_shape = frame_shape
        self.threshold = threshold
        self.delta_alpha = delta_alpha
        self.frame_alpha = frame_alpha
        self.improve_contrast = improve_contrast
        
        # 计算运动检测帧大小（保持宽高比）
        self.resize_factor = frame_shape[0] / motion_height
        self.motion_frame_size = (
            motion_height,
            motion_height * frame_shape[1] // frame_shape[0]
        )
        
        # 背景帧（加权平均）
        self.avg_frame = np.zeros(self.motion_frame_size, np.float32)
        
        # 平均帧差（用于平滑噪声）
        self.avg_delta = np.zeros(self.motion_frame_size, np.float32)
        
        # 帧计数器
        self.frame_counter = 0
        
        # 运动帧计数器
        self.motion_frame_count = 0
        
        # 性能统计（可选）
        self._perf_counter = 0
        self._perf_times = []
    
    def detect(self, frame):
        """
        检测是否有运动

        Args:
            frame: 输入帧（BGR或灰度格式）

        Returns:
            bool: 是否有运动
        """
        # 性能统计（开始）
        perf_start = time.time()
        step_times = {}

        # 转换为灰度图（如果是BGR）
        step_start = time.time()
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        step_times['灰度转换'] = (time.time() - step_start) * 1000

        # 降采样（减少计算量）
        step_start = time.time()
        resized_frame = cv2.resize(
            gray,
            dsize=(self.motion_frame_size[1], self.motion_frame_size[0]),
            interpolation=cv2.INTER_LINEAR
        )
        step_times['降采样'] = (time.time() - step_start) * 1000

        # 对比度增强（适应光照变化）- 默认禁用以提升性能
        step_start = time.time()
        if self.improve_contrast:
            min_value = np.percentile(resized_frame, 4)
            max_value = np.percentile(resized_frame, 96)
            # 跳过单色图像
            if min_value < max_value:
                resized_frame = np.clip(resized_frame, min_value, max_value)
                resized_frame = (
                    ((resized_frame - min_value) / (max_value - min_value)) * 255
                ).astype(np.uint8)
        step_times['对比度增强'] = (time.time() - step_start) * 1000

        # 帧计数
        self.frame_counter += 1

        # 跳过前30帧（预热）
        if self.frame_counter < 30:
            cv2.accumulateWeighted(resized_frame, self.avg_frame, 0.2)
            return False

        # 计算帧差（当前帧 - 背景帧）
        step_start = time.time()
        frameDelta = cv2.absdiff(resized_frame, cv2.convertScaleAbs(self.avg_frame))
        step_times['帧差计算'] = (time.time() - step_start) * 1000

        # 计算平均帧差（平滑噪声，避免误检测）
        step_start = time.time()
        cv2.accumulateWeighted(frameDelta, self.avg_delta, self.delta_alpha)
        step_times['累积加权'] = (time.time() - step_start) * 1000

        # 计算当前帧的阈值图像
        step_start = time.time()
        current_thresh = cv2.threshold(
            frameDelta,
            self.threshold,
            255,
            cv2.THRESH_BINARY
        )[1]
        step_times['阈值化'] = (time.time() - step_start) * 1000

        # 黑出avg_delta中当前帧没有运动的区域
        # 这可以防止之前帧的delta被包含进来
        step_start = time.time()
        avg_delta_image = cv2.convertScaleAbs(self.avg_delta)
        avg_delta_image = cv2.bitwise_and(avg_delta_image, current_thresh)
        step_times['位运算'] = (time.time() - step_start) * 1000

        # 然后查找avg_delta中高于阈值的区域（但只在当前帧有delta的区域）
        step_start = time.time()
        thresh = cv2.threshold(
            avg_delta_image,
            self.threshold,
            255,
            cv2.THRESH_BINARY
        )[1]
        step_times['二次阈值化'] = (time.time() - step_start) * 1000

        # 膨胀（填充空洞）
        step_start = time.time()
        thresh_dilated = cv2.dilate(thresh, None, iterations=2)
        step_times['膨胀'] = (time.time() - step_start) * 1000

        # 查找轮廓
        step_start = time.time()
        contours = cv2.findContours(
            thresh_dilated,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )
        contours = contours[0] if len(contours) == 2 else contours[1]
        step_times['轮廓查找'] = (time.time() - step_start) * 1000

        # 判断是否有运动（快速检查）
        step_start = time.time()
        has_motion = False
        min_contour_area = 100  # 最小轮廓面积（过滤噪声）

        for c in contours:
            contour_area = cv2.contourArea(c)
            if contour_area > min_contour_area:
                has_motion = True
                break  # 找到第一个有效运动区域就退出

        # 更新背景（自适应更新）
        if has_motion:
            self.motion_frame_count += 1
            if self.motion_frame_count >= 10:
                # 只有运动持续一段时间才更新背景
                cv2.accumulateWeighted(
                    resized_frame,
                    self.avg_frame,
                    self.frame_alpha
                )
        else:
            # 无运动时，持续更新背景
            cv2.accumulateWeighted(
                resized_frame,
                self.avg_frame,
                self.frame_alpha
            )
            self.motion_frame_count = 0
        step_times['背景更新'] = (time.time() - step_start) * 1000

        # 性能统计（结束）
        perf_time = (time.time() - perf_start) * 1000
        self._perf_times.append(perf_time)
        self._perf_counter += 1

        # 每100帧输出一次性能统计（带详细步骤）
        if self._perf_counter % 100 == 0:
            import logging
            logger = logging.getLogger('LightweightMotionDetector')
            avg_time = sum(self._perf_times[-100:]) / min(100, len(self._perf_times))
            logger.info(f"[LightweightMotionDetector] 平均耗时: {avg_time:.2f}ms (最近100帧)")
            # ✅ 注释：详细步骤日志已删除（避免刷屏）
            # if self._perf_counter == 100:
            #     detail_str = ', '.join([f"{k}:{v:.2f}ms" for k, v in step_times.items()])
            #     logger.info(f"[LightweightMotionDetector] 详细步骤: {detail_str}")

        return has_motion
    
    def reset(self):
        """重置背景建模器"""
        self.avg_frame = np.zeros(self.motion_frame_size, np.float32)
        self.avg_delta = np.zeros(self.motion_frame_size, np.float32)
        self.frame_counter = 0
        self.motion_frame_count = 0