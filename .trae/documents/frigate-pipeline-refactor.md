# 检测管道 Frigate 架构改造方案

## Context（背景与目标）

当前项目的检测管道存在严重架构缺陷，导致事件图片无法生成、检测帧不连续、RGB帧丢失：

1. **运动检测用作触发器**（`task_executor.py:1028-1098`）：无运动直接跳过检测，导致帧不连续，跟踪轨迹断裂。
2. **检测器通过队列返回 RGB 帧**（`global_detection_pool.py:538-545`）：整张 RGB 帧塞进 `mp.Queue`，造成队列阻塞和 RGB 帧丢失。
3. **异步流水线导致帧错位**（`task_executor.py:1071-1090`）：提交本帧任务却读"上一帧"结果，RGB帧与当前帧 PTS 不对应，事件图片错位。
4. **事件处理耦合在 CameraWorker 内**（`task_executor.py:1495-1687`）：采集、GIF、Zone 检测与检测循环相互阻塞。
5. **检测器进程依赖 Manager.dict()**（`global_detection_pool.py:177,253`）：性能差且易失效。

**目标**：将检测管道完全按照 frigate 架构重写，删除现有检测管道代码。同时扩展 frigate 原生能力，支持**多任务算法并行**（frigate 原生只支持单模型多相机）。

**用户已确认决策**：
- 跟踪器：完全换成 Norfair（删除 BoT-SORT）
- AI 视频流：保留，移到事件进程
- 兼容模式：不需要，直接替换
- 运动检测：用 frigate 算法适配 y_plane
- 扩展：支持多任务算法并行

## 保留边界（绝对不动）

| 模块 | 文件 | 说明 |
|------|------|------|
| 解码器 | `src/decoders/video_decoder.py` | `get_frame_index()` + `ring_buffer.read_frame_i420(block_id)` 返回 `(header, y_plane, u_plane, v_plane)` |
| 共享内存 | `src/utils/shared_memory.py` | I420 分离平面环形缓冲区 |
| 解码入口 | `task_executor.py:989-1001` | frame_index → read_frame_i420 |
| 检测器后端 | `src/detectors/backends/{rknn,tensorrt,onnx_*.py}` | `BaseDetector.detect(tensor)` |
| Web API/ONVIF/配置/日志/run.py | `src/auth`, `src/system`, `src/cameras`, `run.py` | 不变 |

## 新架构设计

### 核心理念（与当前架构的根本区别）

| 维度 | 当前架构 | 新架构（frigate） |
|------|---------|------------------|
| 运动检测作用 | 触发检测（有运动才检测） | 缩小检测区域（每帧都检测） |
| 帧连续性 | 不连续 | 连续（每帧处理） |
| 检测时机 | 异步流水线（延迟1帧） | 同步（当帧完成） |
| 检测单元 | 整帧 | region（运动框+跟踪框聚类生成的区域） |
| 结果传递 | 队列返回RGB帧 | 共享内存+Event信号 |
| 事件处理 | CameraWorker内 | 独立进程 |

### 每帧处理流程

```
1. read_frame_i420 → (header, y_plane, u_plane, v_plane)   [保留]
2. motion_detector.detect(y_plane) → motion_boxes          [每帧执行]
3. 生成 regions:
   - tracked_object_boxes（已跟踪对象框）
   - motion_boxes 中不在已跟踪区域内的独立运动框
   - 聚类合并 → region 列表 [x1,y1,x2,y2]
4. 对每个 region 同步检测:
   - create_tensor_input(y,u,v,region) → 裁剪+缩放为 (1,640,640,3)
   - remote_detector.detect(tensor) → detections（共享内存+Event）
   - region相对坐标 → 原图绝对坐标
5. reduce_detections → 合并去重
6. tracker.match_and_update(frame_name, frame_time, detections) → 更新跟踪
7. detected_objects_queue.put((cam, frame_name, time, tracked_objects,
    motion_boxes, regions, shm_block_id, shm_name)) → 事件进程
```

