"""
BoT-SORT 主跟踪器

三阶段匹配策略：
1. 第一阶段：高分检测 + 活跃轨迹（IoU + ReID）
2. 第二阶段：低分检测 + 丢失轨迹（IoU）
3. 第三阶段：ReID 重识别（外观特征）

参考：
- https://github.com/NirAharon/BoT-SORT
- https://github.com/ifzhang/ByteTrack
"""

import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List, Tuple, Optional
import logging
from .track import Track
from .reid_extractor import ReIDExtractor

logger = logging.getLogger('DetectorWorker')


class BoTSORTTracker:
    """BoT-SORT 主跟踪器"""
    
    def __init__(self,
                 track_thresh: float = 0.5,
                 low_score_thresh: float = 0.1,
                 match_thresh: float = 0.8,
                 max_age: int = 30,
                 n_init: int = 3,
                 max_history_length: int = 30,
                 use_reid: bool = False,
                 reid_model_type: str = 'none',
                 reid_model_path: Optional[str] = None,
                 reid_thresh: float = 0.3,
                 device: str = 'cpu',
                 half: bool = False):
        """
        初始化 BoT-SORT 跟踪器

        Args:
            track_thresh: 高分检测阈值
            low_score_thresh: 低分检测阈值
            match_thresh: IoU 匹配阈值
            max_age: 最大丢失帧数
            n_init: 新轨迹确认帧数
            max_history_length: 最大历史长度
            use_reid: 是否使用 ReID
            reid_model_type: ReID 模型类型
            reid_model_path: ReID 模型路径
            reid_thresh: ReID 匹配阈值
            device: 设备
            half: 是否使用半精度（FP16）
        """
        # 参数
        self.track_thresh = track_thresh
        self.low_score_thresh = low_score_thresh
        self.match_thresh = match_thresh
        self.max_age = max_age
        self.n_init = n_init
        self.max_history_length = max_history_length
        self.reid_thresh = reid_thresh
        
        # ReID 特征提取器
        self.use_reid = use_reid
        self.reid_extractor = None

        if use_reid:
            self.reid_extractor = ReIDExtractor(
                model_type=reid_model_type,
                model_path=reid_model_path,
                device=device,
                half=half
            )
        
        # 轨迹列表
        self.tracked_tracks: List[Track] = []   # 活跃轨迹
        self.lost_tracks: List[Track] = []      # 丢失轨迹
        
        # 轨迹 ID 计数器
        self.next_id = 1
        
        # 帧计数器
        self.frame_count = 0
    
    def update(self,
               detections: np.ndarray,
               image: Optional[np.ndarray] = None) -> np.ndarray:
        """
        更新跟踪器
        
        Args:
            detections: 检测框 [N, 6] (x1, y1, x2, y2, score, cls_id)
            image: 图像（用于 ReID 特征提取）
        
        Returns:
            跟踪结果 [M, 7] (x1, y1, x2, y2, track_id, score, cls_id)
        """
        self.frame_count += 1
        
        # 检查是否有检测结果
        if len(detections) == 0:
            # 无检测，所有轨迹标记为丢失
            for track in self.tracked_tracks:
                track.mark_missed()
            
            # 移动活跃轨迹到丢失轨迹
            self.lost_tracks.extend(self.tracked_tracks)
            self.tracked_tracks = []
            
            # 清理超时轨迹
            self.lost_tracks = [t for t in self.lost_tracks if t.consecutive_misses <= self.max_age]
            
            # 返回空结果
            return np.zeros((0, 7))
        
        # 提取 ReID 特征（如果启用）
        embeddings = None
        if self.use_reid and self.reid_extractor.is_enabled() and image is not None:
            bboxes = detections[:, 0:4]  # [x1, y1, x2, y2]
            embeddings = self.reid_extractor.extract_batch(image, bboxes)
        
        # 分离高分和低分检测
        high_score_dets = detections[detections[:, 4] >= self.track_thresh]
        low_score_dets = detections[
            (detections[:, 4] >= self.low_score_thresh) &
            (detections[:, 4] < self.track_thresh)
        ]
        
        # === 第一阶段：高分检测 + 活跃轨迹 ===
        matches1, unmatched_tracks1, unmatched_dets1 = self._match_stage1(
            self.tracked_tracks,
            high_score_dets,
            embeddings
        )
        
        # 更新匹配成功的轨迹
        for track_idx, det_idx in matches1:
            track = self.tracked_tracks[track_idx]
            det = high_score_dets[det_idx]
            
            # 转换边界框格式：[x1, y1, x2, y2] -> [cx, cy, w, h]
            x1, y1, x2, y2 = det[0:4]
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            w = x2 - x1
            h = y2 - y1
            bbox = np.array([cx, cy, w, h])
            
            # 提取外观特征
            embedding = None
            if embeddings is not None and det_idx < len(embeddings):
                embedding = embeddings[det_idx]
            
            # 更新轨迹
            track.update(bbox, det[4], embedding)
        
        # 标记未匹配的轨迹为丢失
        for track_idx in unmatched_tracks1:
            track = self.tracked_tracks[track_idx]
            logger.debug(f"轨迹 {track.track_id} 未匹配（第一阶段），标记为丢失（连续丢失 {track.consecutive_misses + 1} 帧）")
            track.mark_missed()
        
        # === 第二阶段：低分检测 + 丢失轨迹 ===
        # ✅ 关键修复：保存组合列表长度，避免并发修改导致的索引错误
        combined_tracks = [self.tracked_tracks[i] for i in unmatched_tracks1] + self.lost_tracks
        combined_len = len(combined_tracks)
        unmatched_tracks1_len = len(unmatched_tracks1)  # 保存长度，避免重复计算

        matches2, unmatched_tracks2, unmatched_dets2 = self._match_stage2(
            combined_tracks,
            low_score_dets
        )

        # 更新匹配成功的轨迹
        for track_idx, det_idx in matches2:
            # ✅ 关键修复：检查索引是否在有效范围内（严格小于 combined_len）
            if track_idx >= combined_len:
                logger.warning(f"[BotSort] 第二阶段匹配索引超出组合列表长度: track_idx={track_idx}, combined_len={combined_len}, unmatched_tracks1_len={unmatched_tracks1_len}, lost_tracks_len={len(self.lost_tracks)}")
                continue

            # 找到对应的轨迹
            if track_idx < unmatched_tracks1_len:
                # 前半部分：来自 tracked_tracks 的未匹配轨迹
                track = self.tracked_tracks[unmatched_tracks1[track_idx]]
            else:
                # 后半部分：来自 lost_tracks
                lost_idx = track_idx - unmatched_tracks1_len
                if lost_idx < len(self.lost_tracks):
                    track = self.lost_tracks[lost_idx]
                else:
                    # 索引越界，跳过（可能是并发修改）
                    logger.warning(f"[BotSort] lost_tracks 索引越界: track_idx={track_idx}, lost_idx={lost_idx}, unmatched_tracks1_len={unmatched_tracks1_len}, lost_tracks_len={len(self.lost_tracks)}")
                    continue

            det = low_score_dets[det_idx]
            
            # 转换边界框格式
            x1, y1, x2, y2 = det[0:4]
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            w = x2 - x1
            h = y2 - y1
            bbox = np.array([cx, cy, w, h])
            
            # 更新轨迹
            track.update(bbox, det[4], None)
            
            # 如果轨迹在丢失列表，移动到活跃列表
            if track in self.lost_tracks:
                self.lost_tracks.remove(track)
                self.tracked_tracks.append(track)
        
        # === 第三阶段：ReID 重识别（如果启用）===
        if self.use_reid and embeddings is not None:
            # 未匹配的高分检测
            remaining_dets = high_score_dets[unmatched_dets1]
            remaining_embeddings = [embeddings[i] for i in unmatched_dets1 if i < len(embeddings)]

            # 未匹配的丢失轨迹（正确解析组合列表索引）
            # ✅ 关键修复：使用 combined_tracks 和 combined_len，避免并发修改
            remaining_tracks = []
            for idx in unmatched_tracks2:
                # 检查索引是否在有效范围内
                if idx >= combined_len:
                    logger.warning(f"[BotSort] 第三阶段索引超出组合列表长度: idx={idx}, combined_len={combined_len}")
                    continue

                if idx < len(unmatched_tracks1):
                    # 来自 tracked_tracks 的未匹配轨迹（仍在 tracked_tracks 中）
                    remaining_tracks.append(self.tracked_tracks[unmatched_tracks1[idx]])
                else:
                    # 来自 lost_tracks
                    lost_idx = idx - len(unmatched_tracks1)
                    if lost_idx < len(self.lost_tracks):
                        remaining_tracks.append(self.lost_tracks[lost_idx])
                    else:
                        logger.warning(f"[BotSort] 第三阶段 lost_tracks 索引越界: idx={idx}, lost_idx={lost_idx}, lost_tracks_len={len(self.lost_tracks)}")
            
            # ReID 重识别
            matches3, unmatched_dets3 = self._match_stage3(
                remaining_tracks,
                remaining_dets,
                remaining_embeddings
            )
            
            # 更新匹配成功的轨迹
            for track_idx, det_idx in matches3:
                track = remaining_tracks[track_idx]
                det = remaining_dets[det_idx]
                
                # 转换边界框格式
                x1, y1, x2, y2 = det[0:4]
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                w = x2 - x1
                h = y2 - y1
                bbox = np.array([cx, cy, w, h])
                
                # 更新轨迹
                embedding = remaining_embeddings[det_idx] if det_idx < len(remaining_embeddings) else None
                track.update(bbox, det[4], embedding)
                
                # 移动到活跃列表
                if track in self.lost_tracks:
                    self.lost_tracks.remove(track)
                    self.tracked_tracks.append(track)
            
            # 未匹配的检测创建新轨迹
            for det_idx in unmatched_dets3:
                det = remaining_dets[det_idx]
                self._init_new_track(det, remaining_embeddings[det_idx] if det_idx < len(remaining_embeddings) else None)
        
        else:
            # 未匹配的高分检测创建新轨迹
            for det_idx in unmatched_dets1:
                det = high_score_dets[det_idx]
                embedding = embeddings[det_idx] if embeddings is not None and det_idx < len(embeddings) else None
                self._init_new_track(det, embedding)
        
        # 预测所有活跃轨迹
        for track in self.tracked_tracks:
            track.predict()
        
        # 清理超时轨迹（从活跃轨迹中移除）
        self.tracked_tracks = [t for t in self.tracked_tracks if t.state != Track.DELETED]

        # 清理超时轨迹（从丢失轨迹中移除）
        # ✅ 修复：清理 DELETED 状态的轨迹，避免累积过多轨迹
        prev_lost_count = len(self.lost_tracks)
        self.lost_tracks = [t for t in self.lost_tracks if t.state != Track.DELETED]
        cleaned_count = prev_lost_count - len(self.lost_tracks)

        if cleaned_count > 0:
            logger.info(f"[BotSort] 清理了 {cleaned_count} 个过期 lost_tracks，剩余 {len(self.lost_tracks)} 个")

        # ✅ 修复：如果 lost_tracks 仍然过多，按超时时间清理（不是截断）
        # 原因：截断可能删除刚丢失的轨迹，导致无法ReID匹配
        MAX_LOST_TRACKS = 30  # 最大丢失轨迹数量（降低到30，因为max_age=30帧）

        # ✅ 添加状态监控：每100帧输出一次lost_tracks状态
        if self.frame_count % 100 == 0 and len(self.lost_tracks) > 0:
            logger.info(
                f"[BotSort] 当前状态: tracked_tracks={len(self.tracked_tracks)}, "
                f"lost_tracks={len(self.lost_tracks)}, "
                f"max_consecutive_misses={max([t.consecutive_misses for t in self.lost_tracks]) if self.lost_tracks else 0}"
            )

        if len(self.lost_tracks) > MAX_LOST_TRACKS:
            # 按consecutive_misses降序排序（超时最长的在前面）
            self.lost_tracks.sort(key=lambda t: t.consecutive_misses, reverse=True)

            # 计算需要清理的数量
            remove_count = len(self.lost_tracks) - MAX_LOST_TRACKS
            removed_tracks = self.lost_tracks[:remove_count]

            # 只清理超时较长的轨迹（consecutive_misses > max_age的80%）
            # 例如：max_age=30时，清理丢失>24帧的轨迹
            threshold = int(self.max_age * 0.8)
            actually_removed = [t for t in removed_tracks if t.consecutive_misses > threshold]

            if actually_removed:
                self.lost_tracks = [t for t in self.lost_tracks if t not in actually_removed]
                logger.info(
                    f"[BotSort] lost_tracks 数量过多 ({prev_lost_count})，清理了 {len(actually_removed)} 个超时轨迹 "
                    f"(consecutive_misses > {threshold}帧)，剩余 {len(self.lost_tracks)} 个"
                )
            else:
                # 所有轨迹超时都不够长，降级为DEBUG日志（避免刷屏）
                logger.debug(
                    f"[BotSort] lost_tracks 数量过多 ({len(self.lost_tracks)})，"
                    f"但所有轨迹丢失时间 < {threshold}帧，暂不清理（等待自然超时）"
                )

        # 收集结果（过滤异常 bbox）
        results = []
        for track in self.tracked_tracks:
            if track.is_confirmed() and track.consecutive_misses == 0:  # 只返回活跃轨迹
                bbox = track.get_bbox_xyxy()
                
                # === 过滤异常 bbox（防止轨迹乱飞）===
                x1, y1, x2, y2 = bbox
                
                # 检查 bbox 是否合理（放宽过滤条件）
                if (x2 > x1 and y2 > y1 and  # 坐标顺序正确
                    x1 >= -500 and y1 >= -500 and  # 允许更多负数（边缘目标）
                    x2 < 2000 and y2 < 2000 and  # 允许更大坐标（适应不同分辨率）
                    (x2 - x1) > 2 and (y2 - y1) > 2):  # 降低最小尺寸
                    
                    results.append([
                        bbox[0], bbox[1], bbox[2], bbox[3],
                        track.track_id,
                        track.score,
                        track.class_id
                    ])
        
        return np.array(results) if len(results) > 0 else np.zeros((0, 7))
    
    def _match_stage1(self,
                      tracks: List[Track],
                      detections: np.ndarray,
                      embeddings: Optional[List[np.ndarray]]) -> Tuple[List, List, List]:
        """
        第一阶段匹配：高分检测 + 活跃轨迹（IoU + ReID）
        
        Returns:
            (matches, unmatched_tracks, unmatched_detections)
        """
        if len(tracks) == 0 or len(detections) == 0:
            return [], list(range(len(tracks))), list(range(len(detections)))
        
        # 计算距离矩阵（IoU + ReID）
        cost_matrix = self._compute_cost_matrix(tracks, detections, embeddings)
        
        # 匈牙利算法
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        
        # 筛选匹配
        matches = []
        for i, j in zip(row_ind, col_ind):
            if cost_matrix[i, j] < self.match_thresh:
                matches.append((i, j))
            else:
                # 匹配失败（IoU太低）
                track = tracks[i]
                det = detections[j]
                logger.debug(f"轨迹 {track.track_id} 匹配失败：IoU={1-cost_matrix[i, j]:.3f} < match_thresh={self.match_thresh}")
        
        # 未匹配的
        unmatched_tracks = [i for i in range(len(tracks)) if i not in [m[0] for m in matches]]
        unmatched_dets = [j for j in range(len(detections)) if j not in [m[1] for m in matches]]
        
        return matches, unmatched_tracks, unmatched_dets
    
    def _match_stage2(self,
                      tracks: List[Track],
                      detections: np.ndarray) -> Tuple[List, List, List]:
        """
        第二阶段匹配：低分检测 + 丢失轨迹（IoU）
        
        Returns:
            (matches, unmatched_tracks, unmatched_detections)
        """
        if len(tracks) == 0 or len(detections) == 0:
            return [], list(range(len(tracks))), list(range(len(detections)))
        
        # 计算 IoU 距离矩阵
        cost_matrix = self._compute_iou_distance(tracks, detections)
        
        # 匈牙利算法
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        
        # 筛选匹配
        matches = []
        for i, j in zip(row_ind, col_ind):
            if cost_matrix[i, j] < self.match_thresh:
                matches.append((i, j))
        
        # 未匹配的
        unmatched_tracks = [i for i in range(len(tracks)) if i not in [m[0] for m in matches]]
        unmatched_dets = [j for j in range(len(detections)) if j not in [m[1] for m in matches]]
        
        return matches, unmatched_tracks, unmatched_dets
    
    def _match_stage3(self,
                      tracks: List[Track],
                      detections: np.ndarray,
                      embeddings: List[np.ndarray]) -> Tuple[List, List]:
        """
        第三阶段匹配：ReID 重识别（外观特征）
        
        Returns:
            (matches, unmatched_detections)
        """
        if len(tracks) == 0 or len(detections) == 0:
            return [], list(range(len(detections)))
        
        # 计算外观距离矩阵（余弦距离）
        cost_matrix = self._compute_reid_distance(tracks, embeddings)
        
        # 匈牙利算法
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        
        # 筛选匹配
        matches = []
        for i, j in zip(row_ind, col_ind):
            if cost_matrix[i, j] < self.reid_thresh:
                matches.append((i, j))
        
        # 未匹配的检测
        unmatched_dets = [j for j in range(len(detections)) if j not in [m[1] for m in matches]]
        
        return matches, unmatched_dets
    
    def _compute_cost_matrix(self,
                             tracks: List[Track],
                             detections: np.ndarray,
                             embeddings: Optional[List[np.ndarray]]) -> np.ndarray:
        """
        计算距离矩阵（IoU + ReID）
        
        Args:
            tracks: 轨迹列表
            detections: 检测框 [x1, y1, x2, y2, ...]
            embeddings: 外观特征列表
        
        Returns:
            距离矩阵 [N_tracks, N_dets]
        """
        # IoU 距离
        iou_dist = self._compute_iou_distance(tracks, detections)
        
        # 如果启用 ReID，融合外观距离
        if self.use_reid and embeddings is not None:
            reid_dist = self._compute_reid_distance(tracks, embeddings)
            
            # 自适应权重融合
            # IoU > 0.5：以几何相似性为主
            # IoU < 0.3：提升 ReID 权重
            # 介于两者：线性插值
            
            # 计算权重
            iou = 1 - iou_dist  # IoU = 1 - IoU_distance
            
            # 权重公式：w_reid = max(0, min(1, (0.5 - iou) / 0.2))
            # 当 iou > 0.5 时，w_reid = 0（只使用 IoU）
            # 当 iou < 0.3 时，w_reid = 1（只使用 ReID）
            # 介于两者时，线性插值
            w_reid = np.maximum(0, np.minimum(1, (0.5 - iou) / 0.2))
            w_iou = 1 - w_reid
            
            # 融合距离
            cost_matrix = w_iou * iou_dist + w_reid * reid_dist
            
        else:
            cost_matrix = iou_dist
        
        return cost_matrix
    
    def _compute_iou_distance(self,
                              tracks: List[Track],
                              detections: np.ndarray) -> np.ndarray:
        """
        计算 IoU 距离矩阵
        
        Args:
            tracks: 轨迹列表
            detections: 检测框 [x1, y1, x2, y2, ...]
        
        Returns:
            IoU 距离矩阵 [N_tracks, N_dets]
        """
        n_tracks = len(tracks)
        n_dets = len(detections)
        
        cost_matrix = np.zeros((n_tracks, n_dets))
        
        for i, track in enumerate(tracks):
            track_bbox = track.get_bbox_xyxy()
            
            for j, det in enumerate(detections):
                det_bbox = det[0:4]
                
                # 计算 IoU
                iou = self._compute_iou(track_bbox, det_bbox)
                
                # IoU 距离 = 1 - IoU
                cost_matrix[i, j] = 1 - iou
        
        return cost_matrix
    
    def _compute_reid_distance(self,
                               tracks: List[Track],
                               embeddings: List[np.ndarray]) -> np.ndarray:
        """
        计算外观距离矩阵（余弦距离）
        
        Args:
            tracks: 轨迹列表
            embeddings: 外观特征列表
        
        Returns:
            外观距离矩阵 [N_tracks, N_dets]
        """
        n_tracks = len(tracks)
        n_dets = len(embeddings)
        
        cost_matrix = np.zeros((n_tracks, n_dets))
        
        for i, track in enumerate(tracks):
            track_embedding = track.get_avg_embedding()
            
            if track_embedding is None:
                # 如果轨迹没有外观特征，设置最大距离
                cost_matrix[i, :] = 1.0
                continue
            
            for j, det_embedding in enumerate(embeddings):
                if det_embedding is None:
                    cost_matrix[i, j] = 1.0
                    continue
                
                # 计算余弦距离
                cos_dist = self._compute_cosine_distance(track_embedding, det_embedding)
                
                cost_matrix[i, j] = cos_dist
        
        return cost_matrix
    
    def _compute_iou(self, bbox1: np.ndarray, bbox2: np.ndarray) -> float:
        """
        计算 IoU
        
        Args:
            bbox1: [x1, y1, x2, y2]
            bbox2: [x1, y1, x2, y2]
        
        Returns:
            IoU 值
        """
        # 计算交集
        x1 = max(bbox1[0], bbox2[0])
        y1 = max(bbox1[1], bbox2[1])
        x2 = min(bbox1[2], bbox2[2])
        y2 = min(bbox1[3], bbox2[3])
        
        if x2 <= x1 or y2 <= y1:
            return 0.0
        
        inter_area = (x2 - x1) * (y2 - y1)
        
        # 计算并集
        area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
        area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
        
        union_area = area1 + area2 - inter_area
        
        if union_area <= 0:
            return 0.0
        
        return inter_area / union_area
    
    def _compute_cosine_distance(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """
        计算余弦距离
        
        Args:
            vec1: 向量 1
            vec2: 向量 2
        
        Returns:
            余弦距离（1 - 余弦相似度）
        """
        # 余弦相似度
        dot = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 1.0
        
        cos_sim = dot / (norm1 * norm2)
        
        # 余弦距离 = 1 - 余弦相似度
        return 1 - cos_sim
    
    def _init_new_track(self,
                        det: np.ndarray,
                        embedding: Optional[np.ndarray]):
        """
        初始化新轨迹
        
        Args:
            det: 检测框 [x1, y1, x2, y2, score, cls_id]
            embedding: 外观特征
        """
        # 转换边界框格式
        x1, y1, x2, y2 = det[0:4]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        w = x2 - x1
        h = y2 - y1
        bbox = np.array([cx, cy, w, h])
        
        # 创建新轨迹
        track = Track(
            bbox=bbox,
            score=det[4],
            class_id=int(det[5]),
            track_id=self.next_id,
            embedding=embedding,
            max_history_length=self.max_history_length,
            max_age=self.max_age,
            n_init=self.n_init
        )
        
        self.next_id += 1
        
        # 添加到活跃轨迹列表
        self.tracked_tracks.append(track)