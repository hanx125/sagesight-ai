# 全局检测进程池架构重构方案

## 问题分析

### 当前架构（问题）

```
任务1 (MultiCameraExecutor)
  └── 检测进程池1
      ├── 检测器1 (YOLOv11n) ← 加载模型1 (900MB)
      └── 检测器2 (YOLOv11n) ← 加载模型2 (900MB)

任务2 (MultiCameraExecutor)
  └── 检测进程池2
      ├── 检测器3 (YOLOv11n) ← 加载模型3 (900MB) ⚠️ 重复！
      └── 检测器4 (YOLOv11n) ← 加载模型4 (900MB) ⚠️ 重复！

任务3 (MultiCameraExecutor)
  └── 检测进程池3
      ├── 检测器5 (YOLOv11n) ← 加载模型5 (900MB) ⚠️ 重复！
      └── 检测器6 (YOLOv11n) ← 加载模型6 (900MB) ⚠️ 重复！
```

**资源浪费**：
- 内存：6 × 900MB = 5.4GB
- CPU：6个进程并发运行
- GPU显存：6个模型实例

### 目标架构（优化后）

```
主进程
  └── GlobalDetectionPoolManager (单例)
      ├── detector_pool_yolov11n
      │   ├── 检测器1 (YOLOv11n) ← 加载模型1 (900MB)
      │   └── 检测器2 (YOLOv11n) ← 加载模型2 (900MB)
      ├── detector_pool_yolov11m
      │   └── 检测器3 (YOLOv11m) ← 加载模型3 (900MB) ← 不同模型
      └── detector_pool_yolov8n
          └── 检测器4 (YOLOv8n) ← 加载模型4 (900MB) ← 不同模型

任务1 → detection_queue → GlobalDetectionPoolManager
任务2 → detection_queue → GlobalDetectionPoolManager
任务3 → detection_queue → GlobalDetectionPoolManager
```

**资源节省**：
- 内存：只加载不同类型的模型
- CPU：共享检测器进程
- GPU显存：避免重复加载

## 重构步骤

### 步骤1：创建全局检测进程池管理器 ✅

已完成：`src/detectors/global_detection_pool.py`

核心功能：
- 单例模式：确保全局只有一个实例
- 按模型类型管理检测器池：避免重复加载
- 任务注册/注销机制
- 共享检测结果存储

### 步骤2：修改主进程初始化（run.py）

**修改文件**：`run.py`

**修改位置**：主进程启动时初始化全局检测进程池管理器

```python
# 在主进程启动时初始化
from src.detectors.global_detection_pool import get_global_detection_pool_manager

# 全局检测进程池管理器（单例）
global_pool_manager = get_global_detection_pool_manager()
```

### 步骤3：修改MultiCameraExecutor初始化

**修改文件**：`src/tasks/task_executor.py`

**修改位置**：第2722-2757行

**修改前**：
```python
from src.detectors.detection_process_pool import DetectionProcessPool
from src.detectors.base import DetectorConfig

# 创建检测进程池（每个任务独立）
num_detectors = self.task_config.get('num_detectors', 2)
queue_maxsize = self.task_config.get('detection_queue_maxsize', 100)

self.detection_pool = DetectionProcessPool(
    num_detectors=num_detectors,
    detector_config=detector_config_obj,
    detection_queue_maxsize=queue_maxsize
)

# 启动检测进程池
self.detection_pool.start()
```

**修改后**：
```python
from src.detectors.global_detection_pool import get_global_detection_pool_manager

# 使用全局检测进程池管理器（共享）
global_pool_manager = get_global_detection_pool_manager()

# 注册任务到全局检测进程池
model_type = detector_config['model_type']  # 如 'yolov11n'
model_path = detector_config['model_path']
num_detectors = self.task_config.get('num_detectors', 2)

# 注册任务，获取专属检测队列
self.detection_queue = global_pool_manager.register_task(
    task_id=str(self.task_id),
    model_type=model_type,
    model_path=model_path,
    detector_config=detector_config_obj.__dict__,  # 转换为字典
    num_detectors=num_detectors
)

# 保存管理器引用（用于获取结果）
self.global_pool_manager = global_pool_manager
```

### 步骤4：修改CameraWorker初始化

**修改文件**：`src/tasks/task_executor.py`

**修改位置**：CameraWorker初始化逻辑

**修改前**：
```python
# 注册摄像机到检测进程池
self.detection_pool.register_camera(self.camera_id, self.decoder.shm_name)

# 获取检测事件
self.detection_event = self.detection_pool.get_detection_event(self.camera_id)
```

**修改后**：
```python
# 使用全局检测进程池（已经在MultiCameraExecutor注册）
# CameraWorker只需要使用共享的detection_queue
self.detection_queue = detection_queue  # 从MultiCameraExecutor传入
```

### 步骤5：修改检测结果获取

**修改文件**：`src/tasks/task_executor.py`

**修改位置**：`_detect_with_pool`方法

**修改前**：
```python
# 从检测进程池获取结果
detections = self.detection_pool.get_detection_results(self.camera_id)
```

**修改后**：
```python
# 从全局检测进程池获取结果
detections = self.global_pool_manager.get_detection_results(self.camera_id)
```

### 步骤6：修改任务停止逻辑

**修改文件**：`src/tasks/task_executor.py`

**修改位置**：任务停止时注销检测器

**修改前**：
```python
# 停止检测进程池
self.detection_pool.stop()
```

**修改后**：
```python
# 从全局检测进程池注销任务
self.global_pool_manager.unregister_task(
    task_id=str(self.task_id),
    model_type=self.detector_config['model_type']
)
```

## 预期效果

### 资源节省对比

| 项目 | 当前架构 | 优化后架构 | 节省 |
|------|---------|-----------|------|
| **内存占用** | 5.4GB | 1.8GB | 67% ↓ |
| **进程数** | 13个 | 5个 | 62% ↓ |
| **模型实例** | 6个 | 2个 | 67% ↓ |

### 具体计算

假设3个任务都使用YOLOv11n：

**当前架构**：
- 3个任务 × 2个检测器 = 6个模型实例
- 6 × 900MB = 5.4GB内存

**优化后架构**：
- 1个全局检测器池（YOLOv11n）
- 2个检测器（可配置）
- 2 × 900MB = 1.8GB内存

**节省**：5.4GB - 1.8GB = 3.6GB（67%）

## 实施步骤

### 第1步：修改task_executor.py（已完成）

✅ 创建全局检测进程池管理器

### 第2步：修改MultiCameraExecutor初始化（待实施）

修改第2722-2757行，使用全局检测进程池

### 第3步：修改CameraWorker逻辑（待实施）

调整CameraWorker使用共享的检测队列

### 第4步：测试验证

1. 启动1个任务，验证检测正常
2. 启动2个相同模型的任务，验证不重复加载
3. 启动不同模型的任务，验证多模型支持
4. 停止任务，验证资源释放

## 风险评估

### 低风险

- ✅ 向后兼容：保留旧的检测模式
- ✅ 渐进式重构：可以逐步迁移

### 需要测试的场景

1. **单任务**：验证基础功能
2. **多任务同模型**：验证共享机制
3. **多任务不同模型**：验证多池管理
4. **任务停止/重启**：验证资源释放
5. **异常情况**：验证稳定性

## 总结

这个架构重构将显著降低资源占用：
- **内存节省67%**（从5.4GB降到1.8GB）
- **进程数减少62%**（从13个降到5个）
- **模型实例减少67%**（从6个降到2个）

建议立即实施，快速降低CPU和内存占用。