### 多模型支持扩展（关键扩展）

frigate 原生只有单一全局模型。本方案扩展为**按 model_type 分组的 DetectorPool 字典**：

```
DetectorPoolManager
  ├── pools: dict[model_type, DetectorPool]
  │   ├── "yolov11" → DetectorPool（3个检测器进程，共享 detection_queue）
  │   ├── "yolov8"  → DetectorPool（2个检测器进程）
  │   └── "custom"  → DetectorPool（2个检测器进程）
  │
  └── register_task(task_id, camera_ids, model_type):
        pool = self.pools.setdefault(model_type, DetectorPool(model_type, ...))
        return pool.register_cameras(camera_ids)
```

**支持两种模式**：
- **模式A（1算法多相机）**：所有任务用同一 model_type，共享一个 DetectorPool（frigate 原生行为）
- **模式B（多算法并行）**：不同任务用不同 model_type，各有独立 DetectorPool 和独立检测器进程

相同 model_type 的相机共享检测器进程池（节约资源）；不同 model_type 独立池（互不干扰）。

## 文件变更清单

### 新建文件（11个）

| 路径 | 职责 |
|------|------|
| `src/detectors/detector_pool.py` | `DetectorPoolManager`（多模型字典）+ `DetectorPool`（单模型池） |
| `src/detectors/detector_process.py` | `run_detector` 进程函数 + `ObjectDetectProcess` + `LocalObjectDetector` 适配器 |
| `src/detectors/remote_detector.py` | `RemoteObjectDetector`：CameraWorker侧同步代理（输入SHM+输出SHM+Event） |
| `src/detectors/region_utils.py` | 移植 frigate 的 region 算法 + `create_tensor_input`（适配分离Y/U/V平面） |
| `src/motion/frigate_motion.py` | `ImprovedMotionDetector`：输入 y_plane，输出 motion_boxes 列表 |
| `src/tracking/norfair_tracker.py` | `NorfairTracker`：移植 frigate 的 Norfair 跟踪器 + `match_and_update` 接口 |
| `src/tracking/tracked_object.py` | `TrackedObject` 状态机：entering→active→stationary→leaving→ended |
| `src/events/tracked_object_processor.py` | `TrackedObjectProcessor` 独立进程：消费 detected_objects_queue |
| `src/events/camera_state.py` | `CameraState`：每相机状态、帧缓存、回调注册 |
| `src/events/ai_stream_emitter.py` | AI 视频流元数据推送（从事件进程通过HTTP API推给主进程广播） |
| `src/pipeline/camera_pipeline.py` | 新版 `run_pipeline` 主循环 |

### 修改文件（3个）

| 路径 | 修改内容 |
|------|----------|
| `src/tasks/task_executor.py` | `CameraWorker.run()` 第1020行起替换为调用 `camera_pipeline.run_pipeline()`；删除 `_detect_with_pool`、`_process_tracks`、`_capture_frame`、运动触发逻辑；`MultiCameraTaskExecutor` 改为创建 `DetectorPoolManager` 和 `TrackedObjectProcessor` 进程 |
| `src/detectors/__init__.py` | 导出新的 `DetectorPoolManager`、`RemoteObjectDetector` |
| `src/motion/__init__.py` | 导出 `ImprovedMotionDetector` |

### 删除文件（5个）

| 路径 | 删除原因 |
|------|----------|
| `src/detectors/global_detection_pool.py` | 被 `detector_pool.py` + `detector_process.py` 取代 |
| `src/detectors/detection_queue.py` | `DetectionTask` dataclass 不再需要 |
| `src/detectors/preprocessor.py` | 被 `region_utils.create_tensor_input` 取代 |
| `src/motion/lightweight_motion.py` + `src/motion/motion_detector.py` | 旧运动检测实现 |
| `src/events/event_output_thread.py` | 线程模式废弃，改独立进程 |
| `src/tracking/botsort/*` | BoT-SORT 删除，换 Norfair |

