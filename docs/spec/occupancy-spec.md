# 占道堆积/异常占用检测 — 实施规格（Spec）

> 依据：《占道堆积检测详细设计.md》v1.1（已批准）
> 日期：2026-10-13

## 0. 已核实的架构事实（调研结论）

- 进程链：Flask 主进程(run.py) → TaskProcessManager.start_task → Process(_run_executor_process) → MultiCameraExecutor（任务进程）→ 每相机 Process(_camera_worker_process_wrapper) → CameraWorker.run() → pipeline_func
- 队列消息：9 元组 `(camera_id, frame_time, tracked_objects_dict, motion_boxes, regions, shm_block_id, shm_name, detection_results, frame_seq)`；`shm_name = f"sagesight_{task_id}_{camera_id}_block"`
- 事件出口：`EventOutput.emit(event_type, data, image, lifecycle=False, defer_db=False)` → 立即落库 + MQTT + WebSocket；`data.current_zones` → zone_name 列
- 事件更新：`TaskManager.update_event(event_id, end_time=..., status='final', metadata=...)` 支持部分更新
- Socket.IO emit 仅存在于 Flask 主进程（API 层），子进程无先例 → **不做子进程直推**
- 跨进程共享现状：AI 流已从 Manager dict 迁移到**文件标记**（`data/ai_stream_flags/`），run.py 注释明确 Manager 顶层创建的坑 → 基线控制采用同模式

## 1. 关键决策（对设计文档的实现级修正）

| 设计文档原文 | 实现决策 | 理由 |
|---|---|---|
| 共享控制字典 control_flags | **文件标记** `data/baseline_requests/{task_id}_{channel_id}.flag`（API 写、管线检测到即删除并执行） | 与 AI 流文件标记一致；避免 Manager 跨进程共享的复杂度 |
| Socket.IO emit `baseline_captured`/`baseline_invalid` | 管线进程每 5s 回写 `data/baseline_status/{channel_id}.json`；前端打开区域弹窗时轮询 `GET baseline-status`（3s）刷新徽标 | 子进程无法 emit Socket.IO；轮询满足需求且零新通道 |
| recovery 通知"同事件 ID 追加推送" | update_event 置 `status='final'` + `end_time`；`occupancy_recovery_notify` 开启时用 mqtt_outputter.send() 以原 event_id 发一条 `data.status='recovery'` 消息 | 复用现有 MQTT 出口，不新建事件记录 |

## 2. 新增文件

### 2.1 `src/occupancy/__init__.py`
导出 `BaselineManager, DiffAnalyzer, ZoneOccupancyState, ChannelOccupancyMonitor`，定义 `OCCUPANCY_DETECTION_TYPES = ('pile_up', 'abnormal_occupancy')`。

### 2.2 `src/occupancy/baseline_manager.py`
```
BASELINE_DIR='data/baselines'; REQUEST_DIR='data/baseline_requests'; STATUS_DIR='data/baseline_status'
BASELINE_CAPTURE_FRAMES=5
class BaselineManager:            # 全静态方法
    exists(channel_id) -> bool
    load(channel_id) -> np.ndarray|None     # cv2.imread grayscale
    meta(channel_id) -> dict|None           # {captured_at, frame_shape, task_id, camera_id}
    save(channel_id, baseline_y, meta)
    capture_median(y_planes: list) -> np.ndarray   # np.median(axis=0).astype(uint8)
    request_capture(task_id, channel_id)           # 写 flag 文件（主进程 API 调用）
    consume_capture_request(task_id, channel_id) -> bool  # 存在即删除返回 True（管线调用）
    save_status(channel_id, status: dict)          # 管线 5s 回写
    load_status(channel_id) -> dict|None           # baseline-status API 读取
```

### 2.3 `src/occupancy/diff_analyzer.py`
```
DIFF_THRESHOLD=25
@dataclass DiffResult: area_ratio: float; bbox: list|None   # bbox 全帧坐标 [x1,y1,x2,y2]
class DiffAnalyzer(diff_threshold=25):
    analyze(current_y, baseline_y, polygon_px) -> DiffResult
    # 1. 多边形外接矩形+4px 边距裁剪 ROI
    # 2. 亮度归一化: (cur-mean_c)*(std_b/max(std_c,1))+mean_b, clip 0-255
    # 3. absdiff > threshold → mask
    # 4. mask &= fillPoly(多边形 ROI 掩膜)
    # 5. 开运算3x3 → 闭运算5x5
    # 6. ratio = mask像素/polygon像素; bbox=最大连通域 boundingRect
```

