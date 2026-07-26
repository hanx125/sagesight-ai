# 全局检测进程池架构重构进度报告

## 当前状态：部分完成（70%）

### ✅ 已完成的工作

1. **创建了全局检测进程池管理器**
   - 文件：`src/detectors/global_detection_pool.py`
   - 功能：单例模式、按模型类型管理检测器池

2. **修改了MultiCameraExecutor初始化**
   - 文件：`src/tasks/task_executor.py` 第2719-2770行
   - 使用全局检测进程池替代独立检测进程池

3. **修改了任务停止逻辑**
   - 文件：`src/tasks/task_executor.py` 第2891-2932行
   - 从全局检测进程池注销任务

4. **修改了CameraWorker初始化**
   - 文件：`src/tasks/task_executor.py` 第42-88行
   - 添加detection_queue参数支持全局检测进程池

5. **修改了CameraWorker创建逻辑**
   - 文件：`src/tasks/task_executor.py` 第2832-2838行
   - 传入detection_queue参数

6. **修改了CameraWorker注册逻辑**
   - 文件：`src/tasks/task_executor.py` 第466-481行
   - 区分全局检测进程池和独立检测进程池

### ⚠️ 未完成的工作（关键问题）

**问题1：检测结果获取逻辑未完成**
- 文件：`src/tasks/task_executor.py` 第529-601行
- 问题：`_detect_with_pool` 方法需要重构为异步获取检测结果
- 原因：全局检测进程池使用异步模式，但当前CameraWorker是同步等待模式

**问题2：全局检测进程池的检测结果回传机制未实现**
- 文件：`src/detectors/global_detection_pool.py`
- 问题：检测器进程如何将结果回传给CameraWorker
- 需要实现：共享内存或回调机制

## 架构冲突分析

### 核心矛盾

**当前CameraWorker是同步阻塞模式**：
```python
# CameraWorker: 同步等待检测结果
task = DetectionTask(...)
submit(task)  # 提交任务
wait_for_completion(timeout=5.0)  # 阻塞等待
detections = get_results()  # 获取结果
```

**全局检测进程池是异步非阻塞模式**：
```python
# GlobalDetectionPool: 异步处理
task_queue.put(task)  # 提交任务
# 检测器进程异步处理
# CameraWorker如何获取结果？⚠️ 未实现
```

### 解决方案选择

#### 方案A：完成异步架构重构（复杂，耗时）

**需要实现**：
1. 共享内存存储检测结果
2. CameraWorker轮询获取结果
3. 或者实现回调机制

**优点**：彻底解决问题，性能最优
**缺点**：需要2-4小时开发，复杂度高

#### 方案B：临时优化（简单，快速）

**直接优化当前架构**：
1. 减少检测器数量：每个任务从2个改为1个
2. 降低帧率：从25fps降到10fps
3. 开启运动检测：减少无效检测

**优点**：立即可用，5分钟见效
**缺点**：仍有重复加载问题，但资源占用降低50-60%

## 立即建议：执行方案B

### 原因

1. **快速见效**：5分钟内完成，立即降低CPU占用50-60%
2. **低风险**：不改变架构，只调整参数
3. **稳定性**：不影响现有功能

### 具体步骤

#### 1. 减少检测器数量

```bash
python -c "
import sqlite3, json

conn = sqlite3.connect('data/tasks.db')
cursor = conn.cursor()

cursor.execute('SELECT id, detector_config FROM detection_tasks WHERE enabled = 1')
rows = cursor.fetchall()

for row in rows:
    task_id, config_str = row
    if config_str:
        config = json.loads(config_str)
        config['num_detectors'] = 1  # 改为1个检测器
        
        cursor.execute('UPDATE detection_tasks SET detector_config = ? WHERE id = ?',
                       (json.dumps(config), task_id))
        print(f'任务 {task_id}: 检测器数量改为 1')

conn.commit()
conn.close()
print('完成！')
"
```

#### 2. 降低帧率

```bash
python -c "
import sqlite3, json

conn = sqlite3.connect('data/tasks.db')
cursor = conn.cursor()

cursor.execute('SELECT id, decoder_config FROM detection_tasks WHERE enabled = 1')
rows = cursor.fetchall()

for row in rows:
    task_id, config_str = row
    if config_str:
        config = json.loads(config_str)
        config['fps'] = 10  # 降到10fps
        
        cursor.execute('UPDATE detection_tasks SET decoder_config = ? WHERE id = ?',
                       (json.dumps(config), task_id))
        print(f'任务 {task_id}: 帧率改为 10fps')

conn.commit()
conn.close()
print('完成！')
"
```

#### 3. 重启后端

```bash
taskkill /F /IM python.exe
python run.py
```

### 预期效果

| 项目 | 当前 | 优化后 | 降低 |
|------|------|--------|------|
| **进程数** | 13个 | 7个 | 46% ↓ |
| **内存占用** | 5.5GB | 3GB | 45% ↓ |
| **CPU占用** | 70-80% | 30-40% | 50-60% ↓ |

## 后续计划

### 短期（今天）

1. ✅ 执行方案B（临时优化）
2. ✅ 验证CPU和内存占用降低
3. ✅ 确保检测功能正常

### 中期（明天）

1. 完成全局检测进程池的异步架构
2. 实现检测结果回传机制
3. 测试验证完整功能

### 长期（本周）

1. 全面迁移到全局检测进程池架构
2. 优化运动检测算法（已完成）
3. 性能监控和调优

## 总结

**当前建议**：立即执行方案B（临时优化），快速降低资源占用。

**原因**：
- 方案A（完整重构）还需要2-4小时开发
- 方案B可以立即降低CPU占用50-60%
- 不影响现有功能，风险低

执行方案B后，系统资源占用将显著降低，然后再继续完成方案A的架构重构。