### 保留并适配的文件

| 路径 | 处理 |
|------|------|
| `src/events/event_output.py` | 保留，由 `TrackedObjectProcessor` 通过回调调用 |
| `src/events/gif_generator.py` | 保留，由 `TrackedObjectProcessor` 调用 |
| `src/detection/zone_manager.py` | 保留，迁入 `CameraState` |
| `src/detectors/backends/*` + `src/detectors/factory.py` + `base.py` | 保留，由 `LocalObjectDetector` 包装 |

## 关键接口定义

### DetectorPoolManager（多模型支持）

```python
class DetectorPoolManager:
    """管理多个按 model_type 分组的检测器进程池"""
    def __init__(self):
        self.pools: dict[str, DetectorPool] = {}

    def register_task(self, task_id: str, camera_ids: list[str],
                      model_type: str, detector_config: dict,
                      num_detectors: int = 3) -> dict[str, RemoteObjectDetector]:
        """注册任务，返回 {camera_id: RemoteObjectDetector}"""
        if model_type not in self.pools:
            self.pools[model_type] = DetectorPool(model_type, detector_config, num_detectors)
        return self.pools[model_type].register_cameras(camera_ids)

    def stop(self): ...
```

### RemoteObjectDetector（同步代理）

```python
class RemoteObjectDetector:
    def detect(self, tensor_input, threshold=0.4) -> list[tuple]:
        """同步：写输入SHM → queue.put(camera_id) → wait event → 读输出SHM"""
        self.np_shm[:] = tensor_input[:]       # 输入SHM: (1,H,W,3) uint8
        self.event.clear()
        self.detection_queue.put(self.camera_id)  # 只传字符串
        if not self.event.wait(timeout=5.0): return []
        return [(labels[int(d[0])], float(d[1]), (d[2],d[3],d[4],d[5]))
                for d in self.out_np_shm if d[1] >= threshold]
```

### ImprovedMotionDetector（适配 y_plane）

```python
class ImprovedMotionDetector:
    def detect(self, y_plane: np.ndarray) -> list[tuple[int,int,int,int]]:
        """直接接收 y_plane（灰度），跳过 frigate 的 frame[0:H,0:W] 提取"""
        # 降采样 → 对比度增强 → 高斯模糊 → absdiff → threshold → dilate
        # → findContours → 过滤 contour_area → 缩放回原尺寸 → 返回 [(x1,y1,x2,y2), ...]
```

### create_tensor_input（适配分离平面）

```python
def create_tensor_input(y_plane, u_plane, v_plane, frame_shape, region, model_size=(640,640)):
    """从分离I420平面裁剪region并转RGB"""
    # 1. 拼接 (H*3//2, W) YUV420p（注意FFmpeg是YV12，交换U/V）
    # 2. yuv_region_2_rgb 裁剪
    # 3. cv2.resize 到 model_size
    # 4. np.expand_dims → (1,H,W,3) uint8
```

## 进程架构

```
主进程 (run.py / Flask)
  └── MultiCameraTaskExecutor（每个任务一个子进程）
        ├── DetectorPoolManager
        │     ├── pools["yolov11"] → DetectorPool
        │     │     ├── detection_queue (mp.Queue，放 camera_id 字符串)
        │     │     ├── ObjectDetectProcess #1, #2, #3
        │     │     └── 预分配 in-{cam}/out-{cam} SHM
        │     └── pools["yolov8"] → DetectorPool（多算法并行时）
        │
        ├── TrackedObjectProcessor (子进程，事件处理 + AI流推送)
        │     └── detected_objects_queue (mp.Queue)
        │
        ├── CameraWorker 进程 #1 (camera_pipeline)
        │     ├── VideoDecoder（保留）
        │     ├── ImprovedMotionDetector
        │     ├── RemoteObjectDetector（绑定该相机model_type的池）
        │     └── NorfairTracker
        └── CameraWorker 进程 #2 ...

通信：
  - CameraWorker → DetectorPool: detection_queue.put(camera_id) [字符串]
  - DetectorPool → CameraWorker: out_events[cam].set() + 输出SHM
  - CameraWorker → TrackedObjectProcessor: detected_objects_queue.put(8-tuple)
  - TrackedObjectProcessor → 主进程: HTTP POST /api/events/push + /api/ai-stream/push
```

