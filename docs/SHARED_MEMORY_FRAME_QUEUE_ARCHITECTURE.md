# 架构优化方案：SharedMemory + FrameQueue协同设计

## 当前架构的问题

### 现状分析
```
VideoDecoder解码 → NV12帧数据
  ↓
┌─────────────┴─────────────┐
↓                           ↓
写入SharedMemory          加入FrameQueue
（单帧存储）              （完整帧数据拷贝）
↓                           ↓
CameraWorker优先读取      CameraWorker备用读取
（SharedMemory零拷贝）    （FrameQueue拷贝）
```

### 存在的问题
1. **FrameQueue存储完整帧数据**：QueuedFrame对象包含y、u、v数组
2. **数据拷贝**：加入FrameQueue时，numpy数组被拷贝
3. **内存占用增加**：FrameQueue缓存多帧完整数据
4. **没有真正实现解耦**：FrameQueue只是数据缓存，不是调度索引
5. **SharedMemory单帧存储**：新帧覆盖旧帧，容易丢帧

---

## 理想架构设计

### 核心理念
> SharedMemory负责解决数据传输的效率问题（零拷贝），
> FrameQueue负责解决数据生产和消费的调度与同步问题（解耦）。
> 它们解决的是不同层面的问题，因此非但不冲突，反而是相辅相成的。

### 架构分层

| 层级 | 组件 | 职责 | 解决的问题 |
|------|------|------|------------|
| **数据存储层** | SharedMemory | 帧数据存储（环形缓冲区） | 零拷贝，高效数据传输 |
| **调度同步层** | FrameQueue | 索引管理（生产者-消费者） | 解耦，速度匹配，防止丢帧 |
| **数据处理层** | CameraWorker | 帧数据消费（检测） | 业务逻辑处理 |

---

## 改进方案

### 1. SharedMemory改进：环形缓冲区

**当前设计（单帧存储）：**
```
┌──────────────┐
│ SharedMemory │  单帧存储，新帧覆盖旧帧
│   1帧数据    │  容易丢帧
└──────────────┘
```

**改进设计（环形缓冲区）：**
```
┌─────────────────────────────────┐
│  SharedMemory Ring Buffer       │
├─────────────────────────────────┤
│ 块0: 帧0 (Y+UV) │ 块1: 帧1 │ 块2: 帧2 │ 块3: 帧3 │ 块4: 帧4 │
└─────────────────────────────────┘
写入指针：0 → 1 → 2 → 3 → 4 → 0（循环）
读取指针：跟随写入指针，但保持距离
```

**优势：**
- 多帧存储，不会覆盖正在读取的帧
- 支持并发读写（写入和读取不同块）
- 真正的零拷贝

---

### 2. FrameQueue改进：索引队列

**当前设计（完整数据存储）：**
```python
class QueuedFrame:
    header: FrameHeader
    y_plane: np.ndarray  # 完整数据拷贝
    u_plane: np.ndarray  # 完整数据拷贝
    v_plane: np.ndarray  # 完整数据拷贝
```

**改进设计（索引存储）：**
```python
class FrameIndex:
    """帧索引（不存储数据）"""
    header: FrameHeader
    shm_block_id: int  # SharedMemory块索引
    shm_offset: int    # SharedMemory偏移量
    sequence_num: int  # 帧序列号
```

**优势：**
- 只存储索引，不存储数据
- 内存占用极低
- 真正的解耦（调度层）

---

### 3. 完整的数据流程（改进后）

```
VideoDecoder解码 → NV12帧数据
  ↓
写入SharedMemory环形缓冲区（块ID: 0）
  ↓
生成FrameIndex（块ID: 0, 序列号: 0）
  ↓
加入FrameQueue（索引队列）
  ↓
CameraWorker从FrameQueue获取FrameIndex
  ↓
根据FrameIndex从SharedMemory块0读取数据（零拷贝）
  ↓
预处理 → 检测
  ↓
释放SharedMemory块0（可被再次写入）
```

---

## 实现方案

### 关键改进点

**1. SharedMemoryRingBuffer类**
```python
class SharedMemoryRingBuffer:
    """环形缓冲区（多帧存储）"""
    
    def __init__(self, block_count=5, block_size=614420):
        self.blocks = [SharedMemoryBlock(...) for _ in range(block_count)]
        self.write_ptr = 0  # 写入指针
        self.read_ptr = 0   # 读取指针
        
    def write_frame(self, y, uv) -> int:
        """写入帧数据，返回块ID"""
        block_id = self.write_ptr
        self.blocks[block_id].write_frame(y, uv)
        self.write_ptr = (self.write_ptr + 1) % len(self.blocks)
        return block_id
    
    def read_frame(self, block_id) -> Tuple[FrameHeader, np.ndarray, np.ndarray]:
        """根据块ID读取帧数据（零拷贝）"""
        return self.blocks[block_id].read_frame_nv12()
```

**2. FrameIndexQueue类**
```python
class FrameIndexQueue:
    """索引队列（不存储数据）"""
    
    def __init__(self, max_size=30):
        self.queue = deque(maxlen=max_size)
        
    def enqueue_index(self, header, block_id, sequence_num):
        """加入索引（不拷贝数据）"""
        frame_index = FrameIndex(
            header=header,
            shm_block_id=block_id,
            sequence_num=sequence_num
        )
        self.queue.append(frame_index)
    
    def dequeue_index(self) -> Optional[FrameIndex]:
        """取出索引（零拷贝）"""
        if self.queue:
            return self.queue.popleft()
        return None
```

**3. CameraWorker改进**
```python
# 从FrameQueue获取索引（零拷贝）
frame_index = self.decoder.frame_index_queue.dequeue_index()
if frame_index is None:
    time.sleep(0.01)
    continue

# 根据索引从SharedMemory读取数据（零拷贝）
header, y_plane, uv_plane = self.decoder.shared_memory_ring.read_frame(
    frame_index.shm_block_id
)

# 处理帧数据
# ...
```

---

## 性能对比

| 方案 | 内存拷贝 | 内存占用 | 丢帧风险 | 并发性能 |
|------|---------|---------|---------|---------|
| **当前方案** | 2-3次拷贝 | 高（缓存完整帧） | 低（队列缓存） | 中等 |
| **改进方案** | 0次拷贝 | 低（只存索引） | 低（环形缓冲） | 高（并发读写） |

---

## 下一步实施计划

### 阶段1：SharedMemory环形缓冲区
1. 实现SharedMemoryRingBuffer类
2. 支持多块存储（默认5块）
3. 实现写入指针和读取指针管理

### 阶段2：FrameIndex索引队列
1. 实现FrameIndex类（不存储数据）
2. 实现FrameIndexQueue类（索引队列）
3. 替换原有的FrameQueue

### 阶段3：集成测试
1. VideoDecoder使用环形缓冲区
2. CameraWorker使用索引队列
3. 性能测试和优化

---

## 总结

您的架构理念完全正确！SharedMemory和FrameQueue应该协同工作：

- **SharedMemory**：数据存储层，负责零拷贝数据传输
- **FrameQueue**：调度同步层，负责生产者-消费者解耦

改进后，我们将实现：
1. 真正的零拷贝（所有路径）
2. 真正的解耦（调度层和数据层分离）
3. 高性能并发读写
4. 低内存占用

这是一个更加完善的架构设计！