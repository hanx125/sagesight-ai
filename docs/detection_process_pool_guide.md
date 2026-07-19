# 检测进程池使用指南

## 📋 概述

检测进程池是一个多进程架构，用于提高目标检测的性能和吞吐量。参考 Frigate 的 `ObjectDetectProcess` 设计，实现了检测进程池化。

### 核心优势

1. **并行检测**：多个检测进程并行处理，充分利用 GPU
2. **解耦设计**：摄像机进程和检测进程分离，互不阻塞
3. **高可用性**：单个检测进程崩溃不影响其他进程
4. **资源池化**：多摄像机共享检测进程池，提高资源利用率

---

## 🏗️ 架构设计

### 进程架构

```
主进程（Flask Web 服务）
    ↓
摄像机进程-1 ──┐
摄像机进程-2 ──┼→ DetectionQueue → 检测进程池
摄像机进程-3 ──┘                      ├─ 检测进程-1
                                     ├─ 检测进程-2
                                     └─ 检测进程-N
```

### 数据流

```
摄像机进程（生产者）
    ↓
1. 解码帧 → 共享内存
2. 提交检测任务到 DetectionQueue
    ↓
检测进程池（消费者）
    ↓
3. 从队列获取任务
4. 从共享内存读取帧（零拷贝）
5. 执行检测
6. 触发 mp.Event（通知完成）
    ↓
摄像机进程
    ↓
7. 等待检测完成
8. 获取检测结果
9. 执行跟踪和事件处理
```

---

## 🚀 快速开始

### 1. 基础用法

```python
from src.detectors.detection_process_pool import DetectionProcessPool
from src.detectors.detection_queue import DetectionTask

# 1. 创建检测进程池
detector_config = {
    'algorithm': 'yolov11',
    'model_path': 'models/yolov11.onnx',
    'confidence_threshold': 0.5
}

pool = DetectionProcessPool(
    num_detectors=2,  # 2个检测进程
    detector_config=detector_config,
    detection_queue_maxsize=100
)

# 2. 启动进程池
pool.start()

# 3. 注册摄像机
pool.register_camera('cam-001', 'shm_cam_001')

# 4. 提交检测任务
task = DetectionTask(
    camera_id='cam-001',
    frame_seq=1,
    shm_block_id=0,
    shm_name='shm_cam_001',
    timestamp_ns=int(time.time() * 1e9),
    frame_shape=(640, 640),
    output_event_name='detection_done_cam_001'
)

success = pool.submit_detection(task)

# 5. 等待检测完成
if success:
    completed = pool.wait_for_detection('cam-001', timeout=5.0)
    if completed:
        print("检测完成")
    else:
        print("检测超时")

# 6. 停止进程池
pool.stop()
```

### 2. 集成到 CameraWorker

参考 `src/tasks/task_executor_with_pool.py` 文件，展示了如何将检测进程池集成到现有的摄像机工作线程中。

关键修改：

```python
class CameraWorkerWithPool:
    def __init__(self, ..., detection_pool: DetectionProcessPool):
        self.detection_pool = detection_pool  # 共享检测进程池
        self.detection_event = None  # 检测完成事件

    def run(self):
        while running:
            # 1. 获取帧
            frame_index = self.decoder.get_frame_index()

            # 2. 提交检测任务（非阻塞）
            task = DetectionTask(...)
            self.detection_pool.submit_detection(task)

            # 3. 等待检测完成（阻塞）
            self.detection_event.wait(timeout=5.0)

            # 4. 获取检测结果
            detections = self.get_latest_detections()

            # 5. 执行跟踪
            tracked_objects = self.tracker.update(detections)
```

---

## 📊 性能优化建议

### 1. 检测进程数量

```python
# 单 GPU 环境
num_detectors = 2  # 推荐 1-2 个进程

# 多 GPU 环境
num_detectors = num_gpus * 2  # 每 GPU 2 个进程

# 高负载环境
num_detectors = 3  # 根据队列积压情况调整
```

### 2. 队列大小

```python
# 低延迟要求
detection_queue_maxsize = 50

# 高吞吐量要求
detection_queue_maxsize = 200

# 平衡方案
detection_queue_maxsize = 100  # 默认推荐
```

### 3. 等待超时

```python
# 快速响应场景
timeout = 2.0  # 秒

# 高精度场景
timeout = 5.0  # 秒（默认）

# 网络不稳定场景
timeout = 10.0  # 秒
```

---

## 🔧 高级功能

### 1. 动态调整检测器数量

```python
# 查看当前状态
stats = pool.get_stats()
print(f"活跃进程: {stats['active_processes']}")
print(f"待处理任务: {stats['pending_tasks']}")

# 根据负载调整（需要重启）
if stats['pending_tasks'] > 50:
    # 增加检测进程
    pool.stop()
    pool = DetectionProcessPool(num_detectors=4, ...)
    pool.start()
```

### 2. 监控队列状态