## 分步实施计划

### 阶段1：基础设施（无破坏性）

**Step 1.1** `src/detectors/region_utils.py` — 移植 frigate region 算法 + `create_tensor_input` 适配分离平面
- 参考：`frigate/util/builtin.py`（get_cluster_candidates, get_cluster_region, get_min_region_size, inside_any, intersects_any, get_startup_regions）+ `frigate/util/object.py`（reduce_detections）+ `frigate/video.py:480-521`（detect/create_tensor_input）
- 验证：单元测试，给定 y/u/v 和 region=(100,100,300,300)，输出 (1,640,640,3) uint8

**Step 1.2** `src/motion/frigate_motion.py` — 移植 ImprovedMotionDetector，detect() 直接接收 y_plane
- 参考：`frigate/motion/improved_motion.py:55-207`
- 验证：用视频文件解码出 y_plane，连续调用100次，打印 motion_boxes

**Step 1.3** `src/tracking/norfair_tracker.py` + `src/tracking/tracked_object.py` — 移植 Norfair 跟踪器 + 状态机
- 参考：`frigate/track/norfair_tracker.py`（match_and_update, tracked_objects, disappeared）+ `frigate/track/tracked_object.py`（状态机）
- 验证：喂入模拟 detections 序列，观察状态转换

### 阶段2：检测器进程池重写（支持多模型）

**Step 2.1** `src/detectors/detector_process.py` — `run_detector` + `ObjectDetectProcess` + `LocalObjectDetector`
- 参考：`frigate/object_detection.py:98-154`（run_detector）+ `frigate/object_detection.py:42-95`（LocalObjectDetector）
- `LocalObjectDetector` 包装现有 `create_detector()` 后端
- 验证：单进程测试，queue.put("test") → 等event → 读out SHM

**Step 2.2** `src/detectors/remote_detector.py` — `RemoteObjectDetector` 同步代理
- 参考：`frigate/video.py` 中 RemoteObjectDetector（搜索 class RemoteObjectDetector）
- 验证：与 Step 2.1 联调

**Step 2.3** `src/detectors/detector_pool.py` — `DetectorPool` + `DetectorPoolManager`（多模型字典）
- 验证：注册2个 model_type，确认各自独立进程池；同 model_type 多相机共享池

### 阶段3：事件处理进程

**Step 3.1** `src/events/camera_state.py` — `CameraState` + 集成 ZoneManager
- 参考：`frigate/object_processing.py:236-306`（CameraState.update）

**Step 3.2** `src/events/tracked_object_processor.py` — 独立进程，消费 detected_objects_queue
- 参考：`frigate/object_processing.py:484-952`（TrackedObjectProcessor.run）
- 通过 shm_block_id + shm_name 回读 YUV 转 RGB（用于快照），复用 `src/utils/shared_memory.py`
- 调用现有 `EventOutput` + `GIFGenerator`

**Step 3.3** `src/events/ai_stream_emitter.py` — AI 视频流元数据推送
- 从事件进程通过 HTTP API 推送给主进程 WebSocket 广播
- 端口从配置读取（项目记忆：HTTP API端口必须从配置文件读取）

### 阶段4：CameraWorker 主循环重写

**Step 4.1** `src/pipeline/camera_pipeline.py` — `run_pipeline` 主循环
- 参考：`frigate/video.py:524-848`（process_frames）
- 保留第989-1001行解码入口
- 删除所有运动触发、异步流水线、RGB帧返回逻辑

