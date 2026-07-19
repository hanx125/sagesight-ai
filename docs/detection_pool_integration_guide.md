# 检测进程池集成指南

## 概述

本文档说明如何将检测进程池集成到现有的 `task_executor.py` 中。

## 修改策略

采用**渐进式集成**策略：
- 保持现有代码不变（默认行为）
- 通过配置开关启用检测进程池
- 支持回退到原有架构

---

## 修改步骤

### 1. 修改 MultiCameraTaskExecutor 类

**位置**：`src/tasks/task_executor.py`

#### 1.1 添加检测进程池初始化

```python
class MultiCameraTaskExecutor:
    def __init__(self, task_config: Dict, cameras: List[Dict]):
        """初始化多摄像机任务执行器"""
        self.task_config = task_config
        self.cameras = cameras
        self.running = False
        
        # ========================================
        # 新增：检测进程池配置
        # ========================================
        # 从任务配置读取是否启用检测进程池
        self.use_detection_pool = task_config.get('use_detection_pool', False)
        
        if self.use_detection_pool:
            # 使用检测进程池
            from src.detectors.detection_process_pool import DetectionProcessPool
            from src.detectors.base import DetectorConfig
            
            # 创建 DetectorConfig（从任务配置）
            detector_cfg = task_config.get('detector_config', {})
            detector_config = DetectorConfig(
                model_path=detector_cfg.get('model_path', 'models/yolov11n.onnx'),
                model_type=detector_cfg.get('model_type', 'yolo11n'),
                backend=detector_cfg.get('backend', 'onnx_cuda'),
                # ... 其他参数
            )
            
            # 创建检测进程池
            self.detection_pool = DetectionProcessPool(
                num_detectors=task_config.get('num_detectors', 2),
                detector_config=detector_config,
                detection_queue_maxsize=task_config.get('detection_queue_maxsize', 100)
            )
            
            # 启动检测进程池
            self.detection_pool.start()
            
            print(f"[MultiCameraTaskExecutor] 检测进程池已启动（{task_config.get('num_detectors', 2)}个进程）")
            
        else:
            # 使用原有的单线程检测器
            from src.detectors.factory import create_detector_from_config
            
            detector_cfg = task_config.get('detector_config', {})
            self.detector = create_detector_from_config(detector_cfg)
            self.detector.load_model()
            
            print("[MultiCameraTaskExecutor] 使用单线程检测器")
```

#### 1.2 修改 cleanup 方法

```python
def cleanup(self):
    """清理资源"""
    self.running = False
    
    # 停止所有摄像机工作线程
    for worker in self.workers:
        worker.running = False
    
    # ========================================
    # 新增：停止检测进程池
    # ========================================
    if self.use_detection_pool and hasattr(self, 'detection_pool'):
        self.detection_pool.stop()
        print("[MultiCameraTaskExecutor] 检测进程池已停止")
    
    # 等待所有线程退出
    for worker in self.workers:
        if worker.thread and worker.thread.is_alive():
            worker.thread.join(timeout=5)
    
    print("[MultiCameraTaskExecutor] 资源清理完成")
```

---

### 2. 修改 CameraWorker 类

#### 2.1 修改初始化方法

```python
class CameraWorker:
    def __init__(self, camera_config: Dict, task_config: Dict, detector_or_pool, tracker_class, use_pool=False):
        """
        初始化摄像机工作线程
        
        Args:
            camera_config: 摄像机配置
            task_config: 任务配置
            detector_or_pool: 检测器实例或检测进程池
            tracker_class: 跟踪器类
            use_pool: 是否使用检测进程池
        """
        self.camera_config = camera_config
        self.task_config = task_config
        self.use_pool = use_pool
        
        # 根据模式选择检测方式
        if use_pool:
            self.detection_pool = detector_or_pool
            self.detector = None
        else:
            self.detector = detector_or_pool
            self.detection_pool = None
        
        self.tracker_class = tracker_class
        
        # ... 其他初始化代码 ...
        
        # 检测事件（用于等待检测结果）
        self.detection_event = None
        self.latest_detections = []
```

