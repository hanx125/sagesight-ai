# Frigate vs SageSight-AI 跟踪和事件生成对比分析

## 📊 核心架构对比

### 1. 跟踪器实现

#### **Frigate（TrackedObject 类）**

**文件位置**：`frigate/track/tracked_object.py`

**核心特性**：
- **完整的对象状态管理**
  - `score_history`：分数历史（用于计算中位数）
  - `computed_score`：计算分数（中位数）
  - `false_positive`：误报判定
  - `active`：活跃状态
  - `stationary`：静止状态

- **Zone 区域管理**
  - `zone_presence`：区域存在计数
  - `zone_loitering`：区域徘徊计数
  - `current_zones`：当前所在区域
  - `entered_zones`：进入过的区域

- **媒体管理**
  - `thumbnail_data`：缩略图数据
  - `has_clip`：是否有录像
  - `has_snapshot`：是否有快照

- **属性管理**
  - `attributes`：属性字典（支持多种属性）
  - `pending_loitering`：徘徊判定

- **Review 系统**
  - `max_severity`：最大严重性（alert/detection）

#### **SageSight-AI（BoTSORTTracker）**

**文件位置**：`src/tracking/botsort/botsort_tracker.py`

**核心特性**：
- **三阶段匹配策略**
  - 第一阶段：高分检测 + 活跃轨迹（IoU + ReID）
  - 第二阶段：低分检测 + 丢失轨迹（IoU）
  - 第三阶段：ReID 重识别

- **轨迹管理**
  - `tracked_tracks`：活跃轨迹
  - `lost_tracks`：丢失轨迹

- **参数**
  - `track_thresh`：高分检测阈值
  - `low_score_thresh`：低分检测阈值
  - `match_thresh`：IoU 匹配阈值
  - `max_age`：最大丢失帧数

---

### 2. 事件生成机制

#### **Frigate（CameraState 类）**

**文件位置**：`frigate/object_processing.py`

**核心特性**：
- **完整的对象生命周期管理**
  ```python
  class CameraState:
      def __init__(self):
          self.tracked_objects: dict[str, TrackedObject] = {}
          self.best_objects: dict[str, TrackedObject] = {}
          self.zone_objects = defaultdict(list)
          self.frame_cache = {}
  ```

- **事件触发条件**（对象状态变化）
  ```python
  # Frigate 的触发逻辑
  def should_trigger_event(self, obj):
      # 1. 不是误报
      if obj.false_positive:
          return False
      
      # 2. 必需区域检查
      if required_zones and not (set(obj.entered_zones) & required_zones):
          return False
      
      # 3. 分数阈值检查
      if obj.computed_score < threshold:
          return False
      
      # 4. 区域变化检查
      if zone_changed:
          return True
      
      return True
  ```

- **Zone 事件生成**
  ```python
  # 进入区域事件
  for zone_name in obj.entered_zones:
      if zone_name not in previous_zones:
          emit_event("zone_enter", obj, zone_name)
  
  # 离开区域事件
  for zone_name in previous_zones:
      if zone_name not in obj.entered_zones:
          emit_event("zone_leave", obj, zone_name)
  ```

- **缩略图管理**
  ```python
  # 自动选择最佳缩略图
  if is_better_thumbnail(label, thumbnail_data, obj_data, frame_shape):
      thumbnail_data = {
          "frame_time": frame_time,
          "box": bbox,
          "area": area,
          "region": region,
          "score": score,
          "attributes": attributes
      }
  ```

#### **SageSight-AI（CameraWorker 类）**

**文件位置**：`src/tasks/task_executor.py`

**核心特性**：
- **简单的事件触发**（基于置信度）
  ```python
  # 置信度阈值检查
  event_threshold = self.event_cfg.get('event_threshold', 0.7)
  should_trigger_event = confidence >= event_threshold
  ```

- **Zone 事件发送**（已集成）
  ```python
  # Zone 区域检测
  if self.zone_manager:
      zone_events = self.zone_manager.check_object_zones(track_id, bbox)
      for zone_name, event_type in zone_events.items():
          self._emit_zone_event(track_id, zone_name, event_type, bbox, confidence)
  ```

- **事件输出**
  ```python
  # 直接输出到多种通道
  self.event_output.emit_event(event_type, event_data)
  ```

---

### 3. 对象属性计算

#### **Frigate（丰富的属性）**

**关键属性**：
```python
class TrackedObject:
    def update(self, frame_time, obj_data):
        # 1. 分数计算（中位数）
        self.computed_score = median(self.score_history)
        
        # 2. 误报判定
        self.false_positive = self.computed_score < threshold
        
        # 3. 静止状态（基于 motionless_count）
        if position_changed:
            self.motionless_count = 0
        else:
            self.motionless_count += 1
        
        # 4. 区域检查
        for zone_name, zone in zones.items():
            if point_in_zone(obj.position, zone):
                self.zone_presence[zone_name] += 1
                
        # 5. 徘徊检测
        if self.zone_presence[zone_name] > loitering_threshold:
            self.pending_loitering = True
        
        # 6. 缩略图更新
        if is_better_thumbnail(...):
            self.thumbnail_data = {...}
```

