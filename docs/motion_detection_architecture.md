# 运动检测和预处理架构文档

## 📊 架构总览

```
graph TD 
     A[共享内存<br/>YUV帧] -->|frame_manager.get| B[帧数据<br/>video.py:583] 
     B -->|motion_detector.detect| C[运动检测<br/>590] 
     C --> D[运动区域<br/>motion_boxes] 
     D -->|静止物体判断| E{是否有运动?} 
     
     E -->|有运动| F[创建检测区域<br/>regions] 
     E -->|无运动| G[跳过检测<br/>优化性能] 
     
     F --> H[create_tensor_input<br/>preprocessor.py:277] 
     H --> I[预处理] 
     I --> J[detect_raw<br/>yolo_detector.py] 
     J --> K[目标检测] 
     
     K --> L[检测结果] 
     L --> M[目标跟踪<br/>object_tracker] 
     M --> N[事件生成]
```

## 🏗️ 模块分层

### **第1层：运动检测层** (`src/motion/`)

**独立模块，用于优化目标检测性能**

#### 功能：
- 基于 MOG2 背景建模的运动检测
- 检测运动区域（motion_boxes）
- 性能优化（跳过无运动帧）

#### 核心类：
- `MotionDetector`: MOG2 运动检测器
- `MotionRegion`: 运动区域数据结构
- `MotionFilter`: 运动区域过滤工具

#### 使用示例：
```python
from src.motion import MotionDetector

# 创建运动检测器
motion_detector = MotionDetector(
    history=500,
    var_threshold=16.0,
    min_area=500
)

# 检测运动
motion_regions = motion_detector.detect(frame)

# 快速判断是否有运动
if motion_detector.has_motion(frame):
    print("检测到运动！")
```

---

### **第2层：目标检测层** (`src/detectors/`)

**包含预处理和目标检测功能**

#### 2.1 预处理模块 (`preprocessor.py`)

**核心功能：从运动区域创建张量输入**

#### 功能：
- YUV420p / NV12 → RGB 转换
- 裁剪运动区域
- 缩放到模型输入尺寸
- 归一化 + CHW 格式

#### 核心方法：
- `create_tensor_input()`: 从帧数据创建张量输入
- `preprocess_nv12()`: NV12 格式预处理
- `preprocess()`: YUV420p 格式预处理
- `preprocess_drm_prime()`: DRM Prime 格式预处理

#### 使用示例：
```python
from src.detectors import OptimizedPreprocessor

# 创建预处理器
preprocessor = OptimizedPreprocessor(
    width=640,
    height=640,
    model_input_size=640
)

# 方式1：处理完整帧
frame_data = {
    'y': y_plane,
    'uv': uv_plane,
    'format': 'nv12'
}
tensor = preprocessor.create_tensor_input(frame_data)

# 方式2：处理运动区域（裁剪）
motion_region = (100, 100, 500, 500)
tensor = preprocessor.create_tensor_input(
    frame_data=frame_data,
    region=motion_region,
    frame_shape=(640, 640)
)
```

#### 2.2 目标检测模块 (`yolo_detector.py`)

**核心功能：YOLO 目标检测**

#### 功能：
- 加载 YOLO 模型
- 执行目标检测
- 返回检测结果

#### 核心类：
- `YOLODetector`: YOLO 检测器
- `DetectorConfig`: 检测器配置
- `Detection`: 检测结果

#### 使用示例：
```python
from src.detectors import YOLODetector, DetectorConfig

# 创建检测器配置
config = DetectorConfig(
    model_path="models/yolov8n.engine",
    confidence_threshold=0.5,
    nms_threshold=0.4
)

# 创建检测器
detector = YOLODetector(config)

# 执行检测
detections = detector.detect(tensor)

# 处理结果
for det in detections:
    print(f"类别: {det.class_name}, 置信度: {det.confidence:.2f}")
```

---

## 🔄 完整工作流程

### **步骤1：获取帧数据**
```python
# 从共享内存获取帧数据
frame_index = decoder.get_frame_index()
if frame_index:
    y, uv = decoder.get_frame_from_block(frame_index.shm_block_id)
    
    frame_data = {
        'y': y,
        'uv': uv,
        'format': 'nv12'
    }
```