### 2.4 `src/occupancy/occupancy_state.py`
```
GLOBAL_CHANGE_RATIO=0.60; GRACE_SECONDS=5; CLEAR_CONFIRM_SECONDS=30
DEFAULT_MIN_AREA_RATIO=0.02
DEFAULT_DWELL={'pile_up':300,'abnormal_occupancy':60}
ZONE_ENABLE_KEY={'pile_up':'enable_pileup','abnormal_occupancy':'enable_occupancy'}
ZONE_DWELL_KEY={'pile_up':'pileup_dwell_seconds','abnormal_occupancy':'occupancy_dwell_seconds'}

class ZoneOccupancyState(zone_name, dwell_seconds, min_area_ratio, grace_seconds, clear_confirm_seconds):
    update(ratio, now) -> 'alert'|'recovery'|None
      # UNCALIBRATED/MONITORING: ratio>=min → 累计（grace 内短暂跌落不重置）；达 dwell → alert 转 ALERTED
      # ALERTED: ratio<min 持续 clear_confirm → recovery 转 MONITORING；期间不重复 alert
    invalidate(now) -> 'global_change'|None   # 仅从 MONITORING 迁移 INVALID 时返回一次
    recalibrate()                             # 拍摄新基线后 INVALID/UNCALIBRATED → MONITORING
    @property state;  snapshot() -> dict      # {name,state,last_alert_at}

class ChannelOccupancyMonitor(channel_id, detection_type, zones_config, frame_shape):
    # 筛选 ZONE_ENABLE_KEY 开启的区域；无启用区域 → enabled_zones 为空
    # baseline_y 注入式加载（load_baseline/load_success 标志）
    process_frame(y_plane, now) -> list[dict] # 遗留物迁移消息
      # 无基线→[]；逐区域 analyze；max_ratio>=0.60 → 全区域 invalidate（去重只发一次 global_change）
      # alert/recovery 消息: {event, zone_name, area_ratio, dwell_seconds, diff_bbox, timestamp}
    status_summary() -> dict                  # {state, zones:[snapshot...], captured_at}
    handle_baseline_captured(baseline_y, now) # 全区域 recalibrate + 注入基线
```

### 2.5 `src/pipeline/occupancy_pipeline.py`
```
OCCUPANCY_INTERVAL_FRAMES=6; STATUS_WRITE_INTERVAL=5.0; CAPTURE_FRAME_INTERVAL=0.5
run_occupancy_pipeline(camera_id, task_id, detection_type, decoder, detected_objects_queue,
                       frame_shape, zones_config, stop_event, channel_id=None)
```
- 主循环仿 camera_pipeline：`decoder.get_frame_index()` → `read_frame_i420`；停滞 30s 节流打点；`_malloc_trim` 复用（本文件内实现同函数）
- 每帧检查 `consume_capture_request` → 连取 5 帧(0.5s 间隔) Y 平面中值 → save → monitor.handle_baseline_captured
- `frame_count % 6 == 0` → monitor.process_frame → 消息补全 `{__type__:'occupancy', camera_id, detection_type, task_id, shm_block_id, shm_name, sequence_num}` → queue.put(timeout=0.05，满则丢弃)
- 每 5s → `BaselineManager.save_status(channel_id, monitor.status_summary())`

## 3. 集成修改（现有文件）

### 3.1 `src/pipeline/pipeline_factory.py`
- `VALID_DETECTION_TYPES` += `('pile_up','abnormal_occupancy')`；`get_pipeline` 两类型延迟导入返回 `run_occupancy_pipeline`