**Step 4.2** 修改 `src/tasks/task_executor.py`
- `CameraWorker.__init__` 删除 detection_queue/result_queues，改为持有 remote_detector/motion_detector/tracker/detected_objects_queue
- `CameraWorker.run()` 第1020行起替换为 `camera_pipeline.run_pipeline(...)`
- 删除 `_detect_with_pool`、`_process_tracks`、`_capture_frame`、`_init_motion_detector`、`_init_event_output` 等方法
- `MultiCameraTaskExecutor.init_shared_components` 改为创建 `DetectorPoolManager` + `TrackedObjectProcessor`

### 阶段5：清理与回归测试

**Step 5.1** 删除废弃文件（见删除清单）

**Step 5.2** 更新 `__init__.py` 导出

**Step 5.3** 回归测试（见验证部分）

## 关键技术问题解决

1. **帧格式适配**：方案B——运动检测器直接接收 y_plane（灰度），跳过 frigate 的 frame[0:H,0:W] 提取
2. **检测器通信**：预分配输入SHM（1,H,W,3）+ 输出SHM（20,6）+ Event信号，不通过队列传RGB
3. **事件处理独立进程**：通过 detected_objects_queue 传8-元组（含 shm_block_id + shm_name 用于快照回读）
4. **region检测帧数据**：CameraWorker 本地完成 create_tensor_input（裁剪+缩放），检测器进程只读输入SHM
5. **保留检测器后端**：`LocalObjectDetector` 包装现有 RKNN/TensorRT/ONNX 后端
6. **多模型支持**：`DetectorPoolManager` 按 model_type 分组，同类型共享池，不同类型独立池
7. **环形缓冲区覆盖**：增大 ring_buffer_block_count（5→16），事件进程100ms内消费

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| TrackedObjectProcessor 回读 YUV 时 shm_block_id 被覆盖 | 增大 ring_buffer_block_count 到 16；事件进程100ms内消费 |
| 同步检测阻塞 CameraWorker | num_detectors 设3-4；Event 5s超时；超时丢region |
| 删除 BoT-SORT 丢失 ReID | Norfair 也支持 ReID，移植时保留；或后续补充 |
| Windows SHM 释放问题 | 沿用现有 SharedMemoryBlock 的 try/except 模式 |
| 多模型并行内存增长 | 同 model_type 共享池；不同 model_type 独立池按需创建 |

## 验证方法

### 端到端测试
1. **单相机 RTSP 流**：启动1个任务1个相机，确认：
   - motion_boxes、regions、detections、tracked_objects 都正常
   - 事件图片生成率 ≥ 95%（vs 改造前 < 50%）
   - 检测延迟 < 100ms，无帧丢失
   - 前端 AIPlayer 收到 bbox 元数据并正确画框
2. **多相机并发（1算法）**：5路相机共享同一 model_type 的 DetectorPool
   - 检测器进程池 CPU 总占用 < 200%（4核）
   - 跟踪轨迹无断裂
3. **多算法并行**：2个任务用不同 model_type
   - 各自独立 DetectorPool，互不干扰
   - 两个任务都能正常生成事件
4. **视频文件模式**：用本地视频文件测试
5. **GIF 生成**：确认 TrackedObjectProcessor 能调用 GIFGenerator
6. **性能对比**：FPS、CPU占用、检测延迟 vs 改造前

### 单元测试
- `region_utils.create_tensor_input`：给定 y/u/v 和 region，输出 shape 正确
- `ImprovedMotionDetector.detect`：连续100帧，motion_boxes 格式正确
- `NorfairTracker.match_and_update`：模拟 detections 序列，状态转换正确
- `DetectorPoolManager.register_task`：注册2个 model_type，确认独立池

### 验收标准
- 单相机 25fps：检测延迟 < 100ms，无帧丢失
- 事件图片生成率 ≥ 95%
- 跟踪轨迹无断裂
- 多算法并行互不干扰