### **步骤2：运动检测**
```python
# 检测运动
motion_regions = motion_detector.detect(frame)

# 判断是否有运动
if not motion_regions:
    # 跳过检测（优化性能）
    continue
```

### **步骤3：创建检测区域**
```python
from src.motion import MotionFilter

# 合并重叠区域
merged_regions = MotionFilter.merge_overlapping_regions(motion_regions)

# 扩展区域（覆盖更多潜在目标）
expanded_regions = MotionFilter.expand_regions(
    merged_regions, 
    frame_shape=(640, 640),
    expand_ratio=0.1
)

# 限制区域数量
final_regions = MotionFilter.limit_region_count(expanded_regions, max_count=10)
```

### **步骤4：创建张量输入**
```python
# 为每个运动区域创建张量输入
tensors = []
for region in final_regions:
    tensor = preprocessor.create_tensor_input(
        frame_data=frame_data,
        region=region.bbox,
        frame_shape=(640, 640)
    )
    tensors.append(tensor)
```

### **步骤5：目标检测**
```python
# 对每个张量执行检测
all_detections = []
for tensor in tensors:
    detections = detector.detect(tensor)
    all_detections.extend(detections)
```

---

## 🎯 性能优化策略

### **策略1：运动检测优化**
- ✅ 快速预检（统计前景像素数量）
- ✅ 最小面积过滤（减少噪声）
- ✅ 区域数量限制（防止过多检测）

### **策略2：区域裁剪优化**
- ✅ 只处理运动区域（减少检测范围）
- ✅ 合并重叠区域（减少重复检测）
- ✅ 扩展区域边界（覆盖更多潜在目标）

### **策略3：预处理优化**
- ✅ 预分配缓冲区（避免内存分配）
- ✅ OpenCV 加速（GPU 加速）
- ✅ 批量处理（减少 GPU 调用）

---

## 📈 性能对比

| 方案 | 检测次数/帧 | CPU 占用 | 延迟 | 适用场景 |
|------|-----------|---------|------|---------|
| **全帧检测** | 1 次 | 高 | 高 | 所有场景 |
| **运动检测优化** | 0-N 次 | 中 | 中 | 固定摄像头 |
| **区域裁剪优化** | 0-N 次 | 低 | 低 | 高性能需求 |

---

## 🚀 最佳实践

### **场景1：室外监控（光照变化）**
```python
# 使用 MOG2 运动检测（抗光照变化）
motion_detector = MotionDetector(
    history=500,
    var_threshold=16.0,
    detect_shadows=False
)

# 使用完整的帧处理
tensor = preprocessor.create_tensor_input(frame_data)
```

### **场景2：室内监控（光照稳定）**
```python
# 使用帧平均法（可选）
# 使用区域裁剪优化
motion_regions = motion_detector.detect(frame)
if motion_regions:
    tensor = preprocessor.create_tensor_input(
        frame_data=frame_data,
        region=motion_regions[0].bbox,
        frame_shape=(640, 640)
    )
```

### **场景3：高性能需求**
```python
# 使用运动检测 + 区域裁剪
motion_regions = motion_detector.detect(frame)
if not motion_regions:
    continue  # 跳过检测

# 合并 + 扩展 + 限制
regions = MotionFilter.merge_overlapping_regions(motion_regions)
regions = MotionFilter.expand_regions(regions, frame_shape=(640, 640))
regions = MotionFilter.limit_region_count(regions, max_count=5)

# 批量检测
tensors = [preprocessor.create_tensor_input(frame_data, r.bbox, (640, 640)) 
           for r in regions]
detections = [detector.detect(t) for t in tensors]
```

---

## 📚 相关文档

- [运动检测模块文档](../motion/README.md)
- [预处理器文档](../detectors/preprocessor.py)
- [目标检测器文档](../detectors/yolo_detector.py)
- [性能优化指南](./performance_optimization.md)

---

## ⚠️ 注意事项

1. **运动检测器初始化时间**
   - MOG2 需要 30-500 帧预热
   - 建议在系统启动时预加载

2. **区域裁剪性能**
   - 小区域检测速度更快，但可能遗漏目标
   - 建议扩展区域边界 10-20%

3. **内存管理**
   - 预处理器使用预分配缓冲区
   - 注意及时释放临时对象

4. **线程安全**
   - 运动检测器不是线程安全的
   - 每个线程应创建独立的实例