### 3.2 `src/tasks/task_executor.py`
**CameraWorker.run()**（L340 起）：`detection_type in OCCUPANCY_DETECTION_TYPES` 时走专用分支：
- `init_components()`（解码器）→ `decoder.start()` → 调 `run_occupancy_pipeline(camera_id, task_id, detection_type, decoder, detected_objects_queue, frame_shape, zones_config, stop_event, channel_id=camera_config.get('channel_id'))`
- **跳过** RemoteObjectDetector / ImprovedMotionDetector / NorfairTracker 创建与 get_pipeline 主路径

**MultiCameraExecutor.init_shared_components()**（步骤5，L855）：
- occupancy 时**跳过检测器池注册**（DetectorPoolManager 不创建、不注册、`detector_pool_manager=None`）；`detector_params_by_camera[cam_id]` 填占位 `{'model_type':'','class_names':[],'input_height':640,'input_width':640}`（CameraWorker occupancy 分支不消费）
- 步骤7 camera_configs 照常（event_type 已随任务配置注入）

**MultiCameraExecutor.start()**（L1163）：occupancy 时跳过步骤1（`detector_pool_manager.start_all()` 与 wait_ready）；其余照常

### 3.3 `src/events/tracked_object_processor.py`
**_process_detection_data()**（L673）入口最前：
```python
if isinstance(data, dict):
    self._on_occupancy_message(data); return
```
**新增 `_on_occupancy_message(msg)`**：
- `alert`：`_read_frame_from_shm(camera_id, {shm_block_id, shm_name, frame_time, frame_seq})` 取快照 → `event_output.emit(event_type=msg['detection_type'], data={class_name: msg['detection_type'], confidence: area_ratio, bbox: diff_bbox or [0,0,0,0], status:'active', start_time, current_zones:[zone_name]}, image=frame, frame_id=sequence_num, lifecycle=False, defer_db=False)` → event_id 存 `self.occupancy_events[camera_id][zone_name]`；调 `_trigger_alarm_voice(camera_id)`（语音映射无 pile_up 项时静默跳过，天然兼容）
- `recovery`：pop event_id → `TaskManager().update_event(event_id, end_time=now, status='final')`；`event_config.occupancy_recovery_notify!=False` 且 mqtt_outputter 存在 → 构造 `{event_id, type: detection_type, camera_id, timestamp, data:{status:'recovery', zone_name}}` → `mqtt_outputter.send()`
- `global_change`：仅日志（前端经 baseline-status 轮询感知 INVALID）

### 3.4 `src/tasks/task_api.py`
**`_validate_zones_config()`**（L898）追加可选字段校验：
- `enable_pileup`/`enable_occupancy`: bool
- `pileup_dwell_seconds`/`occupancy_dwell_seconds`: int（非 bool）10~86400
- `min_area_ratio`: 数值 0.001~0.5

**新增 2 个路由**：
- `POST /api/tasks/<task_id>/capture-baseline`：任务运行中（process_manager.task_processes 含 task_id）且 detection_type 为 occupancy，否则 400；取任务通道列表逐个 `BaselineManager.request_capture`；返回 `{message, channels:[...]}`
- `GET /api/tasks/<task_id>/baseline-status`：逐通道返回 `{channel_id, baseline_captured_at, state, zones}`（数据=load_status 优先，缺省时按基线文件存在性给 `monitoring`/`uncalibrated`）

### 3.5 前端 `templates/tasks.html`
- 检测类型下拉（L130）+2 选项：`pile_up` 物品堆积检测 / `abnormal_occupancy` 异常占用检测
- `onDetectionTypeChange`：occupancy 类型时事件类型自动锁定同名值、隐藏检测模型无关配置（与 people_counting 策略一致处复用现有逻辑）
- 区域弹窗：顶部「拍摄基线」按钮 + 状态徽标（未校准/监测中/已告警/已失效 + 拍摄时间），弹窗打开时 3s 轮询 baseline-status；每区域「遗留物检测」参数区：堆积开关+时长(s)、占用开关+时长(s)、最小面积占比
- `events.html`（L23）事件类型筛选 +2 选项

## 4. 验证

1. `python -m py_compile` 全部改动 py 文件
2. `pytest tests/test_occupancy_diff.py tests/test_occupancy_state.py`（纯 numpy/cv2 合成帧单测，不依赖摄像头）
3. `node --check` static/js 改动文件（如拆分 js 则对应校验）