#### **SageSight-AI（基础属性）**

**关键属性**：
```python
class CameraWorker:
    def _process_tracks(self, tracks):
        for track in tracks:
            track_id = track[4]
            bbox = track[:4]
            confidence = track[5]
            class_id = track[6]
            
            # Zone 检测（已集成）
            zone_events = self.zone_manager.check_object_zones(track_id, bbox)
            
            # 对象状态更新（已集成）
            object_state = self.object_state_manager.update_object(track_id, bbox)
            
            # 必需区域过滤（已集成）
            if not self.zone_manager.should_emit_event(track_id, bbox):
                continue
```

---

## 🔍 关键差异总结

### 1. **对象状态管理**

| 功能 | Frigate | SageSight-AI | 状态 |
|------|---------|--------------|------|
| **分数历史** | ✓（median计算） | ✗ | 未实现 |
| **误报判定** | ✓（computed_score） | ✗ | 未实现 |
| **静止状态** | ✓（motionless_count） | ✓（ObjectStateManager） | 已实现 |
| **区域计数** | ✓（zone_presence） | ✓（ZoneManager） | 已实现 |
| **徘徊检测** | ✓（loitering） | ✗ | 未实现 |

### 2. **事件触发机制**

| 功能 | Frigate | SageSight-AI | 状态 |
|------|---------|--------------|------|
| **多条件触发** | ✓（误报+区域+分数） | △（仅置信度） | 需改进 |
| **必需区域过滤** | ✓ | ✓ | 已实现 |
| **缩略图选择** | ✓（最佳帧） | ✗ | 未实现 |
| **属性事件** | ✓ | ✗ | 未实现 |

### 3. **Zone 管理**

| 功能 | Frigate | SageSight-AI | 状态 |
|------|---------|--------------|------|
| **进入/离开检测** | ✓ | ✓ | 已实现 |
| **区域计数** | ✓ | ✓ | 已实现 |
| **徘徊检测** | ✓ | ✗ | 未实现 |
| **区域对象限制** | ✓ | △ | 部分实现 |

---

## 💡 改进建议

### 优先级 1：分数历史和误报判定

**Frigate 的优势**：
- 使用中位数计算分数，更稳定
- 基于分数历史判定误报，减少抖动

**改进方案**：
```python
# 在 Track 类中添加分数历史
class Track:
    def __init__(self):
        self.score_history = deque(maxlen=10)
    
    def update_score(self, score):
        self.score_history.append(score)
        self.computed_score = median(self.score_history)
        
        # 误报判定
        self.false_positive = self.computed_score < threshold
```

### 优先级 2：徘徊检测

**Frigate 的优势**：
- 检测对象在区域内的停留时间
- 触发徘徊警报

**改进方案**：
```python
# 在 ZoneManager 中添加徘徊检测
class ZoneManager:
    def check_loitering(self, track_id, min_duration=30):
        """检测徘徊（对象在区域内停留超过 min_duration 帧）"""
        for zone_name, count in self.object_zones.items():
            if count > min_duration:
                return zone_name
        return None
```

### 优先级 3：缩略图选择

**Frigate 的优势**：
- 自动选择最佳缩略图（最高分数、最佳位置）
- 提高事件质量

**改进方案**：
```python
# 在事件输出中添加缩略图选择
class EventOutput:
    def update_thumbnail(self, event_id, frame, bbox, score):
        """更新事件缩略图（选择最佳帧）"""
        if score > self.best_score:
            self.thumbnails[event_id] = crop_frame(frame, bbox)
            self.best_score = score
```

---

## 📊 性能对比

| 指标 | Frigate | SageSight-AI |
|------|---------|--------------|
| **跟踪精度** | 高（中位数计算） | 中（实时计算） |
| **事件质量** | 高（多条件触发） | 中（单条件触发） |
| **误报率** | 低（分数历史） | 中（无历史） |
| **Zone 管理** | 完善 | 已实现核心功能 |
| **代码复杂度** | 高 | 低 |

---

## 🎯 总结

### SageSight-AI 已实现的核心功能：
- ✓ Zone 区域检测（进入/离开）
- ✓ 对象状态管理（静止/移动）
- ✓ 必需区域过滤
- ✓ 事件输出

### 需要改进的功能：
1. **分数历史和误报判定**（提高稳定性）
2. **徘徊检测**（Zone 扩展功能）
3. **缩略图选择**（提高事件质量）
4. **属性事件**（扩展事件类型）

### 建议：
- 保持 SageSight-AI 的简洁架构
- 逐步借鉴 Frigate 的优秀设计
- 优先实现分数历史和误报判定（提高稳定性）