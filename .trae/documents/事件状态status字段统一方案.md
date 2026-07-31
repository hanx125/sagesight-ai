# 事件状态 status 字段统一方案

## Context（背景与问题）

事件状态(status)目前在系统中存在 **3 处互不一致的来源**，导致前端不知道以哪个为准、状态显示混乱：

| 层 | status 存在哪 | 是否真正使用 |
|---|---|---|
| 数据库 `status` 列 | 独立 `VARCHAR(20)` 列（旧迁移遗留） | ❌ 孤儿列，从不写入/读取 |
| 数据库 `metadata.status` | JSON 字段里的 `status` 键 | ✅ 后端实际读写来源 |
| `new_event` WebSocket 推送 | 完全没有 status 字段 | ❌ 前端新建卡片无状态 |
| `event_update` WebSocket 推送 | 顶层 `status` 字段 | ✅ 推送了 |
| 前端 monitor.html | 读 `data.status` | ⚠️ 硬编码成"已结束"，忽略实际值 |

实测数据库 `tasks.db`：`status` 列全是默认值 `tracking_start`，真实状态只在 `metadata.status`（如 `{"status":"tracking_end"}`）。

**目标**：根据用户偏好（独立 DB 字段优于嵌套 JSON、命名全系统一致、显式列选择），统一以**独立 `status` 列为唯一真相源**，废弃 `metadata.status`，并让前端正确显示 4 种状态（tracking_start / tracking_update / tracking_end / gif_ready）。

状态状态机：`tracking_start` → `tracking_update` → `tracking_end` → `gif_ready`

## 改动顺序（按依赖关系）

1. `src/tasks/task_manager.py`（数据库层 + 数据类，建立单一真相源）
2. `src/events/event_output.py`（业务层，依赖 task_manager 新接口）
3. `templates/monitor.html`（驾驶舱前端）
4. `templates/events.html`（事件管理前端）
5. `check_events.py`（调试工具跟随）

API 层（`task_api.py` / `run.py` 路由）无需改动 —— 它们透传 `to_dict()` 和 socketio payload，status 加入后自动生效。

---

## 1. `src/tasks/task_manager.py`

### 1.1 CREATE TABLE events（约 L742-L764）
在 `end_time DATETIME,` 之后、`created_at` 之前加入：
```sql
status VARCHAR(20) DEFAULT 'tracking_start',
```

### 1.2 数据库迁移（L766-L787 的 start_time/end_time 迁移块之后追加）
三段式幂等迁移（每段独立 try/except，互不影响）：
- **建列**：`if 'status' not in columns: ALTER TABLE events ADD COLUMN status VARCHAR(20) DEFAULT 'tracking_start'`
- **回填**：`UPDATE events SET status = json_extract(metadata, '$.status') WHERE json_extract(metadata,'$.status') IS NOT NULL`；旧事件无 status 的兜底为 `tracking_end`
- **清理**：遍历 `metadata LIKE '%status%'` 的行，Python 解析后 `meta.pop('status')` 写回，统一到独立列

### 1.3 EventRecord 数据类（约 L564-L617）
- 新增字段 `status: str = "tracking_start"`（放在 `end_time` 后）
- `to_dict()` 顶层返回 `'status': self.status`

### 1.4 add_event（约 L1102-L1136）
INSERT 列与 VALUES 加入 `status`（参数从 18 个变 19 个），值取 `event.status`。

### 1.5 update_event（约 L1138-L1238）
- 签名新增 `status: str = None` 参数
- 更新逻辑加：`if status is not None: updates.append('status = ?'); params.append(status)`
- metadata 合并逻辑保持不变（上层不再往 metadata 塞 status，metadata 仅承载 track_id/center 等其他元数据）

### 1.6 get_events SELECT（约 L1253-L1256）
列清单加入 `status`（放在 `end_time` 后、`created_at` 前）。

### 1.7 _row_to_event（约 L1318-L1367）
新列顺序：16=start_time, 17=end_time, **18=status**, 19=created_at。
- `created_at = parse_datetime(row[19])`（从 18 改 19）
- `EventRecord(..., status=row[18] or 'tracking_start', ...)`

---

## 2. `src/events/event_output.py`

### 2.1 _save_to_database（约 L615-L660）
EventRecord 构造：
- `metadata=event.get('metadata', {})`（不再塞 status）
- 新增顶层 `status=event['data'].get('status', 'tracking_start')`

### 2.2 update_event（约 L756-L865）
- **删除** L815-L821 的 `metadata['status'] = status` 块
- task_manager.update_event 调用新增 `status=status` 顶层参数
- 文档字符串改为"写入独立 status 列"

