# 区域检测 Zone 配置增强方案

## Context（背景）

用户要求实现区域检测zone的完整功能：
1. 对每个通道进行配置（**已实现**：`device_channel_pool.zones_config` 字段按通道存储）
2. 添加的区域，点击区域列表能够显示在快照图片（**有bug**：loadZones 坐标转换错误）
3. 前端AI播放时需要显示区域框（**未实现**：后端 `_draw_detections_on_frame` 没有绘制区域框）
4. 是否显示区域检测框需要添加到显示配置中（**未实现**：display_config 没有 show_zones 开关）

### 现状分析

**已正确实现的部分：**
- [zone_manager.py](file:///e:/sagesight-ai/src/detection/zone_manager.py) 的 ZoneManager/ZoneConfig 类，polygon 为像素坐标，有 `draw_zones()` 方法
- [task_api.py:981-1098](file:///e:/sagesight-ai/src/tasks/task_api.py#L981-L1098) 的 `/api/channels/{channel_id}/zones` GET/POST/DELETE API
- [task_manager.py:1547-1644](file:///e:/sagesight-ai/src/tasks/task_manager.py#L1547-L1644) 的 `get_channel_zones`/`save_channel_zones`/`delete_channel_zones` 方法
- [run.py:155-170](file:///e:/sagesight-ai/run.py#L155-L170) 的 `_run_executor_process` 已从数据库读取 `channel_zones` 放入 `camera_config['zones_config']`
- [task_executor.py:625](file:///e:/sagesight-ai/src/tasks/task_executor.py#L625) 已将 `zones_config` 传递给 TrackedObjectProcessor 的 `camera_configs`
- [channel_zone_config.js](file:///e:/sagesight-ai/static/js/channel_zone_config.js) 的通道区域配置UI（绘制、保存、加载）
- [zone_canvas_editor.js](file:///e:/sagesight-ai/static/js/zone_canvas_editor.js) 的 ZoneCanvasEditor 类（归一化坐标 [0-1] 存储）

**存在的问题：**

### 问题1：区域列表点击无法显示在快照图片（loadZones 坐标转换bug）

[zone_canvas_editor.js:236-270](file:///e:/sagesight-ai/static/js/zone_canvas_editor.js#L236-L270) 的 `loadZones` 方法中：

```javascript
// 当前代码（有bug）
const polygon = (zd.polygon || []).map(p => {
    if (zd.polygon_type === 'normalized') {
        return this._normalizedToCanvas(p[0], p[1]);  // ❌ 返回Canvas坐标 {x,y}
    }
    ...
});
this.zones.push({ ..., polygon: polygon, ... });  // 存储了Canvas坐标
```

但 `render()` 方法期望 `zone.polygon` 是归一化坐标：
```javascript
const canvasPoints = zone.polygon.map(p => this._normalizedToCanvas(p.x, p.y));  // 再次转换，坐标错误
```

`exportZones()` 也导出归一化坐标：
```javascript
polygon: z.polygon.map(p => [p.x, p.y]),  // 期望 {x,y} 是归一化坐标
```

**根因**：loadZones 把归一化坐标错误地转成了Canvas坐标存储，导致后续 render() 再次转换时坐标完全错误，区域无法正确显示。

### 问题2：AI播放时不显示区域框

[tracked_object_processor.py:818-933](file:///e:/sagesight-ai/src/events/tracked_object_processor.py#L818-L933) 的 `_draw_detections_on_frame` 方法绘制了运动区域、检测框、跟踪框、轨迹，但**没有绘制区域框（zones）**。

且该方法**没有接收 camera_id 参数**，无法从 `self.camera_configs` 读取对应通道的 zones_config。

### 问题3：显示配置没有"显示区域框"开关

[config_modal.js:236-243](file:///e:/sagesight-ai/static/js/config_modal.js#L236-L243) 的显示配置有 show_detection_boxes、show_track_boxes、show_trajectories、show_motion_regions，但**没有 show_zones**。

---

## 实现方案

### 1. 修复 loadZones 坐标转换bug

**[static/js/zone_canvas_editor.js](file:///e:/sagesight-ai/static/js/zone_canvas_editor.js#L236-L270)**

修改 `loadZones` 方法，归一化坐标直接存储为 `{x, y}`，不调用 `_normalizedToCanvas`：

```javascript
loadZones(zonesData) {
    this.zones = [];
    this._zoneIdCounter = 0;

    for (const zd of zonesData) {
        const id = ++this._zoneIdCounter;
        // 归一化坐标直接存储，render() 时再转为Canvas坐标
        const polygon = (zd.polygon || []).map(p => {
            if (zd.polygon_type === 'normalized') {
                return { x: p[0], y: p[1] };  // ✅ 直接存储归一化坐标
            }
            // 像素坐标：先归一化再存储
            return this._canvasToNormalized(
                p[0] * (this.canvas.width / this.snapshotWidth),
                p[1] * (this.canvas.height / this.snapshotHeight)
            );
        });

        this.zones.push({
            id: id,
            name: zd.name || `区域${id}`,
            polygon: polygon,  // ✅ 归一化坐标 {x: 0-1, y: 0-1}
            color: zd.color || this.colorPresets[...],
            required: zd.required || false,
            enable_loitering_detection: zd.enable_loitering_detection || false,
            loitering_time: zd.loitering_time || 30
        });
    }
    ...
}
```

### 2. 后端 - 在AI帧上绘制区域框

**[src/events/tracked_object_processor.py:818-933](file:///e:/sagesight-ai/src/events/tracked_object_processor.py#L818-L933)**

#### 2a. 修改 `_draw_detections_on_frame` 方法签名，添加 camera_id 参数

```python
def _draw_detections_on_frame(self, frame: np.ndarray, camera_id: str,
                                tracked_objects: dict, motion_boxes: list = None,
                                regions: list = None, detection_results: list = None) -> np.ndarray:
```

#### 2b. 在方法开头读取 show_zones 开关和 zones_config

```python
show_zones = display_cfg.get('show_zones', True)
cam_config = self.camera_configs.get(camera_id, {})
zones_config = cam_config.get('zones_config', [])
```

#### 2c. 在运动区域绘制之后、检测框绘制之前，添加区域框绘制逻辑

```python
# ========================================
# 绘制区域框（受 show_zones 管控）
# ========================================
if show_zones and zones_config:
    frame_h, frame_w = frame.shape[:2]
    for zone_cfg in zones_config:
        polygon_normalized = zone_cfg.get('polygon', [])
        if len(polygon_normalized) < 3:
            continue
        # 归一化坐标 [0-1] 转换为帧像素坐标
        pts = np.array([
            [int(p[0] * frame_w), int(p[1] * frame_h)]
            for p in polygon_normalized
        ], dtype=np.int32)
        color = tuple(zone_cfg.get('color', [255, 0, 0]))
        # 半透明填充
        overlay = frame_copy.copy()
        cv2.fillPoly(overlay, [pts], color)
        frame_copy = cv2.addWeighted(overlay, 0.2, frame_copy, 0.8, 0)
        # 边框
        cv2.polylines(frame_copy, [pts], True, color, 2)
        # 区域名称
        M = cv2.moments(pts)
        if M["m00"] != 0:
            cX = int(M["m10"] / M["m00"])
            cY = int(M["m01"] / M["m00"])
            cv2.putText(frame_copy, zone_cfg.get('name', ''), (cX - 20, cY),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
```

#### 2d. 修改 `_maybe_push_ai_stream` 中的调用，传入 camera_id

```python
frame_with_boxes = self._draw_detections_on_frame(
    frame, camera_id, tracked_objects, motion_boxes, regions, detection_results
)
```

### 3. 前端 - 显示配置添加"显示区域框"开关

**[static/js/config_modal.js](file:///e:/sagesight-ai/static/js/config_modal.js)**

#### 3a. loadConfigToForm 中添加 show_zones 读取

在 `displayShowMotionRegions` 之后添加：
```javascript
document.getElementById('displayShowZones').value = displayObj.show_zones !== false ? 'true' : 'false';
```

#### 3b. saveConfig 中添加 show_zones 保存

在 `show_motion_regions` 之后添加：
```javascript
show_zones: document.getElementById('displayShowZones').value === 'true',
```

**[templates/tasks.html](file:///e:/sagesight-ai/templates/tasks.html)**

#### 3c. 在显示配置选项卡中添加"显示区域框"开关

在 `displayShowMotionRegions` 的 form-group 之后添加：
```html
<div class="form-group">
    <label for="displayShowZones">显示区域框</label>
    <select id="displayShowZones">
        <option value="true">启用</option>
        <option value="false">禁用</option>
    </select>
</div>
```

---

## 涉及文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `static/js/zone_canvas_editor.js` | 修改 | 修复 loadZones 坐标转换bug |
| `src/events/tracked_object_processor.py` | 修改 | `_draw_detections_on_frame` 添加 camera_id 参数和区域框绘制逻辑 |
| `static/js/config_modal.js` | 修改 | loadConfigToForm 和 saveConfig 添加 show_zones |
| `templates/tasks.html` | 修改 | 显示配置选项卡添加"显示区域框"开关 |

---

## 验证方法

1. **区域列表点击显示验证：**
   - 打开通道Zone配置，添加几个区域并保存
   - 重新打开通道Zone配置，点击区域列表中的不同区域
   - 确认快照图片上对应区域高亮显示（半透明填充加深、边框加粗）

2. **AI播放区域框显示验证：**
   - 为通道配置区域并保存
   - 启动任务，在前端点击AI播放
   - 确认AI画面上显示区域框（半透明填充+边框+名称）
   - 在显示配置中关闭"显示区域框"，确认AI画面上区域框消失

3. **显示配置开关验证：**
   - 打开任务参数配置 → 显示配置选项卡
   - 确认有"显示区域框"开关
   - 切换开关并保存，重启任务后确认生效
