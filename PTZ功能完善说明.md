# PTZ控制功能完善说明

## 功能概览

已完善海康威视风格云台控制功能接口，包括：
- 云台方向控制
- 变焦控制
- 预置点管理
- 巡航路径管理

## 已完成的工作

### 1. 后端API完善

#### 文件位置
- `src/cameras/ptz_api.py` - 云台控制API接口
- `src/cameras/ptz_service.py` - PTZ服务实现（修复hostname问题）
- `src/cameras/onvif_client.py` - ONVIF客户端

#### 主要功能
1. **云台控制** (`/api/ptz/control`)
   - 支持8个方向控制（上、下、左、右、左上、右上、左下、右下）
   - 支持速度调节（1-5级）
   - 支持开始/停止操作

2. **变焦控制** (`/api/ptz/zoom`)
   - 支持放大/缩小操作
   - 支持速度调节

3. **预置点管理**
   - `/api/ptz/preset/list` - 获取预置点列表
   - `/api/ptz/preset/goto` - 调用预置点
   - `/api/ptz/preset/set` - 设置预置点
   - `/api/ptz/preset/add` - 添加新预置点
   - `/api/ptz/preset/delete` - 删除预置点

4. **巡航路径管理**
   - `/api/ptz/patrol/list` - 获取巡航路径列表
   - `/api/ptz/patrol/start` - 开始巡航
   - `/api/ptz/patrol/stop` - 停止巡航
   - `/api/ptz/patrol/add` - 添加巡航路径
   - `/api/ptz/patrol/edit` - 编辑巡航路径
   - `/api/ptz/patrol/delete` - 删除巡航路径

### 2. 前端界面完善

#### 文件位置
- `static/js/monitor.js` - 前端交互逻辑
- `static/css/style.css` - 样式完善
- `templates/monitor.html` - HTML结构（已在之前完成）

#### 主要功能
1. **预置点列表加载**
   - 打开单画面播放对话框时自动加载预置点列表
   - 显示预置点名称和操作按钮
   - 支持空状态显示

2. **巡航路径列表加载**
   - 打开单画面播放对话框时自动加载巡航路径列表
   - 显示路径名称、预置点数量和操作按钮
   - 支持空状态显示

3. **操作后自动刷新**
   - 预置点操作后自动刷新列表
   - 巡航路径操作后自动刷新列表

### 3. 工具脚本

#### 创建的脚本
1. `check_ptz_capabilities.py` - 查询摄像机PTZ能力
2. `update_camera_ptz.py` - 更新数据库中的PTZ能力标志
3. `test_ptz_api.py` - 测试PTZ API功能

#### 使用方法
```bash
# 查询摄像机PTZ能力
python check_ptz_capabilities.py --camera-id <摄像机ID>

# 列出所有摄像机
python check_ptz_capabilities.py --list

# 更新摄像机PTZ能力标志
python update_camera_ptz.py

# 测试PTZ API
python test_ptz_api.py
```

## 测试结果

### 测试摄像机信息
- **名称**: 海康枪式摄像机
- **型号**: DS-2CD264YZUVA-XBCDEF
- **IP**: 192.168.5.201:80
- **制造商**: HIKVISION

### PTZ能力
| 功能 | 支持状态 |
|------|---------|
| 连续移动 | ✓ 支持 |
| 相对移动 | ✗ 不支持 |
| 绝对移动 | ✗ 不支持 |
| 变焦 | ✓ 支持 |
| 预置位 | ✓ 支持 |
| 回原点 | ✗ 不支持 |

### 测试结果
- ✓ PTZ控制器获取成功
- ✓ PTZ能力获取成功
- ✓ PTZ状态获取成功
- ✓ 预置位列表获取成功（1个预置位）
- ✓ 变焦操作成功
- ✓ 停止移动成功

## 使用说明

### 1. 前端使用
1. 在监控页面点击摄像机进入单画面播放
2. 左侧控制面板自动显示预置点和巡航路径列表
3. 点击控制面板上的按钮进行操作：
   - **预置点**: 调用、设置、删除、添加
   - **巡航路径**: 开始、停止、编辑、删除、添加

### 2. API调用示例

#### 云台控制
```javascript
// 开始向上移动
fetch('/api/ptz/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        camera_id: '摄像机ID',
        direction: 'up',
        speed: 3,
        action: 'start'
    })
});

// 停止移动
fetch('/api/ptz/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        camera_id: '摄像机ID',
        action: 'stop'
    })
});
```

#### 预置点管理
```javascript
// 获取预置点列表
fetch(`/api/ptz/preset/list?camera_id=${cameraId}`);

// 调用预置点
fetch('/api/ptz/preset/goto', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        camera_id: '摄像机ID',
        preset_id: '预置点Token'
    })
});

// 添加预置点
fetch('/api/ptz/preset/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        camera_id: '摄像机ID',
        name: '预置点名称'
    })
});
```

## 技术要点

### 1. ONVIF PTZ服务集成
- 使用ONVIF协议连接摄像机
- 自动检测PTZ能力（连续移动、相对移动、绝对移动、变焦、预置位等）
- 根据设备能力选择合适的控制方式

### 2. 数据库更新
- 自动检测摄像机的PTZ支持能力
- 更新数据库中的`support_move`和`support_zoom`字段
- 避免在不支持PTZ的摄像机上执行操作

### 3. 容错处理
- 处理ONVIF响应的不同格式
- 处理设备不支持PTZ的情况
- 处理网络连接失败的情况

## 后续改进建议

1. **巡航路径执行引擎**
   - 实现后台任务，按顺序执行预置点
   - 支持停留时间控制
   - 支持循环巡航

2. **数据库存储**
   - 将巡航路径配置存储到数据库
   - 支持持久化存储预置点信息

3. **成像服务集成**
   - 实现聚焦控制（通过成像服务）
   - 实现光圈控制（通过成像服务）

4. **WebSocket实时推送**
   - 实时推送PTZ状态变化
   - 实时推送预置点变化
   - 实时推送巡航状态

## 相关文件

### 后端文件
- [ptz_api.py](file:///e:/sagesight-AI/src/cameras/ptz_api.py)
- [ptz_service.py](file:///e:/sagesight-AI/src/cameras/ptz_service.py)
- [onvif_client.py](file:///e:/sagesight-AI/src/cameras/onvif_client.py)
- [camera_manager.py](file:///e:/sagesight-AI/src/cameras/camera_manager.py)

### 前端文件
- [monitor.js](file:///e:/sagesight-AI/static/js/monitor.js)
- [style.css](file:///e:/sagesight-AI/static/css/style.css)
- [monitor.html](file:///e:/sagesight-AI/templates/monitor.html)

### 工具脚本
- [check_ptz_capabilities.py](file:///e:/sagesight-AI/check_ptz_capabilities.py)
- [update_camera_ptz.py](file:///e:/sagesight-AI/update_camera_ptz.py)
- [test_ptz_api.py](file:///e:/sagesight-AI/test_ptz_api.py)