```python
# 定期打印统计
pool.print_stats()

# 输出：
# [DetectionQueue] 统计信息:
#   已提交任务: 1234
#   已处理任务: 1200
#   当前队列大小: 34
#   最大容量: 100
#   待处理任务: 34
```

### 3. 错误处理

```python
# 提交任务失败（队列已满）
success = pool.submit_detection(task)
if not success:
    # 策略 1：丢弃任务
    print("队列已满，丢弃任务")

    # 策略 2：等待并重试
    time.sleep(0.1)
    success = pool.submit_detection(task)

# 检测超时处理
completed = pool.wait_for_detection('cam-001', timeout=5.0)
if not completed:
    # 策略 1：跳过该帧
    continue

    # 策略 2：使用上一帧的检测结果
    detections = self.last_detections
```

---

## 🧪 测试验证

### 运行测试脚本

```bash
python tests/test_detection_process_pool.py
```

### 测试内容

1. **DetectionQueue 测试**：队列基础功能
2. **进程池基础测试**：启动、停止、注册
3. **任务提交测试**：单任务提交和等待
4. **多任务测试**：10个任务并行处理

### 预期输出

```
✓ 测试 1 通过（队列功能）
✓ 测试 2 通过（进程池基础）
✓ 测试 3 通过（任务提交）
✓ 测试 4 通过（多任务）
✓ 所有测试通过
```

---

## 📝 与现有架构的集成

### 渐进式迁移方案

**阶段 1：并行运行（推荐）**

```python
# 保留现有 task_executor.py
# 新建 task_executor_with_pool.py
# 在新任务中使用进程池架构
```

**阶段 2：小规模测试**

```python
# 选择1-2个摄像机使用进程池
# 其他摄像机继续使用线程架构
# 对比性能和稳定性
```

**阶段 3：全面迁移**

```python
# 所有摄像机切换到进程池架构
# 移除旧的线程架构代码
```

---

## ⚠️ 注意事项

### 1. 共享内存管理

```python
# 检测进程需要访问摄像机的共享内存
# 确保共享内存名称正确注册
pool.register_camera('cam-001', 'shm_cam_001')
```

### 2. 检测结果传递

```python
# 当前版本使用 mp.Event 通知完成
# 检测结果需要额外机制传递（TODO）

# 推荐方案：
# - 使用 mp.Array 存储检测结果
# - 使用 Manager.dict() 共享字典
# - 使用额外的结果队列
```

### 3. 资源清理

```python
# 确保进程池正确停止
try:
    # ... 使用进程池 ...
finally:
    pool.stop()  # 自动清理所有进程
```

---

## 🔗 相关文件

- **核心模块**：
  - `src/detectors/detection_queue.py` - 检测队列
  - `src/detectors/detection_process_pool.py` - 进程池管理

- **集成示例**：
  - `src/tasks/task_executor_with_pool.py` - CameraWorker 集成示例

- **测试脚本**：
  - `tests/test_detection_process_pool.py` - 功能测试

---

## 📚 参考资料

- **Frigate 源码**：
  - `frigate/object_detection.py` - ObjectDetectProcess
  - `frigate/util/process.py` - Process 基类
  - `frigate/service_manager/multiprocessing.py` - ServiceProcess

- **设计文档**：
  - 项目进程设计分析（见对话历史）
  - 多进程架构对比（Frigate vs 当前项目）

---

## 💡 常见问题

### Q1: 检测进程崩溃会怎样？

**A:** 单个检测进程崩溃不影响其他进程。进程池会检测到进程退出，但当前版本未实现自动重启（可扩展）。

### Q2: 如何知道检测是否完成？

**A:** 使用 `mp.Event` 等待通知：

```python
completed = pool.wait_for_detection('cam-001', timeout=5.0)
```

### Q3: 队列满了怎么办？

**A:** 任务会被丢弃（非阻塞提交）。建议：
- 增加队列大小
- 增加检测进程数量
- 优化检测速度

### Q4: 多个摄像机如何共享检测进程池？

**A:** 创建全局的检测进程池实例，所有摄像机进程共享：

```python
# 在主进程中创建
global_pool = DetectionProcessPool(num_detectors=2, ...)

# 传递给每个摄像机进程
camera1 = CameraProcess(global_pool)
camera2 = CameraProcess(global_pool)
```

---

## 🎯 下一步计划

1. **检测结果传递机制**：实现完整的检测结果回传（使用 mp.Array 或 Manager）
2. **自动重启机制**：检测进程崩溃后自动重启
3. **性能监控面板**：实时显示进程池状态、队列大小、FPS等
4. **GPU 负载均衡**：多 GPU 环境下的任务分配优化

---

## 📞 技术支持

如有问题，请参考：
- 测试脚本：`tests/test_detection_process_pool.py`
- 集成示例：`src/tasks/task_executor_with_pool.py`
- Frigate 源码：`D:\frigate\frigate\object_detection.py`