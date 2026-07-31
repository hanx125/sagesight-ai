# Frigate 架构改造实施计划

## Context（背景与目标）

项目正在进行 Frigate 架构改造。新架构的核心组件（`camera_pipeline.py`、`detector_pool.py`、`remote_detector.py`、`norfair_tracker.py`、`tracked_object_processor.py`、`improved_motion.py`）已全部实现，但 `task_executor.py` 仍使用旧架构（`global_detection_pool` + `BoTSORTTracker`），导致新旧架构共存、无法运行。

**目标**：完成最后的改造步骤，将 `task_executor.py` 切换到新架构，删除所有旧架构文件，使系统能正常运行。

## 当前状态

### 新架构组件（已完成，可直接使用）
- [src/pipeline/camera_pipeline.py](file:///e:/sagesight-ai/src/pipeline/camera_pipeline.py) - `run_pipeline()` 主循环
- [src/detectors/detector_pool.py](file:///e:/sagesight-ai/src/detectors/detector_pool.py) - `DetectorPoolManager` + `DetectorPool`
- [src/detectors/remote_detector.py](file:///e:/sagesight-ai/src/detectors/remote_detector.py) - `RemoteObjectDetector`
- [src/tracking/norfair_tracker.py](file:///e:/sagesight-ai/src/tracking/norfair_tracker.py) - `NorfairTracker`
- [src/events/tracked_object_processor.py](file:///e:/sagesight-ai/src/events/tracked_object_processor.py) - `TrackedObjectProcessor`（mp.Process 子类）
- [src/motion/improved_motion.py](file:///e:/sagesight-ai/src/motion/improved_motion.py) - `ImprovedMotionDetector`
- [src/detectors/region_utils.py](file:///e:/sagesight-ai/src/detectors/region_utils.py) - region 工具

### 需要改造的文件
- [src/tasks/task_executor.py](file:///e:/sagesight-ai/src/tasks/task_executor.py)（2925行，核心改造）
- [src/tracking/__init__.py](file:///e:/sagesight-ai/src/tracking/__init__.py)（当前导出 BoTSORTTracker，需改为 NorfairTracker）
- [src/detectors/__init__.py](file:///e:/sagesight-ai/src/detectors/__init__.py)（当前导出 OptimizedPreprocessor，需移除并添加新组件）

### 需要删除的文件
- `src/detectors/global_detection_pool.py` - 被 detector_pool.py 取代
- `src/detectors/detection_queue.py` - DetectionTask 不再需要
- `src/detectors/preprocessor.py` - 被 region_utils.create_tensor_input 取代
- `src/events/event_output_thread.py` - 线程模式废弃
- `src/tracking/botsort/` 整个目录 - BoT-SORT 删除
- `src/tracking/autotrack.py` - 未使用

## 关键技术约束

1. **共享内存跨进程问题**：`RemoteObjectDetector.__init__` 用 `create=False` 连接已存在共享内存。`SharedMemory` 对象不可 pickle，主进程不能创建 `RemoteObjectDetector` 再传给子进程。必须传构造参数（`camera_id, model_type, class_names, detection_queue, out_event, input_shape`），在 CameraWorker 子进程内重新创建。`mp.Queue` 和 `mp.Event` 可跨进程传递。

2. **MotionRegion 兼容性问题（必须修复）**：`ImprovedMotionDetector.detect()` 返回 `List[MotionRegion]`（dataclass，不可下标），但 `region_utils.get_cluster_candidates` 用 `boxes[b][0]` 索引访问。`camera_pipeline.py:164` 直接 `list(motion_boxes)` 会导致运行时崩溃。需在 `camera_pipeline.py` 中提取 `.bbox` 转为元组列表。

3. **VideoDecoder 初始化逻辑保留**：`CameraWorker.init_components()` 第398-444行的解码器初始化逻辑需要保留，后续的 tracker/event/gif 初始化删除。

4. **AI 流端口从配置读取**：TrackedObjectProcessor 的 `ai_stream_config.api_url` 端口必须从 `system_config.yaml` 读取（项目约束）。

## 实施步骤

### 阶段 A：修复 MotionRegion 兼容性（最小侵入）

**文件**：[src/pipeline/camera_pipeline.py](file:///e:/sagesight-ai/src/pipeline/camera_pipeline.py)

1. 第164行附近，region 生成部分：
   ```python
   # 提取 bbox 元组（MotionRegion 不可下标，需转换）
   motion_box_tuples = [
       tuple(m.bbox) if hasattr(m, 'bbox') else tuple(m)
       for m in motion_boxes
   ]
   all_boxes = motion_box_tuples + list(tracker.untracked_object_boxes)
   ```

2. 第261-273行，输出到队列时用 `motion_box_tuples` 替换 `motion_boxes`，确保跨进程 pickle 安全且下游可直接索引。

### 阶段 B：更新 __init__.py

**文件1**：[src/tracking/__init__.py](file:///e:/sagesight-ai/src/tracking/__init__.py)

将导出从 BoTSORTTracker/autotrack 改为 NorfairTracker/TrackedObject/PTZMetrics：
```python
"""跟踪模块（Norfair + PTZ 共享状态）"""
from .norfair_tracker import NorfairTracker
from .tracked_object import TrackedObject
from .ptz_metrics import PTZMetrics

__all__ = ['NorfairTracker', 'TrackedObject', 'PTZMetrics']
```

**文件2**：[src/detectors/__init__.py](file:///e:/sagesight-ai/src/detectors/__init__.py)

移除 `from .preprocessor import OptimizedPreprocessor`，添加新组件导出：
```python
from .base import BaseDetector, Detection, DetectorConfig, load_detector_config
from .detector_pool import DetectorPool, DetectorPoolManager
from .remote_detector import RemoteObjectDetector
# 移除 OptimizedPreprocessor
```
保留 `__getattr__` 延迟导入（factory + backends）。

**文件3**：[src/motion/__init__.py](file:///e:/sagesight-ai/src/motion/__init__.py)

当前已是正确的（导出 ImprovedMotionDetector + MotionRegion + LightweightMotionDetector），无需修改。

### 阶段 C：改造 task_executor.py（核心）

**文件**：[src/tasks/task_executor.py](file:///e:/sagesight-ai/src/tasks/task_executor.py)

#### C1. 改造 `_camera_worker_process_wrapper`（第44-85行）

新签名：
```python
def _camera_worker_process_wrapper(
    camera_config, task_config, detector_params,
    detection_queue, out_event, detected_objects_queue, stop_event, ptz_metrics=None,
):
```
删除 `from src.tracking.botsort import BoTSORTTracker` 和 `result_queues` 处理逻辑。直接构造 `CameraWorker` 并调 `run()`。

#### C2. 改造 `CameraWorker.__init__`（第91-272行）

新参数：`detector_params, detection_queue, out_event, detected_objects_queue, stop_event, ptz_metrics=None`

删除参数：`tracker_class, detection_queue(旧), result_queue`

删除字段（移到 TrackedObjectProcessor）：`event_output, gif_generator, track_frame_sequences, track_trajectories, active_tracks, processed_track_ids, gif_cfg, frame_cache, pending_gifs, zone_manager, object_state_manager, ai_stream_*, gif_executor` 及所有 display/event/trajectory/capture 配置字段。

保留字段：`camera_config, task_config, camera_id, rtsp_url, task_id, task_name, algorithm, decoder, decoder_cfg, motion_cfg, tracker_cfg, zones_config`

新增字段：`detector_params, detection_queue, out_event, detected_objects_queue, stop_event, ptz_metrics, remote_detector, motion_detector, tracker, frame_shape, model_size, detect_threshold, target_classes`

#### C3. 精简 `CameraWorker.init_components`（第273-536行）

- 保留：第273-444行（ZLM 检查 + VideoDecoder 创建与 start()）
- 删除：第447-528行（tracker/preprocessor/motion/event_output/gif/zone/state_manager/event_thread 初始化）
- 修改：`ring_buffer_block_count` 从 5 改为 16（避免事件进程回读时被覆盖）

#### C4. 替换 `CameraWorker.run`（第862-1330行）

替换为：调 `init_components()` → 在子进程内创建 `RemoteObjectDetector`/`ImprovedMotionDetector`/`NorfairTracker` → 调 `run_pipeline(...)`。

关键代码骨架：
```python
def run(self):
    self.running = True
    if not self.init_components():
        return

    from src.detectors.remote_detector import RemoteObjectDetector
    from src.motion.improved_motion import ImprovedMotionDetector
    from src.tracking.norfair_tracker import NorfairTracker

    self.frame_shape = (self.decoder.height, self.decoder.width)
    model_h = self.detector_params.get('input_height', 640)
    model_w = self.detector_params.get('input_width', 640)
    self.model_size = (model_h, model_w)
    self.detect_threshold = self.task_config.get('detector_config', {}).get('conf_threshold', 0.4)
    self.target_classes = self.task_config.get('target_classes', []) or None

    # 创建 RemoteObjectDetector（连接主进程已建的 SHM）
    self.remote_detector = RemoteObjectDetector(
        camera_id=self.camera_id,
        model_type=self.detector_params['model_type'],
        class_names=self.detector_params['class_names'],
        detection_queue=self.detection_queue,
        event=self.out_event,
        input_shape=(1, model_h, model_w, 3),
        stop_event=self.stop_event,
    )

    # 创建 ImprovedMotionDetector
    self.motion_detector = ImprovedMotionDetector(
        frame_shape=self.frame_shape,
        motion_height=self.motion_cfg.get('motion_height', 180),
        threshold=self.motion_cfg.get('threshold', 25),
        contour_area=self.motion_cfg.get('contour_area', 200),
        improve_contrast=self.motion_cfg.get('improve_contrast', True),
        blur_radius=self.motion_cfg.get('blur_radius', 1),
        frame_alpha=self.motion_cfg.get('frame_alpha', 0.2),
        contrast_frame_history=self.motion_cfg.get('contrast_frame_history', 50),
        lightning_threshold=self.motion_cfg.get('lightning_threshold', 0.8),
    )

    # 创建 NorfairTracker
    detect_config = {
        'min_initialized': self.tracker_cfg.get('n_init', 3),
        'max_disappeared': self.tracker_cfg.get('max_age', 30),
        'stationary_threshold': self.task_config.get('stationary_threshold', 10),
        'threshold': self.detect_threshold,
        'max_frames': self.task_config.get('max_frames', {}),
    }
    self.tracker = NorfairTracker(
        camera_id=self.camera_id,
        frame_shape=self.frame_shape,
        detect_config=detect_config,
        ptz_metrics=self.ptz_metrics,
        zones_config=self.zones_config,
    )

    # 调用 run_pipeline
    from src.pipeline.camera_pipeline import run_pipeline
    try:
        run_pipeline(
            camera_id=self.camera_id,
            task_id=str(self.task_id),
            decoder=self.decoder,
            motion_detector=self.motion_detector,
            remote_detector=self.remote_detector,
            tracker=self.tracker,
            detected_objects_queue=self.detected_objects_queue,
            frame_shape=self.frame_shape,
            model_size=self.model_size,
            detect_threshold=self.detect_threshold,
            stop_event=self.stop_event,
            ptz_metrics=self.ptz_metrics,
            target_classes=self.target_classes,
        )
    finally:
        self.cleanup()
```

#### C5. 删除 13 个废弃方法

- `_detect_with_pool` (537)
- `_init_motion_detector` (635)
- `_init_event_output` (714)
- `_init_gif_generator` (811)
- `_format_detections_summary` (668)
- `_log_with_interval` (698)
- `_display_frame` (1331)
- `_process_tracks` (1431)
- `_capture_frame` / `_crop_frame` / `_generate_track_gif` / `_process_pending_gifs` / `_finalize_pending_gif` / `_build_gif_frames_from_cache` (1673-1948)
- `_emit_track_event` (1949)
- `_get_class_name` (2052)
- `_send_detection_metadata` (2068)
- `_scale_bbox_to_original` (2158)
- `_draw_boxes_on_frame` (2184)
- `_send_ai_stream_frame` / `_start_ai_stream_thread` / `_ai_stream_worker` (2300-2464)
- `_emit_zone_event` (2465)

#### C6. 精简 `CameraWorker.cleanup` / `stop`

- `cleanup`：只保留 `decoder.stop()` 和 `remote_detector.cleanup()`
- `stop`：只设 `stop_event.set()` 和 `self.running = False`

#### C7. 改造 `MultiCameraTaskExecutor.__init__`（第2637-2665行）

- 删除：`self.tracker_class, self.detection_queue, self.result_queues, self.result_queue, self.global_pool_manager`
- 新增：`self.detector_pool_manager, self.detected_objects_queue, self.stop_event, self.tracked_object_processor, self.ptz_metrics, self.detector_params_by_camera, self.out_events`

#### C8. 改造 `init_shared_components`（第2666-2880行）

**保留**：第2669-2794行（算法库配置读取 + 标签文件加载 + 算法名称映射）

**替换**：第2796-2872行（`get_global_detection_pool_manager` + `register_task` + `BoTSORTTracker`）改为：
1. 创建 `DetectorPoolManager`
2. 调用 `register_task()` 注册所有相机
3. 调用 `start_all()` 启动检测器进程（在 `start()` 中调用）
4. 为每个相机保存 `detector_params` 和 `out_event`
5. 创建 `detected_objects_queue`（mp.Queue）和 `stop_event`（mp.Event）
6. 构建 `camera_configs`（给 TrackedObjectProcessor 用）
7. 创建 `TrackedObjectProcessor` 进程

#### C9. 改造 `start`（第2882-2925行）

```python
def start(self):
    self.running = True
    if not self.init_shared_components():
        return False

    # 1. 启动所有检测器进程（必须先于 CameraWorker）
    self.detector_pool_manager.start_all()

    # 2. 启动 TrackedObjectProcessor 进程
    self.tracked_object_processor.start()

    # 3. 为每个相机启动 CameraWorker 子进程
    for camera in self.cameras:
        cam_id = camera['camera_id']
        model_type = self.detector_params_by_camera[cam_id]['model_type']
        process = Process(
            target=_camera_worker_process_wrapper,
            args=(
                camera, self.task_config,
                self.detector_params_by_camera[cam_id],
                self.detector_pool_manager.pools[model_type].detection_queue,
                self.out_events[cam_id],
                self.detected_objects_queue,
                self.stop_event,
                self.ptz_metrics.get(cam_id) if self.ptz_metrics else None,
            ),
            name=f"CameraWorker-{cam_id}",
            daemon=True,
        )
        process.start()
        self.worker_processes[cam_id] = process
        # CPU 亲和性设置保留
    return True
```

#### C10. 改造 `stop`（第2965-3012行）

```python
def stop(self):
    self.running = False
    self.stop_event.set()  # 通知所有进程退出

    # 等待 CameraWorker 进程退出
    for cam_id, p in self.worker_processes.items():
        if p.is_alive():
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
    self.worker_processes.clear()

    # 停止 TrackedObjectProcessor
    if self.tracked_object_processor and self.tracked_object_processor.is_alive():
        self.tracked_object_processor.join(timeout=5)
        if self.tracked_object_processor.is_alive():
            self.tracked_object_processor.terminate()

    # 停止检测器进程池（清理所有 SHM）
    if self.detector_pool_manager:
        self.detector_pool_manager.stop()
```

删除：`global_pool_manager.unregister_task(...)` 调用及算法名映射（第2985-3006行）。

### 阶段 D：删除旧架构文件

在 `task_executor.py` 改造完成且引用全部解除后：
1. 删除 `src/detectors/global_detection_pool.py`
2. 删除 `src/detectors/detection_queue.py`
3. 删除 `src/detectors/preprocessor.py`
4. 删除 `src/events/event_output_thread.py`
5. 删除 `src/tracking/botsort/` 整个目录
6. 删除 `src/tracking/autotrack.py`
7. 清理 `src/tracking/__pycache__/` 中的残留 .pyc

## 验证方法

### 1. 静态验证
```powershell
# 导入冒烟测试
python -c "import src.motion; import src.tracking; import src.detectors; import src.pipeline.camera_pipeline; import src.events.tracked_object_processor; import src.tasks.task_executor; print('OK')"

# 语法检查
python -m py_compile src\tasks\task_executor.py src\pipeline\camera_pipeline.py

# 残留引用扫描
Get-ChildItem -Path e:\sagesight-ai\src -Recurse -Filter "*.py" | Select-String -Pattern "global_detection_pool|detection_queue|preprocessor\.OptimizedPreprocessor|event_output_thread|tracking\.botsort|tracking\.autotrack|BoTSORTTracker|OptimizedPreprocessor"
```

### 2. 集成测试
- **单相机 RTSP**：启动 1 任务 1 相机，确认 `run_pipeline` 性能日志输出、`detected_objects_queue` 有数据、TrackedObjectProcessor 处理事件
- **多相机共享池**：5 路相机同一 model_type，确认共享一个 DetectorPool
- **多算法并行**：2 任务不同 model_type，确认各自独立 DetectorPool
- **GIF 生成**：触发目标出现→消失，确认 GIF 文件生成
- **AI 视频流**：通过控制队列启用，确认收到 JPEG 帧
- **优雅退出**：调用 `executor.stop()`，确认所有子进程退出，无残留 SHM

### 3. 验收标准
- 单相机 25fps：检测延迟 < 100ms，无帧丢失
- 事件图片生成率 ≥ 95%
- 跟踪轨迹无断裂
- 多算法并行互不干扰

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| `ring_buffer_block_count` 默认 5 太小，事件进程回读 YUV 时被覆盖 | 在 `init_components` 中改为 16 |
| `DetectorPoolManager` 创建 SHM 后 CameraWorker 子进程连接失败 | 确保 `start_all()` 在 CameraWorker 进程启动之前调用 |
| `RemoteObjectDetector` 在主进程的实例被误用 | `register_task` 返回的 dict 仅持有引用防 GC，不调用 `detect()` |
| Windows SHM 残留 | `DetectorPool.register_cameras` 已有 `FileExistsError` 处理逻辑 |
| TrackedObjectProcessor 的 AI 流端口硬编码 | 必须从 `system_config.yaml` 读取（项目约束） |
