# 区域检测功能实现方案

## Context

当前区域检测（Zone）存在两个问题：
1. **配置方式落后**：前端通过手动输入坐标文本框（如 `x1,y1;x2,y2;x3,y3`）配置多边形区域，用户体验极差
2. **架构不合理**：Zone 存储在任务级别（`detection_tasks.zones_config`），但 Zone 是每个摄像机通道独有的，应该按通道配置

本方案将：
- 改为通过摄像机快照 + Canvas 画布绘制多边形方式配置区域
- 将 Zone 配置从任务级迁移到通道级
- 移除任务配置中的 Zone 区域选项卡

---

## 一、后端改动

### 1.1 新增摄像机快照 API

**文件**: `src/tasks/task_api.py`

新增端点 `GET /api/channels/<int:channel_id>/snapshot`

实现方式：使用 OpenCV `VideoCapture` 从 RTSP 流截取一帧，编码为 JPEG 返回。

关键设计：
- 超时保护：用 `threading.Thread` + 5秒超时，避免 RTSP 连接阻塞
- 响应头返回快照分辨率 `X-Snapshot-Width` / `X-Snapshot-Height`
- 支持 `max_width` / `max_height` 查询参数控制缩放

### 1.2 run.py 注入通道级 zones_config

**文件**: `run.py` L152-159

在构建 `camera_config` 时从数据库读取通道级 Zone 配置并注入：

```python
# 获取通道级 Zone 配置
channel_zones = task_manager.get_channel_zones(task_id, channel.id)

camera_config = {
    'camera_id': effective_camera_id,
    'channel_id': str(channel.id),
    ...
    'zones_config': channel_zones,  # 新增
}
```

### 1.3 task_executor.py 改为从通道级读取

**文件**: `src/tasks/task_executor.py` L553

```python
# 改造前（任务级）
'zones_config': self.task_config.get('zones_config', []),

# 改造后（通道级）
'zones_config': camera.get('zones_config', []),
```

### 1.4 数据库迁移

**文件**: `src/tasks/task_manager.py`

确认 `task_device_channel_bindings` 表已有 `zones_config` 列（L678），无需额外迁移。

废弃任务级 `detection_tasks.zones_config` 字段：保留列不删除，但不再读写。

---

## 二、前端改动

### 2.1 新建 Canvas 编辑器

**新文件**: `static/js/zone_canvas_editor.js`

实现 `ZoneCanvasEditor` 类，核心功能：

| 操作 | 触发方式 | 行为 |
|------|----------|------|
| 添加顶点 | 左键点击画布 | 在当前多边形添加新顶点 |
| 完成多边形 | 点击第一个顶点 或 双击 | 闭合多边形，保存为区域 |
| 拖拽顶点 | 左键按住顶点拖动 | 移动顶点位置 |
| 删除顶点 | 右键点击顶点 | 删除该顶点（至少保留3个） |
| 选择区域 | 点击多边形内部 | 切换活跃区域，右侧显示属性 |

渲染层次（从下到上）：
1. 快照底图
2. 已完成多边形半透明填充（alpha=0.25）
3. 已完成多边形边框线
4. 顶点标记（圆形，活跃区域高亮）
5. 正在绘制中的虚线
6. 区域名称标签

坐标存储：使用**归一化坐标** [0-1] 范围，通过 `polygon_type: "normalized"` 标识。
转换：存储 `归一化x = 像素x / 快照宽度`，运行时还原 `像素x = 归一化x * 帧宽度`

### 2.2 重写 channel_zone_config.js

**文件**: `static/js/channel_zone_config.js`

改造 `openChannelZoneConfig()` 函数：
1. 先请求快照 API 获取摄像机截图
2. 将截图设为 Canvas 底图
3. 加载已有 Zone 配置（归一化坐标还原为像素坐标显示）
4. 保存时将像素坐标转为归一化坐标

### 2.3 改造模态框 HTML

**文件**: `templates/tasks.html`

将 `channelZoneConfigModal`（L314-344）改造为画布式布局：

```
+-------------------------------------------------------+
|  Zone区域配置 - [通道名称]                        [X]  |
+-------------------------------------------------------+
|                            |  区域列表                  |
|                            |  ☐ 入口区域  [删除]       |
|    Canvas 画布区域          |  ☐ 出口区域  [删除]       |
|    (摄像机快照底图)         |                           |
|                            |  + 添加区域               |
|                            |                           |
|                            |  区域属性                  |
|                            |  名称: [________]         |
|                            |  必需区域: [否]            |
|                            |  徘徊检测: [禁用]          |
|                            |  徘徊时间: [30]            |
+-------------------------------------------------------+
|  [刷新快照]                        [取消]  [保存配置]   |
+-------------------------------------------------------+
```

### 2.4 移除任务级 Zone 配置

**文件**: `templates/tasks.html`
- 删除 L365: `<div class="config-tab" data-tab="zones" ...>Zone区域</div>`
- 删除 L986-997: Zone 选项卡面板

**文件**: `static/js/config_modal.js`
- 删除 `zoneConfigCounter` 变量和所有 Zone 相关函数
- 删除 `saveConfig()` 中的 `zones_config: getZonesConfigFromForm()`

---

## 三、Zone 数据流（改造后）

```
前端 Canvas 绘制 → 归一化坐标 [0-1]
    ↓ POST /api/tasks/{id}/channels/{id}/zones
task_device_channel_bindings.zones_config (JSON)
    ↓ run.py 读取
camera_config['zones_config'] = channel_zones
    ↓ task_executor.py 注入
camera_configs_for_processor[cam_id]['zones_config']
    ↓ TrackedObjectProcessor 使用
ZoneManager 创建 Zone 实例（归一化→像素转换）
```

---

## 四、修改文件清单

| 文件 | 改动 |
|------|------|
| `src/tasks/task_api.py` | 新增快照API；移除任务级zones_config写入 |
| `run.py` | camera_config注入zones_config |
| `src/tasks/task_executor.py` | zones_config改为从camera读取 |
| `static/js/zone_canvas_editor.js` | **新建** Canvas多边形编辑器 |
| `static/js/channel_zone_config.js` | 重写为Canvas绘制方式 |
| `templates/tasks.html` | 改造Zone模态框HTML；移除任务级Zone选项卡 |
| `static/js/config_modal.js` | 移除任务级Zone相关代码 |

---

## 五、验证方式

1. 创建任务 → 添加通道 → 点击通道的Zone按钮 → 弹出画布编辑器
2. 点击"刷新快照" → 画布显示摄像机实时截图
3. 在画布上点击绘制多边形 → 右侧区域列表显示新区域
4. 设置区域属性（名称、必需区域、徘徊检测等）
5. 保存 → 检查数据库 `task_device_channel_bindings.zones_config` 存储了归一化坐标
6. 启动任务 → 确认 Zone 配置正确传递到检测引擎
7. 确认任务配置中不再有Zone选项卡
