"""
端到端性能瓶颈分析脚本（完整版）
测试环节：预处理 → 推理 → 后处理 → 跟踪 → 跟踪处理 → WebSocket推送 → AI流 → 显示
模拟 CameraWorker 的完整处理循环
"""
import sys, os, time
import numpy as np
import cv2
import queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 70)
print("端到端性能瓶颈分析（完整版）")
print("=" * 70)

# 模拟 NV12 数据 (640x640)
h, w = 640, 640
y_plane = np.random.randint(0, 255, (h, w), dtype=np.uint8)
uv_plane = np.random.randint(0, 255, (h // 2, w), dtype=np.uint8)

# ===== 环节1: 预处理器整体 =====
from src.detectors.preprocessor import OptimizedPreprocessor
pp = OptimizedPreprocessor(width=640, height=640, model_input_size=640)
times = []
for _ in range(200):
    t = time.perf_counter()
    tensor = pp.preprocess_nv12(y_plane, uv_plane)
    times.append((time.perf_counter() - t) * 1000)
print(f"[1] 预处理器整体:    avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节2: BGR帧拷贝（preprocessor._last_rgb.copy()） =====
bgr_frame = pp._last_rgb.copy()
times = []
for _ in range(200):
    t = time.perf_counter()
    bgr_copy = bgr_frame.copy()
    times.append((time.perf_counter() - t) * 1000)
print(f"[2] BGR帧拷贝:       avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节3: YOLO纯推理（infer） =====
from src.detectors.yolo_detector import YOLODetector
model_path = "models/detection/yolov11n_20260701_100430.onnx"
print(f"\n加载模型: {model_path}")
det = YOLODetector({
    'model_path': model_path,
    'backend': 'onnx_tensorrt',
    'use_fp16': True,
    'target_classes': ['car'],
    'conf_threshold': 0.25,
    'iou_threshold': 0.35
})
det.initialize()

test_tensor = np.random.rand(1, 3, 640, 640).astype(np.float32)
print("预热推理(10次)...")
for _ in range(10):
    det.infer(test_tensor)

times = []
for _ in range(100):
    t = time.perf_counter()
    det.infer(test_tensor)
    times.append((time.perf_counter() - t) * 1000)
print(f"[3] 纯推理(infer):   avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节4: detect方法（推理+后处理） =====
times = []
for _ in range(100):
    t = time.perf_counter()
    result = det.detect(test_tensor)
    times.append((time.perf_counter() - t) * 1000)
print(f"[4] detect(推理+后处理): avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节5: 跟踪器 =====
from src.tracking.botsort.botsort_tracker import BoTSORTTracker
print("\n初始化BoT-SORT跟踪器...")
tracker = BoTSORTTracker(
    track_thresh=0.5,
    low_score_thresh=0.1,
    match_thresh=0.8,
    max_age=30,
    n_init=3,
    use_reid=False
)

# 模拟检测结果 [N, 6] (x1, y1, x2, y2, score, cls_id)
mock_detections = np.array([
    [100, 100, 200, 200, 0.9, 0],
    [300, 300, 400, 400, 0.85, 0],
    [500, 500, 550, 550, 0.8, 0],
], dtype=np.float32)

for _ in range(10):
    tracker.update(mock_detections)
tracker = BoTSORTTracker(track_thresh=0.5, use_reid=False)

times = []
for _ in range(100):
    frame_dets = mock_detections.copy()
    frame_dets[:, 0:4] += np.random.randint(-5, 5, size=(3, 4))
    t = time.perf_counter()
    tracks = tracker.update(frame_dets)
    times.append((time.perf_counter() - t) * 1000)
print(f"[5] 跟踪器(update):  avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节6: 检测结果格式转换（list of dict → numpy array） =====
# 模拟 CameraWorker 中将 detections 转为 det_array 的过程
mock_det_list = [
    {'bbox': [100, 100, 200, 200], 'confidence': 0.9, 'class_id': 0, 'class_name': 'car'},
    {'bbox': [300, 300, 400, 400], 'confidence': 0.85, 'class_id': 0, 'class_name': 'car'},
    {'bbox': [500, 500, 550, 550], 'confidence': 0.8, 'class_id': 0, 'class_name': 'car'},
]
times = []
for _ in range(200):
    t = time.perf_counter()
    det_array = np.array([
        [d['bbox'][0], d['bbox'][1], d['bbox'][2], d['bbox'][3],
         d['confidence'], d['class_id']]
        for d in mock_det_list
    ], dtype=np.float32)
    times.append((time.perf_counter() - t) * 1000)
print(f"[6] 检测格式转换:    avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节7: _process_tracks 模拟 =====
# 模拟轨迹更新、关键帧采集、消失检测
active_tracks = {}
track_trajectories = {}
track_frame_sequences = {}
gif_max_frames = 30

times = []
for frame_id in range(1, 101):
    frame_dets = mock_detections.copy()
    frame_dets[:, 0:4] += np.random.randint(-5, 5, size=(3, 4))
    tracks = tracker.update(frame_dets)
    
    t = time.perf_counter()
    
    # 模拟 _process_tracks 的核心逻辑
    for track in tracks:
        track_id = int(track[4])
        bbox = track[:4]
        confidence = float(track[5])
        class_id = int(track[6]) if len(track) > 6 else 0
        
        # 1. 轨迹更新
        if track_id not in track_trajectories:
            track_trajectories[track_id] = {'points': [], 'start_time': time.time(), 'class_id': class_id, 'max_confidence': 0.0}
        center_x = (bbox[0] + bbox[2]) / 2
        center_y = (bbox[1] + bbox[3]) / 2
        track_trajectories[track_id]['points'].append({'x': center_x, 'y': center_y, 'frame_id': frame_id})
        if len(track_trajectories[track_id]['points']) > 100:
            track_trajectories[track_id]['points'] = track_trajectories[track_id]['points'][-100:]
        
        # 2. 关键帧采集（frame.copy + 存储）
        if track_id not in active_tracks:
            active_tracks[track_id] = {'first_frame': frame_id, 'last_bbox': bbox, 'last_update': time.time()}
            # 模拟 track_start 时的帧拷贝
            _ = bgr_frame.copy()
        else:
            active_tracks[track_id]['last_bbox'] = bbox
            active_tracks[track_id]['last_update'] = time.time()
    
    # 3. 消失检测
    current_ids = set(int(t[4]) for t in tracks)
    for aid in list(active_tracks.keys()):
        if aid not in current_ids and time.time() - active_tracks[aid]['last_update'] > 1.2:
            del active_tracks[aid]
    
    times.append((time.perf_counter() - t) * 1000)

print(f"[7] _process_tracks: avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节8: WebSocket实时数据构建 =====
times = []
for _ in range(200):
    t = time.perf_counter()
    
    # 构建检测框数据
    detection_boxes = []
    for d in mock_det_list:
        detection_boxes.append({
            'bbox': d['bbox'],
            'class_name': d['class_name'],
            'confidence': d['confidence'],
            'class_id': d['class_id']
        })
    
    # 构建跟踪框数据
    track_boxes = []
    if len(tracks) > 0:
        for track in tracks:
            track_boxes.append({
                'bbox': track[:4].tolist(),
                'track_id': int(track[4]),
                'confidence': float(track[5]),
                'class_id': int(track[6]) if len(track) > 6 else 0
            })
    
    # 构建轨迹数据
    trajectory_data = {}
    for tid, tinfo in track_trajectories.items():
        trajectory_data[tid] = tinfo['points']
    
    times.append((time.perf_counter() - t) * 1000)

print(f"[8] WS数据构建:     avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节9: AI流帧发送（帧拷贝 + 队列入队） =====
ai_stream_queue = queue.Queue(maxsize=10)
times = []
for _ in range(200):
    t = time.perf_counter()
    
    frame_copy = bgr_frame.copy()
    queue_item = {
        'frame': frame_copy,
        'detections': mock_det_list,
        'tracks': tracks if len(tracks) > 0 else np.zeros((0, 7), dtype=np.float32),
        'camera_id': 'test'
    }
    try:
        ai_stream_queue.put_nowait(queue_item)
    except queue.Full:
        pass
    # 清空队列避免堆积
    while not ai_stream_queue.empty():
        try:
            ai_stream_queue.get_nowait()
        except queue.Empty:
            break
    
    times.append((time.perf_counter() - t) * 1000)

print(f"[9] AI流帧发送:     avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节10: 显示帧绘制（_display_frame） =====
times = []
for _ in range(200):
    t = time.perf_counter()
    
    display_frame = bgr_frame.copy()
    
    # 绘制检测框
    for d in mock_det_list:
        bbox = d['bbox']
        cv2.rectangle(display_frame,
                     (int(bbox[0]), int(bbox[1])),
                     (int(bbox[2]), int(bbox[3])),
                     (0, 255, 0), 2)
        cv2.putText(display_frame, f"{d['class_name']}: {d['confidence']:.2f}",
                   (int(bbox[0]), int(bbox[1]) - 10),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    
    # 绘制跟踪框
    if len(tracks) > 0:
        for track in tracks:
            track_id = int(track[4])
            bbox = track[:4]
            cv2.rectangle(display_frame,
                         (int(bbox[0]), int(bbox[1])),
                         (int(bbox[2]), int(bbox[3])),
                         (255, 0, 0), 2)
            cv2.putText(display_frame, f"ID: {track_id}",
                       (int(bbox[0]), int(bbox[3]) + 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
    
    # 绘制FPS信息
    cv2.putText(display_frame, "FPS: 15.0", (10, 30),
               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    
    times.append((time.perf_counter() - t) * 1000)

print(f"[10] 显示帧绘制:    avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")

# ===== 环节11: AI流后台处理（绘制+JPEG编码+base64） =====
import base64
times = []
for _ in range(100):
    t = time.perf_counter()
    
    drawn_frame = bgr_frame.copy()
    # 绘制检测框
    for d in mock_det_list:
        bbox = d['bbox']
        cv2.rectangle(drawn_frame,
                     (int(bbox[0]), int(bbox[1])),
                     (int(bbox[2]), int(bbox[3])),
                     (0, 255, 0), 2)
    # JPEG编码
    _, jpeg_buffer = cv2.imencode('.jpg', drawn_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    # base64编码
    jpeg_base64 = base64.b64encode(jpeg_buffer.tobytes()).decode('utf-8')
    
    times.append((time.perf_counter() - t) * 1000)

print(f"[11] AI流后台处理:  avg={np.mean(times):.3f}ms  min={np.min(times):.3f}ms  max={np.max(times):.3f}ms")
print(f"     (绘制+JPEG编码+base64)")

# ===== 环节12: 端到端完整流程（模拟 CameraWorker 主循环） =====
print(f"\n{'=' * 70}")
print("端到端完整流程测试（模拟 CameraWorker 主循环）")
print(f"{'=' * 70}")

# 重置
tracker = BoTSORTTracker(track_thresh=0.5, use_reid=False)
active_tracks = {}
track_trajectories = {}

all_times = {
    'preprocess': [],
    'bgr_copy': [],
    'detect': [],
    'det_format': [],
    'tracking': [],
    'process_tracks': [],
    'ws_build': [],
    'ai_stream': [],
    'display': [],
    'total': [],
}

for frame_id in range(1, 201):
    cycle_start = time.perf_counter()
    
    # 1. 预处理
    t0 = time.perf_counter()
    tensor = pp.preprocess_nv12(y_plane, uv_plane)
    all_times['preprocess'].append((time.perf_counter() - t0) * 1000)
    
    # 2. BGR帧拷贝
    t0 = time.perf_counter()
    bgr = pp._last_rgb.copy()
    all_times['bgr_copy'].append((time.perf_counter() - t0) * 1000)
    
    # 3. 检测
    t0 = time.perf_counter()
    result = det.detect(tensor)
    detections = result.get('detections', [])
    all_times['detect'].append((time.perf_counter() - t0) * 1000)
    
    # 4. 检测格式转换
    t0 = time.perf_counter()
    if len(detections) > 0:
        det_array = np.array([
            [d['bbox'][0], d['bbox'][1], d['bbox'][2], d['bbox'][3],
             d['confidence'], d.get('class_id', 0)]
            for d in detections
        ], dtype=np.float32)
    else:
        det_array = np.zeros((0, 6), dtype=np.float32)
    all_times['det_format'].append((time.perf_counter() - t0) * 1000)
    
    # 5. 跟踪
    t0 = time.perf_counter()
    frame_dets = mock_detections.copy()
    frame_dets[:, 0:4] += np.random.randint(-5, 5, size=(3, 4))
    tracks = tracker.update(frame_dets)
    all_times['tracking'].append((time.perf_counter() - t0) * 1000)
    
    # 6. _process_tracks 模拟
    t0 = time.perf_counter()
    for track in tracks:
        track_id = int(track[4])
        bbox = track[:4]
        if track_id not in active_tracks:
            active_tracks[track_id] = {'last_update': time.time()}
        else:
            active_tracks[track_id]['last_update'] = time.time()
    all_times['process_tracks'].append((time.perf_counter() - t0) * 1000)
    
    # 7. WebSocket数据构建
    t0 = time.perf_counter()
    detection_boxes = [{'bbox': d['bbox'], 'class_name': d.get('class_name', 'car')} for d in detections]
    track_boxes = [{'bbox': t[:4].tolist(), 'track_id': int(t[4])} for t in tracks] if len(tracks) > 0 else []
    all_times['ws_build'].append((time.perf_counter() - t0) * 1000)
    
    # 8. AI流帧发送
    t0 = time.perf_counter()
    frame_copy = bgr.copy()
    try:
        ai_stream_queue.put_nowait({'frame': frame_copy})
    except queue.Full:
        pass
    while not ai_stream_queue.empty():
        try: ai_stream_queue.get_nowait()
        except: break
    all_times['ai_stream'].append((time.perf_counter() - t0) * 1000)
    
    # 9. 显示帧绘制
    t0 = time.perf_counter()
    display_frame = bgr.copy()
    for d in detections:
        bbox = d['bbox']
        cv2.rectangle(display_frame, (int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3])), (0, 255, 0), 2)
    if len(tracks) > 0:
        for track in tracks:
            bbox = track[:4]
            cv2.rectangle(display_frame, (int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3])), (255, 0, 0), 2)
    all_times['display'].append((time.perf_counter() - t0) * 1000)
    
    # 总耗时
    all_times['total'].append((time.perf_counter() - cycle_start) * 1000)

# ===== 结果输出 =====
print(f"\n{'=' * 70}")
print("CameraWorker 完整循环性能分析（200帧统计）")
print(f"{'=' * 70}")

labels = {
    'preprocess':     '预处理(NV12→Tensor)',
    'bgr_copy':       'BGR帧拷贝',
    'detect':         '检测(推理+后处理)',
    'det_format':     '检测格式转换',
    'tracking':       '跟踪器更新',
    'process_tracks': '跟踪结果处理',
    'ws_build':       'WS数据构建',
    'ai_stream':      'AI流帧发送',
    'display':        '显示帧绘制',
}

avg_total = np.mean(all_times['total'])

for key, label in labels.items():
    avg = np.mean(all_times[key])
    pct = avg / avg_total * 100
    marker = " <<<" if avg > 3 else ""
    print(f"  {label:20s}: {avg:6.2f}ms ({pct:5.1f}%){marker}")

print(f"\n  {'总计':20s}: {avg_total:6.2f}ms")
print(f"  {'理论最大FPS':20s}: {1000 / avg_total:.1f}")

# 对比分析
print(f"\n{'=' * 70}")
print("瓶颈排名（按耗时降序）")
print(f"{'=' * 70}")

sorted_items = sorted(labels.items(), key=lambda x: np.mean(all_times[x[0]]), reverse=True)
for rank, (key, label) in enumerate(sorted_items, 1):
    avg = np.mean(all_times[key])
    pct = avg / avg_total * 100
    bar = "█" * max(1, int(pct / 2))
    print(f"  {rank}. {label:20s}: {avg:6.2f}ms ({pct:5.1f}%) {bar}")

# 隐藏开销分析
sum_parts = sum(np.mean(all_times[k]) for k in labels.keys())
overhead = avg_total - sum_parts
print(f"\n  各环节之和:         {sum_parts:.2f}ms")
print(f"  循环开销:           {overhead:.2f}ms")
print(f"  实际总耗时:         {avg_total:.2f}ms")

# 优化建议
print(f"\n{'=' * 70}")
print("优化建议")
print(f"{'=' * 70}")

for key, label in sorted_items:
    avg = np.mean(all_times[key])
    if avg > 8:
        print(f"\n  [高] {label}: {avg:.1f}ms")
        if key == 'preprocess':
            print("       → 归一化+CHW占主导，可考虑 cv2.dnn.blobFromImage 一步完成")
        elif key == 'detect':
            print("       → GPU推理已是最优，可考虑降低分辨率(640→416)")
        elif key == 'display':
            print("       → 可考虑禁用显示或异步绘制")
    elif avg > 3:
        print(f"\n  [中] {label}: {avg:.1f}ms")
        if key == 'bgr_copy':
            print("       → 可考虑零拷贝（直接引用而非copy）")
        elif key == 'ai_stream':
            print("       → 帧拷贝可考虑使用共享缓冲区")