### 2.3 generate_gif（约 L907-L978）
L960-L964 调用改为：
```python
task_manager.update_event(event_id=event_id, gif_urls=gif_urls, status='gif_ready')
```
（从 `metadata={'status': 'gif_ready'}` 改为顶层参数）

### 2.4 _push_event_to_websocket（new_event，约 L683-L715）
event_data 字典新增 `'status': event_record.status`（让前端新建卡片即知初始状态）。

`_push_event_update_to_websocket`（约 L867-L891）已含顶层 status，无需改动。

---

## 3. `templates/monitor.html`

### 3.1 新增统一状态映射函数（紧邻 formatEventType，约 L1101）
```javascript
function getStatusDisplay(status) {
    const m = {
        'tracking_start':  { text: '跟踪开始', className: 'status-start' },
        'tracking_update': { text: '跟踪更新', className: 'status-update' },
        'tracking_end':    { text: '已结束',   className: 'status-ended' },
        'gif_ready':       { text: 'GIF就绪',  className: 'status-gif' }
    };
    return m[status] || { text: status || '未知', className: 'status-unknown' };
}
```

### 3.2 CSS（扩展现有 `.event-status` 块）
4 种状态色：开始=蓝、更新=紫、结束=灰（保留原样式）、GIF=绿。

### 3.3 createMonitorEventCard（约 L730-L784）
infoContainer.innerHTML 新增一行状态徽章：`<span class="event-status ${statusInfo.className}">${statusInfo.text}</span>`（用 getStatusDisplay(event.status)）。

### 3.4 updateMonitorEventCard（约 L1008-L1047）
**删除硬编码"已结束"**，改用 `getStatusDisplay(data.status)` 设置文案与样式；内存对象同步 `monitorEvents[idx].status = data.status`。

### 3.5 updateMonitorEventGif（约 L1050 起，增强）
GIF 就绪时把状态徽章刷为"GIF就绪"（绿色），并同步内存 status='gif_ready'。

---

## 4. `templates/events.html`

### 4.1 复制 getStatusDisplay 函数 + CSS（与 monitor.html 一致）

### 4.2 表格视图加状态列
- 表头（L131-L137 之间）`<th>事件类型</th>` 后加 `<th>状态</th>`
- **修正已有 bug**：L723 `colspan="8"` 改为 `10`（实际 9 列 + 新增 1 = 10）
- renderTableView（约 L735-L751）对应位置加 `<td><span class="event-status ${statusInfo.className}">${statusInfo.text}</span></td>`

### 4.3 卡片视图 + 详情弹窗
- createEventCard header 加状态徽章
- 详情弹窗加"事件状态"行并在 openEventDetail 填充

### 4.4 新增 event_update 监听（L1113 起的 initEventGifWebSocket 内）
当前只处理 gif_ready，补全 event_update：更新内存 status + 局部刷新该行/卡片状态徽章。

---

## 5. `check_events.py`
查询改为读顶层 `status` 列，打印 `metadata.keys()` 确认不再含 status。

---

## 验证方案

### 数据库迁移验证
1. 备份 `data/tasks.db` → `tasks.db.bak`
2. 重启服务，观察控制台迁移日志（建列/回填N条/清理N条）
3. SQL 核对：
   - `SELECT status, COUNT(*) FROM events GROUP BY status`（应有 tracking_end/gif_ready 等分布，不再全是 tracking_start）
   - `SELECT id FROM events WHERE metadata LIKE '%"status"%' LIMIT 5`（期望 0 行）
4. 运行 `python check_events.py`

### 端到端生命周期验证
触发一次跟踪事件，对照下表核对每阶段：

| 阶段 | DB status 列 | metadata | monitor 徽章 |
|------|------------|----------|-------------|
| 跟踪开始 | tracking_start | 不含status | 蓝"跟踪开始" |
| 跟踪更新 | tracking_update | 不含status | 紫"跟踪更新" |
| 跟踪结束 | tracking_end | 不含status | 灰"已结束" |
| GIF完成 | gif_ready | 不含status | 绿"GIF就绪" |

每阶段实时核对：`SELECT status, metadata FROM events WHERE event_id='<uuid>'`

### 前端验证
- monitor.html：卡片状态徽章随状态实时变色变文案
- events.html：列表/卡片显示状态列，详情弹窗有"事件状态"行
- `GET /api/events`：JSON 每事件有顶层 status，metadata 不含 status

### 回滚
迁移幂等非破坏（先回填再清理，三段独立 try/except）。失败时还原 `tasks.db.bak` + git revert。

## 向后兼容
- 全新部署：CREATE TABLE 直接含 status 列
- 老部署（孤儿列+metadata.status）：迁移回填+清理
- 旧 SQLite 无 json_extract：Python 兜底
- to_dict 新增顶层 status，旧消费者忽略即可
