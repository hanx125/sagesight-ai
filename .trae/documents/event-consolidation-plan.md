# 事件系统合并方案：3 种事件类型 → 单一跟踪事件状态模型

## Context

当前一个跟踪目标的生命周期产生 3 条独立事件记录（track_start / track_update / track_end），导致：
- 数据冗余：同一目标 3 条记录、3 次图片保存
- 状态碎片化：前端需从 3 个卡片拼凑一个目标的完整生命周期
- GIF 无法回填：GIF 异步生成后没有 update_event() 方法将 URL 写回原事件
- 缩略图浪费：track_update 仍触发图片保存和数据库写入

**目标**：改为单一"跟踪事件"+ 状态模型，track_start 创建事件，track_update/track_end 更新事件状态和图片，GIF 生成后回填到同一条事件记录。

---

## 实施步骤

### 第 1 步：数据库 Schema 变更

**文件**：`src/tasks/task_manager.py`

1. **EventRecord 新增 `status` 字段**
   - `type` 固定为 `"tracking"`，`status` 为状态（`tracking_start` / `tracking_update` / `tracking_end`）
   - 更新 `_row_to_event()` 解析新字段

2. **events 表 DDL 新增 `status` 列**
   ```sql
   status VARCHAR(20) DEFAULT 'tracking_start'
   ```

3. **数据库迁移**
   - 添加 `status` 列
   - 旧数据：`UPDATE events SET status = type WHERE type IN ('track_start','track_update','track_end')`
   - 旧数据：`UPDATE events SET type = 'tracking' WHERE type IN ('track_start','track_update','track_end')`

4. **新增 `update_event(event_id, updates)` 方法**
   - 支持部分字段更新（status / confidence / bbox / image_url / gif_url / end_time / metadata）
   - 返回 bool

5. **新增 `get_event_by_event_id(event_id)` 方法**
   - 通过 UUID 快速查询单条事件

6. **更新 `add_event()` INSERT 语句包含 `status` 列**

---

### 第 2 步：EventOutput 改造

**文件**：`src/events/event_output.py`

1. **新增 `update_event(event_id, status, data, image, metadata)` 方法**
   - 图片更新：仅当新图片置信度 > 旧图片时替换（从 best_thumbnail 获取最高置信度图片）
   - 更新数据库 + 推送 `event_updated` 到 WebSocket

2. **新增 `update_event_gif(event_id, gif_urls)` 方法**
   - GIF 生成完成后回填 gif_url 到数据库
   - 推送 `event_updated`（update_type='gif_ready'）到 WebSocket

3. **新增 `_push_event_update_to_websocket()` 方法**
   - POST 到 `/api/events/update`，Flask 广播 `event_updated` Socket 事件

4. **修改 `_save_to_database()`**
   - `EventRecord.type = 'tracking'`
   - `EventRecord.status = event['data'].get('status', 'tracking_start')`

5. **修改 `generate_gif()`**
   - GIF 保存后自动调用 `update_event_gif()` 回填数据库
   - 返回 gif_urls 字典

---

### 第 3 步：TrackedObjectProcessor 回调改造

**文件**：`src/events/tracked_object_processor.py`

1. **`_on_object_start()`**：调用 `emit("tracking", data={...status: "tracking_start"})` 创建事件

2. **`_on_object_update()`**：改为调用 `event_output.update_event(event_id, status="tracking_update", data, image)`，不再 emit

3. **`_on_object_end()`**：
   - 从 `get_best_thumbnail()` 获取最高置信度图片作为最终图片
   - 调用 `event_output.update_event(event_id, status="tracking_end", data, image=best_image)`
   - GIF 异步生成后自动回填（通过 generate_gif → update_event_gif）

4. **`_async_generate_gif()`**：generate_gif 现在自动回填 GIF URL，无需额外逻辑

---

### 第 4 步：WebSocket 推送 API 扩展

**文件**：`run.py`

1. **新增 `/api/events/update` POST 路由**
   - 接收 event_id + update_type + updates
   - 广播 `event_updated` Socket 事件

2. **更新 `PUBLIC_PATHS`**：添加 `'/api/events/update'`

3. **修改 `get_event_stats()`**：增加 `by_status` 分组统计

---

### 第 5 步：前端改造

**文件**：`templates/events.html`

1. **统计卡片**：改为"进行中"和"已结束"
2. **筛选器**：类型筛选改为状态筛选（tracking_start / tracking_update / tracking_end）
3. **类型文字映射**：`getTypeText('tracking') = '跟踪事件'`，新增 `getStatusText()` 映射
4. **CSS 类**：从 `event-type-*` 改为 `event-status-*`
5. **卡片渲染**：badge 显示状态而非类型
6. **WebSocket 实时更新**：监听 `event_updated`，更新已有卡片的 status/image/GIF
7. **事件详情**：type 显示"跟踪事件"，新增 status 行显示状态

**文件**：`static/js/main.js`

1. Socket 监听 `event_updated` → 显示通知

---

### 第 6 步：事件列表 API 筛选支持

**文件**：`src/tasks/task_manager.py` + `src/tasks/task_api.py`

- `get_events()` 增加 `type` 和 `status` 筛选参数
- 路由层传递查询参数

---

## 实施顺序

```
1(数据库) → 2(EventOutput) → 3(TrackedObjectProcessor) → 4(WebSocket) → 5+6(前端+API)
```

## 验证方法

1. 启动后端，开启一个检测任务
2. 确认：一个跟踪目标只产生 1 条事件记录（type=tracking）
3. 确认：事件状态从 tracking_start → tracking_update → tracking_end 更新
4. 确认：最终事件图片是最高置信度的帧
5. 确认：GIF 生成后回填到同一条事件记录
6. 确认：前端卡片实时更新状态 badge
7. 确认：事件详情中可切换查看图片和 GIF
8. 确认：筛选器按状态正确过滤
