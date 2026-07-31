# 事件模型重构 + GIF 推送优化方案

## Context

当前事件系统存在四个核心问题：

1. **事件被拆分成三个状态**：[tracked_object_processor.py:455](file:///e:/sagesight-ai/src/events/tracked_object_processor.py#L455) 和 [517 行](file:///e:/sagesight-ai/src/events/tracked_object_processor.py#L517) 在 update/end 时调用 `update_event(status="tracking_update"/"tracking_end")`，而 [event_output.py:829](file:///e:/sagesight-ai/src/events/event_output.py#L829) `update_event` 把 status 写入数据库 `type` 字段，导致同一条记录的 type 在 start→update→end 间变化，前端按 type 查询时像是"三个事件"。

2. **update/end 不推送 WebSocket**：[event_output.py:826-845](file:///e:/sagesight-ai/src/events/event_output.py#L826-L845) `update_event` 只写数据库，不调用 `_push_event_to_websocket`。前端驾驶舱只在 `new_event`（start）时收到一次，之后状态变化和 GIF 完成都收不到。

3. **图片重复保存**：[_on_object_update:452-458](file:///e:/sagesight-ai/src/events/tracked_object_processor.py#L452-L458) 每次有 snapshot 都调用 `update_event(image=...)`，导致 update 过程中反复保存图片到本地/MinIO。用户希望只保存置信度最高的一张。

4. **GIF 生成后不推送**：[event_output.py:920-926](file:///e:/sagesight-ai/src/events/event_output.py#L920-L926) GIF 生成成功只更新数据库，前端无法实时收到 GIF。

**目标**：
- 单一事件模型：type 始终为 `tracking`，状态用 metadata.status（active/ended）记录
- 跟踪开始推送 `new_event`，结束推送 `event_update`，GIF 完成推送 `gif_ready`
- 图片只保存置信度最高的一张（end 时保存 best_thumbnail）
- GIF 优化（CPU/内存/文件大小）保留之前方案

## 实施方案

### 第1步：事件模型重构 — type 保持 `tracking`

**文件**：[src/events/event_output.py](file:///e:/sagesight-ai/src/events/event_output.py) `update_event` 方法（756-851 行）

修改点：
- 不再用 `status` 参数覆盖数据库 `type` 字段
- `type` 保持创建时的 `tracking`
- `status` 写入 `metadata.status`（active/ended）
- 新增 `_push_event_update_to_websocket()` 方法，在 end 时推送 `event_update`

```python
def update_event(self, event_id, status, data=None, image=None, push_websocket=False):
    # ... 图片处理（仅当 image 不为 None 时保存）...
    
    # ✅ 关键修改：type 保持 'tracking'，status 写入 metadata
    metadata = data.get('metadata') if data else None
    if metadata is None:
        metadata = {}
    metadata['status'] = status  # active / ended
    
    # 更新数据库（type 字段不再更新，保持 tracking）
    success = task_manager.update_event(
        event_id=event_id,
        event_type=None,  # ✅ None 表示不更新 type
        metadata=metadata,
        # ... 其他字段 ...
    )
    
    # ✅ 新增：end 时推送 event_update
    if push_websocket and success:
        self._push_event_update_to_websocket(event_id, status, data)
    
    return success

def _push_event_update_to_websocket(self, event_id, status, data):
    """推送事件状态更新到前端"""
    try:
        import requests
        requests.post(
            'http://127.0.0.1:5555/api/events/update',
            json={'event_id': event_id, 'status': status, 'data': data},
            timeout=2
        )
    except Exception as e:
        print(f"[EventOutput] 推送 event_update 失败: {e}")
```

**文件**：[src/tasks/task_manager.py](file:///e:/sagesight-ai/src/tasks/task_manager.py) `update_event` 方法

修改点：
- `event_type=None` 时不更新 type 字段（SQL 中排除 type 列）

### 第2步：触发逻辑重构 — 只在 end 时保存图片

**文件**：[src/events/tracked_object_processor.py](file:///e:/sagesight-ai/src/events/tracked_object_processor.py)

**`_on_object_update`（414-470 行）**：
- 移除 `update_event(image=...)` 调用
- 只调用 `update_thumbnail()` 更新内存最佳缩略图
- 不再保存图片到存储

```python
def _on_object_update(self, camera_id, obj_dict, frame_time):
    # ... 检查活跃事件 ...
    
    # ✅ 只更新内存最佳缩略图（不保存到存储）
    if image is not None:
        event_output.update_thumbnail(event_id, image, confidence, tuple(bbox))
    
    # ✅ 不再调用 update_event（避免重复保存图片）
    logger.debug(f"[{camera_id}] 跟踪更新: id={track_id}, confidence={confidence:.3f}")
```

**`_on_object_end`（472-520 行）**：
- 用 best_thumbnail 作为 final_image
- 调用 `update_event(status="ended", image=best_thumbnail, push_websocket=True)`
- 触发 GIF 生成

```python
def _on_object_end(self, camera_id, obj_dict, frame_time):
    # ... 获取 best_thumbnail ...
    
    # ✅ end 时保存置信度最高的图片 + 推送 event_update
    event_output.update_event(
        event_id,
        status="ended",
        data=update_data,
        image=final_image,        # 只保存这一张（置信度最高）
        push_websocket=True       # ✅ 推送 event_update
    )
    
    # ✅ 触发 GIF 生成（保持原有逻辑）
    if event_output.gif_enabled and self.gif_executor:
        # ... 从 frame_cache 取帧，提交 _async_generate_gif ...
```

### 第3步：GIF 生成完成后推送 gif_ready

**文件**：[src/events/event_output.py](file:///e:/sagesight-ai/src/events/event_output.py) `generate_gif` 方法（867-926 行）

在 [926 行](file:///e:/sagesight-ai/src/events/event_output.py#L926) `print(f"[EventOutput] GIF 生成成功...")` 后新增：

```python
def generate_gif(self, event_id, frames, bbox=None, trajectory_points=None):
    # ... 生成 GIF ...
    
    if gif_urls:
        # 更新数据库
        self.update_event(event_id, status="ended", data={'gif_urls': gif_urls})
        
        # ✅ 新增：推送 gif_ready
        self._push_gif_ready_to_websocket(event_id, gif_urls)
        
    return local_path

def _push_gif_ready_to_websocket(self, event_id, gif_urls):
    """推送 GIF 完成通知到前端"""
    try:
        import requests
        requests.post(
            'http://127.0.0.1:5555/api/events/gif_ready',
            json={
                'event_id': event_id,
                'local_gif_url': gif_urls.get('local', ''),
                'minio_gif_url': gif_urls.get('minio', ''),
                'ftp_gif_url': gif_urls.get('ftp', ''),
            },
            timeout=2
        )
    except Exception as e:
        print(f"[EventOutput] 推送 gif_ready 失败: {e}")
```

### 第4步：后端新增 WebSocket 推送接口

**文件**：[run.py](file:///e:/sagesight-ai/run.py)（在 `/api/events/push` 旁边新增两个接口）

```python
@app.route('/api/events/update', methods=['POST'])
def push_event_update_to_websocket():
    """推送事件状态更新（ended）到前端"""
    data = request.get_json()
    if not data or 'event_id' not in data:
        return jsonify({'error': '参数不完整'}), 400
    socketio.emit('event_update', data, namespace='/ws')
    logger.info(f"[Event] event_update 推送: event_id={data['event_id'][:8]}, status={data.get('status')}")
    return jsonify({'success': True})

@app.route('/api/events/gif_ready', methods=['POST'])
def push_gif_ready_to_websocket():
    """推送 GIF 完成通知到前端"""
    data = request.get_json()
    if not data or 'event_id' not in data:
        return jsonify({'error': '参数不完整'}), 400
    socketio.emit('gif_ready', data, namespace='/ws')
    logger.info(f"[GIF] gif_ready 推送: event_id={data['event_id'][:8]}")
    return jsonify({'success': True})
```

### 第5步：前端驾驶舱监听 event_update 和 gif_ready

**文件**：[templates/monitor.html](file:///e:/sagesight-ai/templates/monitor.html)

**5.1 新增 WebSocket 监听**（924 行 `new_event` 旁边）：

```javascript
eventWebSocket.on('new_event', (event) => {
    addNewEventToMonitor(event);  // 保持原有：status=active，无 GIF
});

// ✅ 新增：事件结束更新
eventWebSocket.on('event_update', (data) => {
    updateMonitorEventCard(data.event_id, data);
});

// ✅ 新增：GIF 完成更新
eventWebSocket.on('gif_ready', (data) => {
    updateMonitorEventGif(data.event_id, data);
});
```

**5.2 新增卡片更新函数**：

```javascript
// 更新已显示事件卡片的状态
function updateMonitorEventCard(eventId, data) {
    const card = document.querySelector(`[data-event-id="${eventId}"]`);
    if (!card) return;
    
    // 更新状态标识为"已结束"
    const statusBadge = card.querySelector('.event-status');
    if (statusBadge) {
        statusBadge.textContent = '已结束';
        statusBadge.className = 'event-status status-ended';
    }
    
    // 更新置信度（如果 data.data 中有）
    if (data.data && data.data.confidence) {
        const confEl = card.querySelector('.event-confidence');
        if (confEl) confEl.textContent = `置信度: ${(data.data.confidence * 100).toFixed(0)}%`;
    }
}

// 更新已显示事件卡片的 GIF
function updateMonitorEventGif(eventId, data) {
    const card = document.querySelector(`[data-event-id="${eventId}"]`);
    if (!card) return;
    
    const gifUrl = data.local_gif_url || data.minio_gif_url || data.ftp_gif_url;
    if (!gifUrl) return;
    
    // 添加 GIF 标识
    let gifBadge = card.querySelector('.gif-badge');
    if (!gifBadge) {
        gifBadge = document.createElement('div');
        gifBadge.className = 'gif-badge';
        gifBadge.innerHTML = '🎬 GIF';
        card.querySelector('.event-image-container').appendChild(gifBadge);
    }
    
    // 点击卡片时播放 GIF（在 showMonitorEventDetail 中已有 GIF 显示逻辑）
    card._gifUrl = gifUrl;
}
```

**5.3 卡片添加 data-event-id 属性**（[createMonitorEventCard:731](file:///e:/sagesight-ai/templates/monitor.html#L731)）：

```javascript
function createMonitorEventCard(event) {
    const card = document.createElement('div');
    card.className = 'monitor-event-card';
    card.dataset.eventId = event.event_id;  // ✅ 新增：用于查找卡片
    card.onclick = () => showMonitorEventDetail(event);
    // ... 其余保持原有 ...
}
```

**5.4 CSS 新增 GIF 标识样式**：

```css
.gif-badge {
    position: absolute;
    top: 4px;
    right: 4px;
    background: rgba(0, 0, 0, 0.7);
    color: #fff;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 3px;
    pointer-events: none;
}
.event-status.status-ended {
    background: #6c757d;
    color: #fff;
}
```

### 第6步：前端事件管理页面监听 gif_ready

**文件**：[templates/events.html](file:///e:/sagesight-ai/templates/events.html)

在 `<script>` 末尾新增 WebSocket 监听（当前 events.html 无 WebSocket，需新增）：

```javascript
// ✅ 新增：监听 gif_ready，实时更新 GIF 列
let eventGifSocket = null;
function initEventGifWebSocket() {
    eventGifSocket = io(`${window.location.protocol}//${window.location.hostname}:${window.location.port}/ws`, {
        transports: ['websocket', 'polling']
    });
    eventGifSocket.on('gif_ready', (data) => {
        const row = document.querySelector(`tr[data-event-id="${data.event_id}"]`);
        if (!row) return;
        const gifCell = row.querySelector('.gif-cell');
        const gifUrl = data.local_gif_url || data.minio_gif_url || data.ftp_gif_url;
        if (gifCell && gifUrl) {
            gifCell.innerHTML = `<img src="${gifUrl}" style="max-width:60px;cursor:pointer;" onclick="previewGif('${gifUrl}')" title="点击预览">`;
        }
    });
}
initEventGifWebSocket();
```

### 第7步：GIF 资源优化（保留之前方案）

**配置调整**（[config/event_output.yaml](file:///e:/sagesight-ai/config/event_output.yaml) gif 段）：
- `max_frames: 60 → 20`
- `fps: 10 → 8`
- `max_width: 320 → 240`
- `max_height: 240 → 180`
- `cache_fps: 10 → 5`
- `cache_retention: 20.0 → 10.0`

**FrameCache 存 JPEG 字节**（[src/events/frame_cache.py](file:///e:/sagesight-ai/src/events/frame_cache.py)）：
- `push()`：`cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 70])` 压缩存储
- `get_frames()`：`cv2.imdecode` 还原
- 内存收益：230KB/帧 → ~8KB/帧

**GIF 编码优化**（[src/events/gif_generator.py](file:///e:/sagesight-ai/src/events/gif_generator.py)）：
- 用 PIL `Image.quantize` 共用调色板替代 `imageio.mimsave`
- 文件收益：1-5MB → 100-300KB

## 实施顺序

1. **后端事件模型**：event_output.py + task_manager.py（type 保持 tracking）
2. **触发逻辑**：tracked_object_processor.py（update 不保存图片，end 推送）
3. **GIF 推送**：event_output.py + run.py（gif_ready 接口）
4. **前端驾驶舱**：monitor.html（event_update + gif_ready 监听）
5. **前端事件管理**：events.html（gif_ready 监听）
6. **GIF 资源优化**：event_output.yaml + frame_cache.py + gif_generator.py

## 验证

1. **重启服务**：`taskkill /F /IM python.exe; python run.py`
2. **触发事件**：等目标出现→消失，观察日志：
   - `[EventOutput] emit() 被调用: type=tracking` （start，推送 new_event）
   - `[CameraWorker] 跟踪更新: id=xxx` （update，不推送，不保存图片）
   - `[EventOutput] 事件更新成功: status=ended` （end，推送 event_update）
   - `[EventOutput] GIF 生成成功` → `[GIF] gif_ready 推送`
3. **数据库验证**：查询 events 表，type 应始终为 `tracking`，metadata.status 为 active/ended
4. **前端驾驶舱验证**：
   - 目标出现 → 卡片立即显示（status=active）
   - 目标消失 → 卡片状态更新为"已结束"
   - GIF 生成后 → 卡片出现 🎬 GIF 标识，点击可播放
5. **图片验证**：`output/images/` 下每个事件只有一张图片（置信度最高）
6. **GIF 文件**：`output/gifs/` 下文件 100-300KB