#### 2.2 修改检测逻辑

**原有的单线程检测（保持不变）**：

```python
def detect_frame_traditional(self, frame):
    """传统单线程检测"""
    # 预处理
    tensor_input = self.preprocessor.preprocess(frame)
    
    # 检测
    detections = self.detector.detect(tensor_input)
    
    # 后处理
    processed_detections = self.detector.postprocess(detections)
    
    return processed_detections
```

**新增的检测进程池方式**：

```python
def detect_frame_with_pool(self, frame_index):
    """
    使用检测进程池检测
    
    Args:
        frame_index: 帧索引（从解码器获取）
        
    Returns:
        list: 检测结果列表
    """
    from src.detectors.detection_queue import DetectionTask
    
    # 创建检测任务
    task = DetectionTask(
        camera_id=self.camera_id,
        frame_seq=frame_index.sequence_num,
        shm_block_id=frame_index.shm_block_id,
        shm_name=self.decoder.shm_name,
        timestamp_ns=frame_index.timestamp_ns,
        frame_shape=(self.decoder.height, self.decoder.width),
        output_event_name=f"detection_done_{self.camera_id}"
    )
    
    # 提交任务到检测进程池（非阻塞）
    success = self.detection_pool.submit_detection(task)
    
    if not success:
        print(f"[CameraWorker-{self.camera_id}] 检测队列已满，跳过帧 {frame_index.sequence_num}")
        return []
    
    # 等待检测完成（阻塞）
    completed = self.detection_pool.wait_for_detection(self.camera_id, timeout=5.0)
    
    if not completed:
        print(f"[CameraWorker-{self.camera_id}] 检测超时")
        return []
    
    # 获取检测结果（需要额外机制）
    return self.get_latest_detections()

def get_latest_detections(self):
    """
    获取最新检测结果
    
    注意：需要额外的机制来传递检测结果
    当前版本：返回空列表（TODO: 实现结果传递）
    """
    # TODO: 实现检测结果传递机制
    # 方案1：使用 mp.Array 存储检测结果
    # 方案2：使用 Manager.dict() 共享字典
    # 方案3：使用额外的结果队列
    return self.latest_detections
```

#### 2.3 修改主循环

```python
def run(self):
    """主循环"""
    while self.running:
        # 获取帧
        frame_index = self.decoder.get_frame_index(timeout=1.0)
        
        if frame_index is None:
            continue
        
        # 检测
        if self.use_pool:
            # 使用检测进程池
            detections = self.detect_frame_with_pool(frame_index)
        else:
            # 使用单线程检测器
            frame = self.decoder.read_frame(frame_index)
            detections = self.detect_frame_traditional(frame)
        
        # 跟踪（后续处理）
        if detections:
            tracked_objects = self.tracker.update(detections)
            self.handle_tracking_results(tracked_objects)
```

---

## 3. 数据库配置

在任务配置表中添加新字段：

```sql
ALTER TABLE tasks ADD COLUMN use_detection_pool BOOLEAN DEFAULT 0;
ALTER TABLE tasks ADD COLUMN num_detectors INTEGER DEFAULT 2;
ALTER TABLE tasks ADD COLUMN detection_queue_maxsize INTEGER DEFAULT 100;
```

---

## 4. 使用示例

### 4.1 启用检测进程池

```python
task_config = {
    'id': 1,
    'task_name': 'multi-camera-detection',
    
    # 启用检测进程池
    'use_detection_pool': True,
    'num_detectors': 2,
    'detection_queue_maxsize': 100,
    
    # 检测器配置
    'detector_config': {
        'model_path': 'models/yolov11n.onnx',
        'backend': 'onnx_cuda',
        # ...
    }
}

executor = MultiCameraTaskExecutor(task_config, cameras)
executor.run()
```

### 4.2 使用传统方式（默认）

