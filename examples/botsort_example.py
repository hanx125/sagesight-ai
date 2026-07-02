"""
BoT-SORT 集成示例

展示如何使用 BoT-SORT 跟踪器替代 ByteTrack
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import cv2
from src.tracking.botsort import BoTSORTTracker
import yaml


def main():
    """主函数"""
    
    # 加载配置
    with open('config/tracker_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 初始化 BoT-SORT 跟踪器
    tracker = BoTSORTTracker(
        track_thresh=config.get('track_thresh', 0.5),
        low_score_thresh=config.get('low_score_thresh', 0.1),
        match_thresh=config['botsort'].get('match_thresh', 0.8),
        max_age=config['botsort'].get('max_age', 30),
        max_history_length=config['botsort'].get('max_history_length', 30),
        use_reid=config['botsort'].get('use_reid', False),
        reid_model_type=config['botsort'].get('reid_model_type', 'none'),
        reid_model_path=config['botsort'].get('reid_model_path', None),
        reid_thresh=config['botsort'].get('reid_thresh', 0.3),
        device=config['botsort'].get('device', 'cpu')
    )
    
    # 模拟检测结果（示例）
    # 格式：[x1, y1, x2, y2, score, cls_id]
    detections = np.array([
        [100, 100, 200, 200, 0.8, 2],  # 车辆1
        [300, 300, 400, 400, 0.7, 2],  # 车辆2
        [500, 500, 600, 600, 0.6, 2],  # 车辆3
    ])
    
    # 更新跟踪器
    tracks = tracker.update(detections)
    
    # 输出跟踪结果
    print("跟踪结果：")
    for track in tracks:
        x1, y1, x2, y2, track_id, score, cls_id = track
        print(f"  Track ID={track_id}, bbox=[{x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f}], score={score:.2f}")
    
    print("\nBoT-SORT 跟踪器已成功集成！")
    print("配置文件：config/tracker_config.yaml")
    print("跟踪器类型：botsort")
    print("ReID 状态：", "启用" if config['botsort']['use_reid'] else "禁用")


if __name__ == '__main__':
    main()