```python
task_config = {
    'id': 2,
    'task_name': 'single-thread-detection',
    
    # 不使用检测进程池（默认）
    'use_detection_pool': False,
    
    # 检测器配置
    'detector_config': {
        'model_path': 'models/yolov11n.onnx',
        'backend': 'onnx_cuda',
        # ...
    }
}

executor = MultiCameraTaskExecutor(task_config, cameras)
executor.run()
```

---

## 5. 检测结果传递机制

### 5.1 方案1：使用共享数组（推荐）

```python
# 在 DetectionProcessPool 中添加结果存储
import multiprocessing as mp

class DetectionProcessPool:
    def __init__(self, ...):
        # 共享数组：存储检测结果
        # 每个摄像机分配固定大小的数组
        self.detection_results = mp.Manager().dict()
    
    def set_detection_results(self, camera_id, results):
        """存储检测结果"""
        self.detection_results[camera_id] = results
    
    def get_detection_results(self, camera_id):
        """获取检测结果"""
        return self.detection_results.get(camera_id, [])
```

### 5.2 方案2：使用结果队列

```python
# 创建结果队列
self.result_queue = mp.Queue()

# 检测进程发送结果
self.result_queue.put((camera_id, detections))

# CameraWorker 接收结果
def get_latest_detections(self):
    """从结果队列获取检测结果"""
    try:
        while True:
            camera_id, detections = self.result_queue.get_nowait()
            if camera_id == self.camera_id:
                return detections
    except queue.Empty:
        return []
```

---

## 6. 测试验证

### 6.1 单元测试

```python
def test_detection_pool_integration():
    """测试检测进程池集成"""
    task_config = {
        'use_detection_pool': True,
        'num_detectors': 2,
        'detector_config': {...}
    }
    
    executor = MultiCameraTaskExecutor(task_config, cameras)
    
    # 测试启动
    assert executor.detection_pool.running == True
    
    # 测试检测
    # ...
    
    # 测试停止
    executor.cleanup()
    assert executor.detection_pool.running == False
```

### 6.2 性能对比

```python
# 传统方式
task_config_traditional = {'use_detection_pool': False, ...}

# 进程池方式
task_config_pool = {'use_detection_pool': True, 'num_detectors': 2, ...}

# 对比 FPS、延迟、GPU 利用率
```

---

## 7. 注意事项

1. **向后兼容**：默认 `use_detection_pool=False`，保持现有行为
2. **资源清理**：确保检测进程池正确停止
3. **错误处理**：检测失败时回退到空结果
4. **性能监控**：添加日志记录检测耗时

---

## 8. 完整修改清单

### 文件修改：
- [x] `src/utils/shared_memory.py` - 添加 `read_frame_from_shared_memory()`
- [ ] `src/tasks/task_executor.py` - 集成检测进程池
- [ ] `src/tasks/task_manager.py` - 添加配置字段
- [ ] `data/tasks.db` - 添加新字段（数据库迁移）

### 新增文件：
- [x] `src/detectors/detection_queue.py` - 检测队列
- [x] `src/detectors/detection_process_pool.py` - 检测进程池
- [x] `docs/detection_process_pool_guide.md` - 使用文档

---

## 9. 性能预期

| 指标 | 传统方式 | 检测进程池 | 提升 |
|------|---------|-----------|-----|
| GPU 利用率 | 40-60% | 80-95% | +50% |
| FPS（单摄像机） | 10-15 | 12-18 | +20% |
| FPS（多摄像机） | 5-8 | 10-15 | +80% |
| 延迟 | 100-200ms | 80-150ms | -25% |

---

## 10. 下一步计划

1. **实现检测结果传递**：选择方案并实现
2. **完善错误处理**：添加重试机制
3. **性能测试**：在实际环境测试性能提升
4. **文档更新**：更新 API 文档和用户手册

---

## 联系方式

如有问题，请参考：
- 检测进程池文档：`docs/detection_process_pool_guide.md`
- Frigate 源码：`D:\frigate\frigate\object_detection.py`