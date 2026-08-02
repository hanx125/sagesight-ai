/**
 * SageSight AI - 监控墙页面 JavaScript
 */

// ========================================
// 全局变量
// ========================================

let cameras = [];
let directories = [];
let currentLayout = 1;
let screens = [];
let selectedScreenIndex = 0;
let currentSingleCamera = null;
let voiceTalkActive = false;
let mediaRecorder = null;
let recordedChunks = [];
let snapshotData = null;
let expandedDirs = new Set(); // 记录展开的目录ID
let ptzSpeed = 3;  // 云台速度（1-5）
let ptzMoving = false;  // 云台是否正在移动
let streamStatusInterval = null;  // 流状态更新定时器

// AI 视频流相关
let aiStreamMode = {};  // {screenIndex: true/false} 记录每个画面是否使用 AI 模式
let aiStreamCanvas = {};  // {screenIndex: canvasElement} AI 流 Canvas 元素
let singleScreenAIMode = false;  // 单画面是否使用 AI 模式
let aiStreamSubscriptions = {};  // {cameraId: {screenIndexes: Set, image: Image}} AI 流订阅记录
let aiFrameCanvases = {};  // {cameraId: canvasElement} 单画面 AI Canvas

// AI 流渲染合并：每帧 WS 消息只缓存最新帧，requestAnimationFrame 合批渲染
// （浏览器刷新率 60Hz 已天然限流，无需全局限流丢帧）
let _pendingAIFrames = {};   // {cameraId: frameBase64} 待渲染的最新帧
let _rafScheduled = false;   // 是否已安排 rAF 任务

// 统计信息
let aiStats = {
    totalFrames: 0,
    droppedFrames: 0,
    renderedFrames: 0
};

// ========================================
// 页面初始化
// ========================================

document.addEventListener('DOMContentLoaded', function() {
    loadCameras();
    setLayout(1);
    initWebSocket();
    initDragDrop();
    fetchAlarmConfig();
    loadAlarmConfigCache();
});

// ========================================
// 屏幕闪烁报警（进入区域红色 / 徘徊橙色）
// ========================================

// 报警配置：全局兜底值（从 display_config.yaml 的 alarm 段拉取）
// 真正生效的是每任务独立配置（见 _alarmConfigByTask），此处仅作兜底
let _alarmConfig = {
    zone_enter_enabled: true,
    loitering_enabled: true,
    duration: 3,
    blink_interval: 500,
    show_banner: true,
    throttle_interval: 3000
};

// 每任务报警配置缓存：{task_id: alarm 配置}
// 单一真相源为任务表 display_config.alarm，此处仅缓存运行中任务的配置
let _alarmConfigByTask = {};

// 报警运行时状态
let _alarmBlinkTimer = null;     // 闪烁切换定时器
let _alarmStopTimer = null;     // 闪烁停止定时器
let _alarmLastTrigger = {};     // {type: timestamp} 节流记录

// 归一化 alarm 配置（补全缺失字段为默认值）
function normalizeAlarmConfig(alarm) {
    alarm = alarm || {};
    return {
        zone_enter_enabled: alarm.zone_enter_enabled !== undefined ? alarm.zone_enter_enabled : true,
        loitering_enabled: alarm.loitering_enabled !== undefined ? alarm.loitering_enabled : true,
        duration: alarm.duration !== undefined ? alarm.duration : 3,
        blink_interval: alarm.blink_interval !== undefined ? alarm.blink_interval : 500,
        show_banner: alarm.show_banner !== undefined ? alarm.show_banner : true,
        throttle_interval: alarm.throttle_interval !== undefined ? alarm.throttle_interval : 3000
    };
}

// 拉取全局报警配置（兜底用，配置文件为默认值来源）
async function fetchAlarmConfig() {
    try {
        const resp = await fetch('/api/display/config');
        const cfg = await resp.json();
        _alarmConfig = normalizeAlarmConfig(cfg.alarm || {});
    } catch (e) {
        console.warn('[Alarm] 拉取全局报警配置失败，使用默认值:', e);
    }
}

// 加载运行中任务的报警配置缓存（每任务独立，单一真相源为任务表 display_config.alarm）
async function loadAlarmConfigCache() {
    try {
        const resp = await fetch('/api/tasks?status=running&per_page=1000');
        const data = await resp.json();
        const tasks = (data && data.tasks) || [];
        const cache = {};
        for (const t of tasks) {
            const alarm = t.display_config && t.display_config.alarm;
            if (alarm) {
                cache[t.id] = normalizeAlarmConfig(alarm);
            }
        }
        _alarmConfigByTask = cache;
    } catch (e) {
        console.warn('[Alarm] 加载任务报警配置缓存失败，使用全局兜底:', e);
    }
}

// 获取生效的报警配置：优先每任务独立配置，缺失时回退全局兜底
function getAlarmConfig(taskId) {
    if (taskId && _alarmConfigByTask[taskId]) {
        return _alarmConfigByTask[taskId];
    }
    return _alarmConfig;
}

// 摄像机 ID → 名称映射（用于横幅显示）
function _cameraDisplayName(cameraId) {
    if (!cameraId) return '';
    const cam = (typeof cameras !== 'undefined' ? cameras : []).find(c => c.id === cameraId);
    return (cam && (cam.name || cam.camera_name)) || cameraId;
}

// 触发屏幕闪烁报警
// type: 'zone_enter'（红色） 或 'loitering'（橙色）
// eventData: new_event/event_update payload（需含 task_id 以便按任务读取报警配置）
function triggerAlarm(type, eventData) {
    // 按任务读取报警配置（每任务独立，缺失时回退全局兜底）
    const alarmCfg = getAlarmConfig(eventData && eventData.task_id);
    const enabled = (type === 'zone_enter') ? alarmCfg.zone_enter_enabled : alarmCfg.loitering_enabled;
    if (!enabled) return;

    // 节流：同类型报警在 throttle_interval 内不重复触发
    const now = Date.now();
    const last = _alarmLastTrigger[type] || 0;
    if (now - last < alarmCfg.throttle_interval) return;
    _alarmLastTrigger[type] = now;

    const overlay = document.getElementById('alarmOverlay');
    const banner = document.getElementById('alarmBanner');
    const bannerText = document.getElementById('alarmBannerText');
    const cameraName = _cameraDisplayName(eventData && eventData.camera_id);

    // 清除上一次闪烁定时器（避免叠加）
    if (_alarmBlinkTimer) { clearInterval(_alarmBlinkTimer); _alarmBlinkTimer = null; }
    if (_alarmStopTimer) { clearTimeout(_alarmStopTimer); _alarmStopTimer = null; }

    // 设置报警类型样式（红/橙）
    overlay.className = 'alarm-overlay alarm-' + type;
    banner.className = 'alarm-banner alarm-' + type;

    // 横幅文本
    if (alarmCfg.show_banner && bannerText) {
        const zones = (type === 'zone_enter'
            ? (eventData.entered_zones || [])
            : (eventData.loitered_zones || []));
        const zoneText = zones.length ? zones.join('、') : '未知区域';
        const label = (type === 'zone_enter') ? '区域入侵' : '徘徊报警';
        bannerText.textContent = `${label}：${zoneText}${cameraName ? '（' + cameraName + '）' : ''}`;
        banner.style.display = 'flex';
    }

    // 闪烁切换：每 blink_interval 毫秒切换 .active 类
    let visible = true;
    overlay.classList.add('active');
    _alarmBlinkTimer = setInterval(() => {
        visible = !visible;
        overlay.classList.toggle('active', visible);
    }, alarmCfg.blink_interval);

    // duration 秒后停止闪烁并隐藏横幅
    _alarmStopTimer = setTimeout(() => {
        if (_alarmBlinkTimer) { clearInterval(_alarmBlinkTimer); _alarmBlinkTimer = null; }
        overlay.classList.remove('active');
        banner.style.display = 'none';
    }, alarmCfg.duration * 1000);
}

// ========================================
// WebSocket 初始化
// ========================================

function initWebSocket() {
    // 使用全局 socket 连接（由 main.js 初始化）
    const socket = getSocket();

    if (socket) {
        socket.on('camera_created', function(data) {
            loadCameras();
        });
        
        socket.on('camera_updated', function(data) {
            loadCameras();
        });
        
        socket.on('camera_deleted', function(data) {
            loadCameras();
            // 如果删除的摄像机正在播放，清除对应画面
            screens.forEach((screen, index) => {
                if (screen && screen.camera_id === data.camera_id) {
                    clearScreen(index);
                }
            });
        });
        
        socket.on('camera_status_changed', function(data) {
            updateCameraStatus(data.camera_id, data.status);
        });
        
        // ========================================
        // AI 视频流帧监听（后端画框，前端只显示）
        // ========================================
        // 后端在 ai_stream_enabled_state[camera_id]=True 时推送带检测框的 JPEG 帧
        // 前端只缓存最新帧，requestAnimationFrame 合批渲染（浏览器刷新率天然限流，
        // 不再使用全局限流丢帧，保证最新帧总能及时显示）
        socket.on('ai_stream_frame', function(data) {
            const {camera_id, frame} = data;

            // 检查是否订阅了AI流（避免接收不需要的流）
            if (!aiStreamSubscriptions[camera_id] || aiStreamSubscriptions[camera_id].screenIndexes.size === 0) {
                return;
            }

            // 性能监控
            if (window.PerfMonitor) {
                PerfMonitor.monitorWebSocket('ai_stream_frame', data);
            }

            aiStats.totalFrames++;

            // 只缓存最新帧（同一 camera 多帧到达时旧的直接被覆盖，等价于丢旧帧）
            _pendingAIFrames[camera_id] = frame;

            // 安排一次 rAF 渲染（已安排则跳过，保证一帧只渲染一次）
            if (!_rafScheduled) {
                _rafScheduled = true;
                requestAnimationFrame(_flushPendingAIFrames);
            }
        });

        // 监听任务启动事件：任务重启后自动恢复AI流
        // 注意：任务停止时已清理 aiStreamMode 和订阅状态，但保留了 screen.ai_mode
        //       此处根据 screen.ai_mode 重新创建 Canvas、订阅并启用 AI 流
        socket.on('task_started', function(data) {
            console.log('[WebSocket] 收到task_started事件:', data);

            // 刷新每任务报警配置缓存（任务可能新建/编辑后重启，alarm 配置已更新）
            loadAlarmConfigCache();

            let hasAIStreamPlaying = false;

            // 电视墙模式：对之前处于 AI 模式的画面，重新创建 Canvas 并启用 AI 流
            screens.forEach((screen, index) => {
                if (screen && screen.ai_mode && screen.camera) {
                    hasAIStreamPlaying = true;
                    aiStreamMode[index] = true;
                    playWithAIStream(index, screen.camera);
                    console.log(`[任务重启] 画面${index} 恢复AI流: camera_id=${screen.camera_id}`);
                }
            });

            // 单画面模式
            if (currentSingleCamera && currentSingleCamera._aiWasMode) {
                hasAIStreamPlaying = true;
                singleScreenAIMode = true;
                currentSingleCamera._aiWasMode = false;
                playSingleWithAIStream(currentSingleCamera);
                updateSingleAIModeButton(true);
                console.log(`[任务重启] 单画面恢复AI流: camera_id=${currentSingleCamera.id}`);
            }

            if (hasAIStreamPlaying) {
                showNotification('任务已启动，AI视频流正在恢复', 'success');
            }
        });

        // 监听任务停止事件：清理 AI 流状态，确保任务重启后状态一致
        // 清理内容：通知后端停止推流 + 清理前端订阅 + 重置 aiStreamMode
        // 保留内容：screen.ai_mode（用于任务重启后自动恢复 AI 流）
        socket.on('task_stopped', function(data) {
            console.log('[WebSocket] 收到task_stopped事件:', data);

            // 刷新每任务报警配置缓存（任务停止后不再产生该任务的报警事件）
            loadAlarmConfigCache();

            // 电视墙模式：清理 AI 流状态
            screens.forEach((screen, index) => {
                if (screen && screen.camera_id && aiStreamMode[index]) {
                    disableAIStream(screen.camera_id);
                    unsubscribeAIStream(screen.camera_id, index);
                    aiStreamMode[index] = false;
                    console.log(`[任务停止] 清理画面${index} AI流: camera_id=${screen.camera_id}`);
                }
            });

            // 单画面模式：清理 AI 流状态，记录之前的 AI 模式以便恢复
            if (currentSingleCamera && singleScreenAIMode) {
                currentSingleCamera._aiWasMode = true;
                disableAIStream(currentSingleCamera.id);
                unsubscribeAIStream(currentSingleCamera.id, 'single');
                singleScreenAIMode = false;
                console.log(`[任务停止] 清理单画面 AI流: camera_id=${currentSingleCamera.id}`);
            }
        });

        // ========================================
        // 区域检测屏幕闪烁报警
        // ========================================
        // new_event（跟踪开始）：目标进入区域 → 红闪；徘徊 → 橙闪
        // 两者同时存在时红闪优先（区域入侵更严重）
        socket.on('new_event', function(event) {
            try {
                const enteredZones = (event.entered_zones || []);
                const loiteredZones = (event.loitered_zones || []);

                if (enteredZones.length > 0) {
                    triggerAlarm('zone_enter', event);
                }
                if (loiteredZones.length > 0) {
                    triggerAlarm('loitering', event);
                }
            } catch (e) {
                console.warn('[Alarm] new_event 处理失败:', e);
            }
        });

        // event_update（跟踪结束）：徘徊区域非空时补一次橙闪提示
        socket.on('event_update', function(data) {
            try {
                const loiteredZones = (data.loitered_zones || []);
                if (loiteredZones.length > 0 && data.status === 'tracking_end') {
                    triggerAlarm('loitering', data);
                }
            } catch (e) {
                console.warn('[Alarm] event_update 处理失败:', e);
            }
        });
    }
}

// ========================================
// 加载摄像机列表和目录树
// ========================================

async function loadCameras() {
    try {
        // 并行加载摄像机和目录数据
        const [camerasRes, dirsRes] = await Promise.all([
            fetch('/api/cameras?per_page=1000'),
            fetch('/api/directories')
        ]);
        
        const camerasData = await camerasRes.json();
        cameras = camerasData.cameras || camerasData || [];
        const dirsData = await dirsRes.json();
        
        if (dirsData.success) {
            directories = dirsData.directories;
        }
        
        renderCameraTree();
        updateCameraStats();
        
    } catch (error) {
        console.error('加载摄像机失败:', error);
    }
}

// ========================================
// 渲染摄像机目录树
// ========================================

function renderCameraTree() {
    const cameraTree = document.getElementById('cameraTree');
    
    if (directories.length === 0 && cameras.length === 0) {
        cameraTree.innerHTML = '<div class="empty-tree">暂无摄像机</div>';
        return;
    }
    
    // 构建目录树结构
    function buildDirTree(parentId = null) {
        return directories
            .filter(d => d.parent_id === parentId)
            .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))
            .map(dir => {
                const children = buildDirTree(dir.id);
                const dirCameras = cameras.filter(c => c.directory_id === dir.id);
                return { ...dir, children, cameras: dirCameras };
            });
    }
    
    const tree = buildDirTree();
    
    // 未分配到目录的摄像机
    const unassignedCameras = cameras.filter(c => !c.directory_id || !directories.find(d => d.id === c.directory_id));
    
    let html = '';
    
    // 渲染目录树
    tree.forEach(dir => {
        html += renderDirNode(dir, 0);
    });
    
    // 渲染未分配的摄像机
    if (unassignedCameras.length > 0) {
        html += `
            <div class="dir-node">
                <div class="dir-header" onclick="toggleDirNode(this)">
                    <svg class="dir-arrow" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/>
                    </svg>
                    <svg class="dir-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/>
                    </svg>
                    <span class="dir-name">默认分组</span>
                    <span class="dir-count">${unassignedCameras.length}</span>
                </div>
                <div class="dir-children" style="display: none;">
                    ${unassignedCameras.map(c => renderCameraItem(c, 1)).join('')}
                </div>
            </div>
        `;
    }
    
    cameraTree.innerHTML = html;
}

function renderDirNode(dir, depth) {
    const isExpanded = expandedDirs.has(dir.id);
    const totalCameras = dir.cameras.length + countDirCameras(dir.children);
    
    let html = `
        <div class="dir-node" data-dir-id="${dir.id}">
            <div class="dir-header" onclick="toggleDirNode(this)" style="padding-left: ${12 + depth * 16}px;">
                <svg class="dir-arrow ${isExpanded ? 'expanded' : ''}" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/>
                </svg>
                <svg class="dir-icon" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/>
                </svg>
                <span class="dir-name">${escapeHtml(dir.name)}</span>
                <span class="dir-count">${totalCameras}</span>
            </div>
            <div class="dir-children" style="display: ${isExpanded ? 'block' : 'none'};">
    `;
    
    // 渲染子目录
    dir.children.forEach(child => {
        html += renderDirNode(child, depth + 1);
    });
    
    // 渲染该目录下的摄像机
    dir.cameras.forEach(cam => {
        html += renderCameraItem(cam, depth + 1);
    });
    
    html += `</div></div>`;
    return html;
}

function renderCameraItem(camera, depth) {
    const statusClass = camera.status === 'online' ? 'online' : 'offline';
    const statusIcon = camera.status === 'online' ? 
        '<svg viewBox="0 0 24 24" fill="currentColor" class="status-icon online"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>' :
        '<svg viewBox="0 0 24 24" fill="currentColor" class="status-icon offline"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>';
    
    return `
        <div class="camera-item ${statusClass}" 
             data-camera-id="${camera.id}" 
             draggable="true"
             ondragstart="handleDragStart(event, '${camera.id}')"
             style="padding-left: ${12 + depth * 16 + 28}px;">
            ${statusIcon}
            <div class="camera-item-info">
                <span class="camera-item-name">${escapeHtml(camera.name)}</span>
            </div>
            <div class="camera-item-actions">
                <button class="camera-item-btn" onclick="playOnSelectedScreen('${camera.id}', false)" title="普通播放">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M8 5v14l11-7z"/>
                    </svg>
                </button>
                <button class="camera-item-btn ai-btn" onclick="playOnSelectedScreen('${camera.id}', true)" title="AI 播放">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M21 3H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H3V5h18v14zM8 15c0-1.66 1.34-3 3-3 .35 0 .69.07 1 .18V6h5v2h-3v7.03A3.003 3.003 0 0 1 11 18c-1.66 0-3-1.34-3-3z"/>
                    </svg>
                </button>
                <button class="camera-item-btn" onclick="openSingleScreen('${camera.id}', event)">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/>
                    </svg>
                </button>
            </div>
        </div>
    `;
}

function countDirCameras(children) {
    let count = 0;
    children.forEach(child => {
        count += child.cameras.length + countDirCameras(child.children);
    });
    return count;
}

function toggleDirNode(headerEl) {
    const dirNode = headerEl.closest('.dir-node');
    const children = dirNode.querySelector(':scope > .dir-children');
    const arrow = headerEl.querySelector('.dir-arrow');
    const dirId = parseInt(dirNode.dataset.dirId);
    
    if (children.style.display === 'none') {
        children.style.display = 'block';
        arrow.classList.add('expanded');
        expandedDirs.add(dirId);
    } else {
        children.style.display = 'none';
        arrow.classList.remove('expanded');
        expandedDirs.delete(dirId);
    }
}

// ========================================
// 更新摄像机统计
// ========================================

function updateCameraStats() {
    const online = cameras.filter(c => c.status === 'online').length;
    const offline = cameras.filter(c => c.status !== 'online').length;
    
    document.getElementById('onlineCount').textContent = `在线: ${online}`;
    document.getElementById('offlineCount').textContent = `离线: ${offline}`;
}

// ========================================
// 筛选摄像机
// ========================================

function filterCameras() {
    const search = document.getElementById('cameraSearch').value.toLowerCase();
    const dirNodes = document.querySelectorAll('.dir-node');
    const cameraItems = document.querySelectorAll('.camera-item');
    
    if (!search) {
        // 恢复默认显示
        dirNodes.forEach(node => {
            node.style.display = 'block';
        });
        cameraItems.forEach(item => {
            item.style.display = 'flex';
        });
        return;
    }
    
    // 先隐藏所有
    dirNodes.forEach(node => node.style.display = 'none');
    cameraItems.forEach(item => item.style.display = 'none');
    
    // 显示匹配的摄像机及其父目录
    cameraItems.forEach(item => {
        const name = item.querySelector('.camera-item-name').textContent.toLowerCase();
        if (name.includes(search)) {
            item.style.display = 'flex';
            // 显示所有父目录
            let parent = item.closest('.dir-node');
            while (parent) {
                parent.style.display = 'block';
                const children = parent.querySelector(':scope > .dir-children');
                if (children) children.style.display = 'block';
                const arrow = parent.querySelector(':scope > .dir-header .dir-arrow');
                if (arrow) arrow.classList.add('expanded');
                parent = parent.parentElement.closest('.dir-node');
            }
        }
    });
}

// ========================================
// 更新摄像机状态
// ========================================

function updateCameraStatus(cameraId, status) {
    const camera = cameras.find(c => c.id === cameraId);
    if (camera) {
        camera.status = status;
        renderCameraTree();
        updateCameraStats();
    }
}

// ========================================
// 布局设置
// ========================================

function setLayout(layout) {
    currentLayout = layout;
    
    // 更新按钮状态
    document.querySelectorAll('.layout-btn').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.layout) === layout);
    });
    
    // 生成画面格子
    generateScreens(layout);
}

// ========================================
// 生成画面格子
// ========================================

function generateScreens(count) {
    const monitorWall = document.getElementById('monitorWall');
    
    // 保存当前画面信息
    const oldScreens = screens.slice();
    screens = [];
    
    // 根据布局生成格子
    const gridClass = getGridClass(count);
    monitorWall.className = `monitor-wall ${gridClass}`;
    
    let html = '';
    for (let i = 0; i < count; i++) {
        html += `
            <div class="screen-cell" 
                 data-index="${i}"
                 onclick="selectScreen(${i})"
                 ondragover="handleDragOver(event)"
                 ondrop="handleDrop(event, ${i})">
                <div class="screen-content" id="screenContent${i}">
                    <div class="screen-placeholder">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>
                        </svg>
                        <span>点击选择摄像机</span>
                    </div>
                </div>
                <div class="screen-overlay">
                    <div class="screen-info" id="screenInfo${i}"></div>
                    <div class="screen-controls">
                        <button class="screen-btn" onclick="toggleAIMode(${i}, event)" title="切换 AI/普通模式">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M21 3H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H3V5h18v14zM8 15c0-1.66 1.34-3 3-3 .35 0 .69.07 1 .18V6h5v2h-3v7.03A3.003 3.003 0 0 1 11 18c-1.66 0-3-1.34-3-3z"/>
                            </svg>
                        </button>
                        <button class="screen-btn" onclick="openSingleScreenFromWall(${i}, event)">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/>
                            </svg>
                        </button>
                        <button class="screen-btn" onclick="clearScreen(${i}, event)">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        screens.push(null);
    }
    
    monitorWall.innerHTML = html;
    
    // 恢复旧画面（如果布局变大）
    oldScreens.forEach((screen, index) => {
        if (screen && index < count) {
            playOnScreen(index, screen.camera_id);
        }
    });
    
    // 默认选中第一个格子
    selectScreen(0);
}

// ========================================
// 获取网格类名
// ========================================

function getGridClass(count) {
    const classMap = {
        1: 'layout-1',
        2: 'layout-2',
        4: 'layout-4',
        9: 'layout-9'
    };
    return classMap[count] || 'layout-4';
}

// ========================================
// 选择画面格子
// ========================================

function selectScreen(index) {
    selectedScreenIndex = index;
    
    // 更新选中状态
    document.querySelectorAll('.screen-cell').forEach((cell, i) => {
        cell.classList.toggle('selected', i === index);
    });
}

// ========================================
// 播放摄像机到选中格子
// ========================================

function playOnSelectedScreen(cameraId, useAIMode = false) {
    playOnScreen(selectedScreenIndex, cameraId, useAIMode);
}

// ========================================
// 播放摄像机到指定格子
// ========================================

function playOnScreen(screenIndex, cameraId, useAIMode = false) {
    const camera = cameras.find(c => c.id === cameraId);
    if (!camera) return;

    console.log('播放摄像机对象:', camera);
    console.log('protocol:', camera.protocol);
    console.log('ws_stream:', camera.ws_stream);
    console.log('http_stream:', camera.http_stream);

    const screenContent = document.getElementById(`screenContent${screenIndex}`);
    const screenInfo = document.getElementById(`screenInfo${screenIndex}`);

    // 创建播放容器
    screenContent.innerHTML = `
        <div id="screenContainer${screenIndex}" class="video-container"></div>
    `;

    // 显示摄像机信息
    screenInfo.innerHTML = `
        <span class="screen-name">${escapeHtml(camera.camera_name || camera.name || camera.camera_id)}</span>
        <span class="screen-status ${camera.status === 'online' ? 'online' : 'offline'}">${camera.status === 'online' ? '在线' : '离线'}</span>
        <span class="screen-mode-badge ${useAIMode ? 'ai-mode' : ''}">${useAIMode ? 'AI' : '普通'}</span>
    `;

    // 记录 AI 模式
    aiStreamMode[screenIndex] = useAIMode;

    // 检查是否是视频文件类型
    const isVideoFile = camera.protocol === 'file';

    if (isVideoFile) {
        // 视频文件播放：
        // - 普通播放：使用HTML5 <video>标签直接播放视频文件
        // - AI播放：使用Canvas + WebSocket接收AI流
        if (useAIMode) {
            console.log('视频文件 + AI模式：使用AI流播放');
            playVideoFileWithAI(screenIndex, camera);
        } else {
            console.log('视频文件 + 普通模式：直接播放视频文件');
            playVideoFileNormal(screenIndex, camera);
        }
    } else if (useAIMode) {
        // RTSP流 + AI模式：使用 Canvas 渲染
        playWithAIStream(screenIndex, camera);
    } else {
        // RTSP流 + 普通模式：使用 WebRTC 播放器
        playStream(`screenContainer${screenIndex}`, camera);
    }

    // 保存画面信息
    screens[screenIndex] = {
        camera_id: cameraId,
        camera: camera,
        playing: true,
        ai_mode: useAIMode,  // AI 模式由用户选择，不再强制
        is_video_file: isVideoFile
    };
}

// ========================================
// 播放视频流（h265web为主，Jessibuca备用）
// ========================================

let webrtcPlayers = {}; // WebRTC播放器实例
let jessibucaPlayers = {}; // Jessibuca播放器实例（备用）

function playStream(containerId, camera) {
    console.log(`播放摄像机: ${camera.id}, rtc_stream=${camera.rtc_stream}, http_stream=${camera.http_stream}`);

    // 停止旧的播放
    stopStream(containerId);

    // 先检查流状态，如果不存在则先启动流
    checkAndStartStream(camera.id).then((updatedCamera) => {
        // 使用最新的流信息（如果有）
        const cameraToPlay = updatedCamera || camera;
        console.log(`使用流信息: rtc_stream=${cameraToPlay.rtc_stream}, http_stream=${cameraToPlay.http_stream}`);
        
        // 流已确认存在，使用WebRTC播放
        playWithWebRTC(containerId, cameraToPlay);
    }).catch(error => {
        console.error('流检查/启动失败:', error);
        showNotification('流启动失败，请手动启动流', 'error');
    });
}

// ========================================
// WebRTC 播放器（主播放器，使用ZLMRTCClient）
// ========================================

function playWithWebRTC(containerId, camera) {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`找不到播放容器: ${containerId}`);
        return;
    }

    // 清空容器，创建video元素
    container.innerHTML = `
        <video id="webrtcVideo${containerId}" 
               autoplay 
               playsinline 
               style="width:100%;height:100%;background-color:#000;object-fit:contain;">
        </video>
    `;

    // 检查 ZLMRTCClient 是否已加载
    if (typeof window.ZLMRTCClient === 'undefined') {
        console.warn('ZLMRTCClient 未加载，尝试动态加载...');
        loadZLMRTCClient().then(() => {
            playWithWebRTC(containerId, camera);
        }).catch(err => {
            console.error('ZLMRTCClient 加载失败，降级到Jessibuca:', err);
            playWithJessibuca(containerId, camera);
        });
        return;
    }

    // 优先使用 RTC 流地址
    const rtcUrl = camera.rtc_stream || camera.rtcs_stream;
    
    if (!rtcUrl) {
        console.warn('没有RTC流地址，降级到Jessibuca');
        playWithJessibuca(containerId, camera);
        return;
    }

    console.log('WebRTC播放地址:', rtcUrl);

    try {
        // 销毁已有实例
        if (webrtcPlayers[containerId]) {
            webrtcPlayers[containerId].close();
            webrtcPlayers[containerId] = null;
        }

        const videoElement = document.getElementById(`webrtcVideo${containerId}`);

        // 创建WebRTC播放器
        const player = new window.ZLMRTCClient.Endpoint({
            element: videoElement,
            debug: false,
            zlmsdpUrl: rtcUrl,
            simulecast: false,
            useCamera: false,
            audioEnable: true,
            videoEnable: true,
            recvOnly: true,
            usedatachannel: false
        });

        webrtcPlayers[containerId] = player;

        // 播放成功回调
        player.on(window.ZLMRTCClient.Events.WEBRTC_ON_REMOTE_STREAMS, (e) => {
            console.log('WebRTC播放成功', e.streams);
        });

        // ICE协商出错
        player.on(window.ZLMRTCClient.Events.WEBRTC_ICE_CANDIDATE_ERROR, (e) => {
            console.error('WebRTC ICE协商出错:', e);
        });

        // offer/answer交换失败
        player.on(window.ZLMRTCClient.Events.WEBRTC_OFFER_ANWSER_EXCHANGE_FAILED, (e) => {
            console.error('WebRTC offer/answer交换失败:', e);
            // 如果是流不存在，降级到Jessibuca
            if (e.code === -400 && e.msg === '流不存在') {
                console.warn('流不存在，降级到Jessibuca');
                stopStream(containerId);
                playWithJessibuca(containerId, camera);
            }
        });

        // 连接状态变化
        player.on(window.ZLMRTCClient.Events.WEBRTC_ON_CONNECTION_STATE_CHANGE, (state) => {
            console.log('WebRTC连接状态:', state);
            if (state === 'failed' || state === 'disconnected') {
                console.warn('WebRTC连接断开');
            }
        });

    } catch (error) {
        console.error('WebRTC播放器创建失败:', error);
        playWithJessibuca(containerId, camera);
    }
}

// 动态加载ZLMRTCClient
function loadZLMRTCClient() {
    return new Promise((resolve, reject) => {
        if (window.ZLMRTCClient) {
            resolve();
            return;
        }

        const script = document.createElement('script');
        script.src = '/static/js/ZLMRTCClient.js';
        script.onload = () => {
            console.log('ZLMRTCClient 加载成功');
            resolve();
        };
        script.onerror = (e) => {
            console.error('ZLMRTCClient 加载失败:', e);
            reject(e);
        };
        document.head.appendChild(script);
    });
}

// ========================================
// 流状态检查和自动启动
// ========================================

async function checkAndStartStream(cameraId) {
    try {
        // 检查流状态
        const response = await fetch(`/api/stream/status/${cameraId}`);
        const result = await response.json();

        if (result.success && result.stream_online) {
            console.log(`流已存在: camera_id=${cameraId}`);
            // 返回最新的流信息
            return result.camera || null;
        }

        // 流不存在，自动启动
        console.log(`流不存在，自动启动: camera_id=${cameraId}`);
        const startResponse = await fetch(`/api/stream/start/${cameraId}`, {
            method: 'POST'
        });
        const startResult = await startResponse.json();

        if (startResult.success) {
            console.log(`流启动成功: camera_id=${cameraId}`);
            // 等待一小段时间让流稳定
            await new Promise(resolve => setTimeout(resolve, 1500));
            // 返回最新的流信息
            return startResult.camera || null;
        } else {
            throw new Error(startResult.error || '流启动失败');
        }
    } catch (error) {
        console.error('流检查/启动失败:', error);
        throw error;
    }
}

// ========================================
// Jessibuca 播放器（备用播放器）
// ========================================

function playWithJessibuca(containerId, camera) {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`找不到播放容器: ${containerId}`);
        return;
    }

    // 清空容器
    container.innerHTML = '';

    // 创建 Jessibuca 播放器
    const player = new Jessibuca({
        container: container,
        decoder: '/static/js/jessibuca/decoder.js', // 解码器 js 文件路径
        wasmDecoder: '/static/js/jessibuca/decoder.wasm', // 解码器 wasm 文件路径
        videoBuffer: 0.2, // 缓冲时间
        isResize: false,  // 不自动调整大小
        text: '',         // OSD文本
        loadingText: '加载中...',
        debug: false,     // 不开启调试
        supportDblclickFullscreen: true, // 双击全屏
        showBandwidth: false, // 不显示码率
        operateBtns: {
            fullscreen: false,
            screenshot: false,
            play: false,
            audio: false
        },
        forceNoOffscreen: true, // 强制使用离屏渲染
        isNotMute: false,       // 默认静音
        timeout: 10             // 超时时间（秒）
    });

    // 保存播放器实例
    jessibucaPlayers[containerId] = player;

    // 优先使用 WebSocket-FLV（低延迟，更稳定）
    const playUrl = camera.ws_stream || camera.http_stream;

    if (playUrl) {
        const isWebSocket = playUrl.startsWith('ws://') || playUrl.startsWith('wss://');
        console.log(`Jessibuca ${isWebSocket ? 'WebSocket-FLV' : 'HTTP-FLV'}播放:`, playUrl);
        player.play(playUrl);

        // 监听播放错误
        player.on('error', (error) => {
            console.error('Jessibuca播放失败:', error);
            showNotification('视频播放失败，请检查流地址', 'error');
        });

        // 监听播放成功
        player.on('play', () => {
            console.log('Jessibuca播放成功');
        });
    } else {
        showNotification('没有可用的播放地址', 'error');
    }
}

function stopStream(containerId) {
    // 关闭 WebRTC 播放器
    const webrtcPlayer = webrtcPlayers[containerId];
    if (webrtcPlayer) {
        try {
            webrtcPlayer.close();
        } catch (e) {
            console.warn('WebRTC释放失败:', e);
        }
        delete webrtcPlayers[containerId];
        console.log(`WebRTC播放器已停止: ${containerId}`);
    }

    // 关闭 Jessibuca 播放器
    const jessibucaPlayer = jessibucaPlayers[containerId];
    if (jessibucaPlayer) {
        jessibucaPlayer.destroy();
        delete jessibucaPlayers[containerId];
        console.log(`Jessibuca播放器已停止: ${containerId}`);
    }

    // 清空容器
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = '';
    }
}

// ========================================
// AI 视频流播放（后端画框，Canvas 渲染）
// ========================================

/**
 * AI 模式播放（统一入口，适用于 RTSP 流和视频文件）
 *
 * 架构：
 * - 前端创建 Canvas 元素
 * - 前端调用 enableAIStream → 后端开始推送带检测框的 JPEG 帧
 * - 前端订阅 ai_stream_frame WebSocket 事件
 * - 后端 TrackedObjectProcessor 推送帧 → 前端 Canvas 直接 drawImage 显示
 *
 * 前端不再绘制任何检测框，所有绘制由后端完成。
 */
function playWithAIStream(screenIndex, camera) {
    const containerId = `screenContainer${screenIndex}`;
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`找不到播放容器: ${containerId}`);
        return;
    }

    // 创建 Canvas 容器（后端推送带框 JPEG 帧，前端直接显示）
    container.innerHTML = `
        <div style="position: relative; width: 100%; height: 100%; background: #000;">
            <canvas id="aiStreamCanvas${screenIndex}"
                    style="width:100%;height:100%;display:block;">
            </canvas>
            <div id="aiStreamStatus${screenIndex}"
                 style="position:absolute;top:10px;left:10px;color:#fff;font-size:12px;background:rgba(0,0,0,0.5);padding:4px 8px;border-radius:4px;">
                等待AI视频流...
            </div>
        </div>
    `;

    const canvas = document.getElementById(`aiStreamCanvas${screenIndex}`);
    aiStreamCanvas[screenIndex] = canvas;

    // 订阅 AI 视频流（前端记录订阅状态，ai_stream_frame 事件根据此状态过滤）
    subscribeAIStream(camera.id, screenIndex);

    // 通知后端开始推送 AI 流（设置 ai_stream_enabled_state[camera_id]=True）
    enableAIStream(camera.id);

    console.log(`AI 模式已启用: camera_id=${camera.id}, screen_index=${screenIndex}`);
}

/**
 * 视频文件 + AI模式：使用统一 AI 流播放
 *（与 RTSP 流 AI 模式使用相同的后端画框机制）
 */
function playVideoFileWithAI(screenIndex, camera) {
    playWithAIStream(screenIndex, camera);
}

/**
 * 视频文件 + 普通模式：使用HTML5 <video>标签直接播放视频文件
 * 不显示检测框，只播放视频文件本身
 */
function playVideoFileNormal(screenIndex, camera) {
    const containerId = `screenContainer${screenIndex}`;
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`找不到播放容器: ${containerId}`);
        return;
    }

    // 从 camera.source 或 camera.rtsp_url 获取视频文件路径
    // 数据库中存储格式: file://D:\sagesight-ai\uploads/video_files\xxx.mp4
    let videoPath = camera.source || camera.rtsp_url || '';

    // 去除 file:// 前缀
    if (videoPath.startsWith('file://')) {
        videoPath = videoPath.substring(7);
    }

    // 构造HTTP访问路径（假设视频文件通过 /uploads/ 路径访问）
    // 例如: D:\sagesight-ai\uploads/video_files\xxx.mp4 -> /uploads/video_files/xxx.mp4
    let httpPath = videoPath;
    if (videoPath.includes('uploads')) {
        // 提取 uploads 后面的路径
        const match = videoPath.match(/uploads[\/\\](.*)/);
        if (match) {
            httpPath = '/uploads/' + match[1].replace(/\\/g, '/');
        }
    }

    console.log('视频文件路径:', videoPath);
    console.log('HTTP访问路径:', httpPath);

    // 创建HTML5 video元素
    container.innerHTML = `
        <div style="position: relative; width: 100%; height: 100%; background: #000;">
            <video id="videoPlayer${screenIndex}"
                   style="width:100%;height:100%;object-fit:contain;"
                   controls
                   autoplay
                   muted>
                <source src="${httpPath}" type="video/mp4">
                您的浏览器不支持视频播放
            </video>
        </div>
    `;

    const video = document.getElementById(`videoPlayer${screenIndex}`);

    // 监听视频加载事件
    video.addEventListener('loadeddata', function() {
        console.log(`视频文件加载成功: ${httpPath}`);
    });

    // 监听错误事件
    video.addEventListener('error', function(e) {
        console.error(`视频文件加载失败:`, e);
        showNotification('视频文件加载失败，请检查路径', 'error');
    });

    console.log(`视频文件普通播放模式已启用: camera_id=${camera.id}, path=${httpPath}`);
}

// ========================================
// AI 流订阅管理
// ========================================

/**
 * 订阅AI视频流（WebSocket）
 * ai_stream_frame 事件根据此订阅记录决定是否渲染帧
 */
function subscribeAIStream(cameraId, screenIndex) {
    if (!aiStreamSubscriptions[cameraId]) {
        aiStreamSubscriptions[cameraId] = {
            screenIndexes: new Set(),
            image: new Image()
        };
    }
    aiStreamSubscriptions[cameraId].screenIndexes.add(screenIndex);
    console.log(`订阅AI视频流: cameraId=${cameraId}, screenIndex=${screenIndex}`);
}

/**
 * 取消订阅AI视频流
 */
function unsubscribeAIStream(cameraId, screenIndex) {
    if (aiStreamSubscriptions[cameraId]) {
        aiStreamSubscriptions[cameraId].screenIndexes.delete(screenIndex);
        if (aiStreamSubscriptions[cameraId].screenIndexes.size === 0) {
            delete aiStreamSubscriptions[cameraId];
        }
    }
}

/**
 * requestAnimationFrame 回调：批量渲染 _pendingAIFrames 中缓存的最新帧
 *
 * 设计要点：
 * - 一个 rAF 周期内多个 WS 消息到达时，_pendingAIFrames 只保留每个 camera 的最新帧
 * - 本回调一次性把所有 camera 的最新帧渲染到对应 Canvas，避免重复 drawImage
 * - 浏览器刷新率（通常 60Hz）天然限流，WS 消息再多也不会超过屏幕刷新能力
 * - 渲染完成后清空 _pendingAIFrames 并重置 _rafScheduled，允许下一轮调度
 */
function _flushPendingAIFrames() {
    // 取出本周期内待渲染的帧（快照后立即清空，便于下一轮 WS 消息继续累积）
    const framesToRender = _pendingAIFrames;
    _pendingAIFrames = {};
    _rafScheduled = false;

    for (const cameraId in framesToRender) {
        renderAIStreamFrame(cameraId, framesToRender[cameraId]);
        aiStats.renderedFrames++;
    }
}

/**
 * 渲染后端推送的带检测框 JPEG 帧到 Canvas
 * 后端已完成所有绘制（检测框、跟踪框、轨迹），前端只需 drawImage 显示
 *
 * 自适应机制：
 * - Canvas 内部分辨率跟随容器实际尺寸（clientWidth/clientHeight）
 * - drawImage 将后端帧拉伸填充整个 Canvas，画面自适应播放器大小
 * - 仅在容器尺寸变化时更新 Canvas 分辨率，避免每帧重置影响性能
 */
function renderAIStreamFrame(cameraId, frameBase64) {
    // 将后端帧渲染到指定 Canvas，自适应容器大小
    const renderToCanvas = (canvas, img) => {
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        // Canvas 内部分辨率跟随容器尺寸（仅在尺寸变化时更新）
        const targetW = canvas.clientWidth || 640;
        const targetH = canvas.clientHeight || 480;
        if (canvas.width !== targetW || canvas.height !== targetH) {
            canvas.width = targetW;
            canvas.height = targetH;
        }
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        // 拉伸图片填充整个 Canvas（画面自适应播放器大小）
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    };

    // 多画面渲染
    screens.forEach((screen, index) => {
        if (screen && screen.camera_id === cameraId && aiStreamMode[index]) {
            const canvas = aiStreamCanvas[index];
            if (!canvas) return;

            const img = new Image();
            img.onload = function() {
                renderToCanvas(canvas, img);
                // 第一帧渲染成功后，清除"等待AI视频流..."提示
                const statusEl = document.getElementById(`aiStreamStatus${index}`);
                if (statusEl) {
                    statusEl.textContent = '';
                    statusEl.style.display = 'none';
                }
            };
            img.onerror = function() {
                console.error(`[AI流] 图片加载失败: camera_id=${cameraId}`);
            };
            img.src = 'data:image/jpeg;base64,' + frameBase64;
        }
    });

    // 单画面弹窗渲染
    if (currentSingleCamera && currentSingleCamera.id === cameraId && singleScreenAIMode) {
        const canvas = document.getElementById('singleAICanvas');
        if (canvas) {
            const img = new Image();
            img.onload = function() {
                renderToCanvas(canvas, img);
                // 第一帧渲染成功后，清除"等待AI视频流..."提示
                const statusEl = document.getElementById('singleAIStatus');
                if (statusEl) {
                    statusEl.textContent = '';
                    statusEl.style.display = 'none';
                }
            };
            img.src = 'data:image/jpeg;base64,' + frameBase64;
        }
    }
}

// ========================================
// AI 流启用/禁用 API
// ========================================

function enableAIStream(cameraId) {
    fetch(`/api/ai-stream/enable/${cameraId}`, {
        method: 'POST'
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              console.log(`AI 视频流已启用: camera_id=${cameraId}`);
          } else {
              console.error(`启用 AI 视频流失败: ${data.error}`);
          }
      }).catch(error => {
          console.error(`启用 AI 视频流请求失败: ${error}`);
      });
}

function disableAIStream(cameraId) {
    fetch(`/api/ai-stream/disable/${cameraId}`, {
        method: 'POST'
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              console.log(`AI 视频流已禁用: camera_id=${cameraId}`);
          }
      }).catch(error => {
          console.error(`禁用 AI 视频流请求失败: ${error}`);
      });
}

function toggleAIMode(screenIndex) {
    const screen = screens[screenIndex];
    if (!screen || !screen.camera) return;

    const currentAIMode = aiStreamMode[screenIndex] || false;
    const newAIMode = !currentAIMode;

    // 停止当前播放
    stopStream(`screenContainer${screenIndex}`);

    // 如果从 AI 模式切换到普通模式，禁用 AI 流并取消订阅
    if (currentAIMode) {
        disableAIStream(screen.camera.id);
        unsubscribeAIStream(screen.camera.id, screenIndex);
    }

    // 重新播放
    playOnScreen(screenIndex, screen.camera.id, newAIMode);

    showNotification(newAIMode ? '已切换到 AI 模式' : '已切换到普通模式', 'success');
}


// ========================================
// 清空画面
// ========================================

function clearScreen(index, event) {
    if (event) event.stopPropagation();

    // 停止视频播放
    stopStream(`screenContainer${index}`);

    // 清理 AI 流资源
    const screen = screens[index];
    if (screen && screen.camera && aiStreamMode[index]) {
        disableAIStream(screen.camera.id);
        unsubscribeAIStream(screen.camera.id, index);
    }
    delete aiStreamCanvas[index];
    delete aiStreamMode[index];

    const screenContent = document.getElementById(`screenContent${index}`);
    const screenInfo = document.getElementById(`screenInfo${index}`);
    
    screenContent.innerHTML = `
        <div class="screen-placeholder">
            <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>
            </svg>
            <span>点击选择摄像机</span>
        </div>
    `;
    
    screenInfo.innerHTML = '';
    screens[index] = null;
}

// ========================================
// 清空所有画面
// ========================================

function clearAllScreens() {
    for (let i = 0; i < currentLayout; i++) {
        clearScreen(i);
    }
}

// ========================================
// 自动排列
// ========================================

function autoArrange() {
    const onlineCameras = cameras.filter(c => c.status === 'online');
    
    for (let i = 0; i < Math.min(currentLayout, onlineCameras.length); i++) {
        playOnScreen(i, onlineCameras[i].id);
    }
}

// ========================================
// 拖拽功能
// ========================================

function initDragDrop() {
    // 初始化已在 generateScreens 中完成
}

function handleDragStart(event, cameraId) {
    event.dataTransfer.setData('cameraId', cameraId);
    event.target.classList.add('dragging');
}

function handleDragOver(event) {
    event.preventDefault();
    event.currentTarget.classList.add('drag-over');
}

function handleDrop(event, screenIndex) {
    event.preventDefault();
    event.currentTarget.classList.remove('drag-over');
    
    const cameraId = event.dataTransfer.getData('cameraId');
    if (cameraId) {
        playOnScreen(screenIndex, cameraId);
    }
    
    // 移除拖拽样式
    document.querySelectorAll('.dragging').forEach(el => el.classList.remove('dragging'));
}

// ========================================
// 单画面弹出
// ========================================

function openSingleScreen(cameraId, event) {
    if (event) event.stopPropagation();

    const camera = cameras.find(c => c.id === cameraId);
    if (!camera) return;

    currentSingleCamera = camera;
    singleScreenAIMode = false;  // 默认普通模式
    updateSingleAIModeButton(false);

    // 更新单画面对话框的摄像机信息（海康威视风格）
    updateSingleScreenInfo(camera);

    // 更新标题
    document.getElementById('singleScreenTitle').textContent = camera.camera_name || camera.camera_id;

    playStream('singleScreenVideo', camera);

    // 初始化PTZ方向箭头按钮事件
    initPtzArrowButtons();

    // 加载预置点和巡航路径列表
    loadPresets(camera.id);
    loadPatrols(camera.id);

    // 初始化语音对讲功能
    initAudioTalk();

    // 启动流状态更新定时器
    startStreamStatusUpdate('singleScreenVideo');

    document.getElementById('singleScreenModal').classList.add('show');
}

// 加载预置点列表
function loadPresets(cameraId) {
    fetch(`/api/ptz/preset/list?camera_id=${cameraId}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const presetList = document.querySelector('.preset-list');
                presetList.innerHTML = '';
                
                presets = data.presets;
                
                // 如果有提示消息，显示提示
                if (data.message) {
                    presetList.innerHTML = `<div class="preset-empty preset-unsupported">${data.message}</div>`;
                } else if (presets.length === 0) {
                    presetList.innerHTML = '<div class="preset-empty">暂无预置点</div>';
                } else {
                    presets.forEach(preset => {
                        const presetItem = document.createElement('div');
                        presetItem.className = 'preset-item';
                        presetItem.setAttribute('data-preset-id', preset.id);
                        presetItem.innerHTML = `
                            <div class="preset-item-content">
                                <span class="preset-name">${preset.name}</span>
                                <div class="preset-actions">
                                    <button onclick="gotoPreset('${preset.id}')" title="调用">
                                        <i class="fa fa-play"></i>
                                    </button>
                                    <button onclick="setPreset('${preset.id}')" title="设置">
                                        <i class="fa fa-cog"></i>
                                    </button>
                                    <button onclick="deletePreset('${preset.id}')" title="删除">
                                        <i class="fa fa-trash"></i>
                                    </button>
                                </div>
                            </div>
                        `;
                        presetList.appendChild(presetItem);
                    });
                }
            } else {
                console.error('加载预置点失败:', data.error);
                // 显示错误提示
                const presetList = document.querySelector('.preset-list');
                presetList.innerHTML = `<div class="preset-empty preset-error">加载失败：${data.error}</div>`;
            }
        })
        .catch(error => {
            console.error('加载预置点请求失败:', error);
            // 显示网络错误提示
            const presetList = document.querySelector('.preset-list');
            presetList.innerHTML = '<div class="preset-empty preset-error">网络请求失败</div>';
        });
}

// 加载巡航路径列表
function loadPatrols(cameraId) {
    fetch(`/api/ptz/patrol/list?camera_id=${cameraId}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const patrolList = document.querySelector('.patrol-list');
                patrolList.innerHTML = '';
                
                patrols = data.patrols;
                
                // 如果有提示消息，显示提示
                if (data.message) {
                    patrolList.innerHTML = `<div class="patrol-empty patrol-unsupported">${data.message}</div>`;
                } else if (patrols.length === 0) {
                    patrolList.innerHTML = '<div class="patrol-empty">暂无巡航路径</div>';
                } else {
                    patrols.forEach(patrol => {
                        const patrolItem = document.createElement('div');
                        patrolItem.className = 'patrol-item';
                        patrolItem.setAttribute('data-patrol-id', patrol.id);
                        patrolItem.innerHTML = `
                            <div class="patrol-item-content">
                                <span class="patrol-name">${patrol.name}</span>
                                <div class="patrol-status">
                                    <span class="patrol-points">预置点数: ${patrol.points}</span>
                                </div>
                                <div class="patrol-actions">
                                    <button onclick="startPatrol(${patrol.id})" title="调用">
                                        <i class="fa fa-play"></i>
                                    </button>
                                    <button onclick="stopPatrol(${patrol.id})" title="停止">
                                        <i class="fa fa-stop"></i>
                                    </button>
                                    <button onclick="editPatrol(${patrol.id})" title="编辑">
                                        <i class="fa fa-edit"></i>
                                    </button>
                                    <button onclick="deletePatrol(${patrol.id})" title="删除">
                                        <i class="fa fa-trash"></i>
                                    </button>
                                </div>
                            </div>
                        `;
                        patrolList.appendChild(patrolItem);
                    });
                }
            } else {
                console.error('加载巡航路径失败:', data.error);
                // 显示错误提示
                const patrolList = document.querySelector('.patrol-list');
                patrolList.innerHTML = `<div class="patrol-empty patrol-error">加载失败：${data.error}</div>`;
            }
        })
        .catch(error => {
            console.error('加载巡航路径请求失败:', error);
            // 显示网络错误提示
            const patrolList = document.querySelector('.patrol-list');
            patrolList.innerHTML = '<div class="patrol-empty patrol-error">网络请求失败</div>';
        });
}

function openSingleScreenFromWall(screenIndex, event) {
    if (event) event.stopPropagation();
    
    const screen = screens[screenIndex];
    if (screen && screen.camera) {
        openSingleScreen(screen.camera.id);
    }
}

function closeSingleScreen() {
    document.getElementById('singleScreenModal').classList.remove('show');

    // 停止视频播放
    stopStream('singleScreenVideo');

    // 清理 AI 流资源
    if (currentSingleCamera && singleScreenAIMode) {
        disableAIStream(currentSingleCamera.id);
        unsubscribeAIStream(currentSingleCamera.id, 'single');
        delete aiFrameCanvases[currentSingleCamera.id];
        delete aiStreamCanvas['single'];
        singleScreenAIMode = false;
    }

    // 停止流状态更新定时器
    stopStreamStatusUpdate();

    currentSingleCamera = null;
}

function toggleSingleFullscreen() {
    const container = document.getElementById('singleScreenContainer');

    if (document.fullscreenElement) {
        document.exitFullscreen();
    } else {
        container.requestFullscreen();
    }
}

// 切换单画面 AI/普通模式
function toggleSingleAIMode() {
    if (!currentSingleCamera) return;

    const wasAIMode = singleScreenAIMode;
    singleScreenAIMode = !singleScreenAIMode;

    // 停止当前播放
    stopStream('singleScreenVideo');

    // 如果从 AI 模式切换到普通模式，禁用后端 AI 流并取消订阅
    if (wasAIMode) {
        disableAIStream(currentSingleCamera.id);
        unsubscribeAIStream(currentSingleCamera.id, 'single');
        delete aiFrameCanvases[currentSingleCamera.id];
        delete aiStreamCanvas['single'];
    }

    if (singleScreenAIMode) {
        // 切换到 AI 模式
        playSingleWithAIStream(currentSingleCamera);
        updateSingleAIModeButton(true);
        showNotification('已切换到 AI 模式', 'success');
    } else {
        // 切换到普通模式
        playStream('singleScreenVideo', currentSingleCamera);
        updateSingleAIModeButton(false);
        showNotification('已切换到普通模式', 'success');
    }
}

function updateSingleAIModeButton(isAIMode) {
    const btn = document.getElementById('singleScreenAIModeBtn');
    if (!btn) return;
    if (isAIMode) {
        btn.classList.add('active');
        btn.title = '当前：AI 模式（点击切换为普通模式）';
    } else {
        btn.classList.remove('active');
        btn.title = '当前：普通模式（点击切换为 AI 模式）';
    }
}

function playSingleWithAIStream(camera) {
    const container = document.getElementById('singleScreenVideo');
    if (!container) return;

    // 清空容器，创建 Canvas 元素
    container.innerHTML = `
        <canvas id="singleAICanvas"
                style="width:100%;height:100%;background-color:#000;display:block;">
        </canvas>
    `;

    const canvas = document.getElementById('singleAICanvas');
    aiStreamCanvas['single'] = canvas;
    aiFrameCanvases[camera.id] = canvas;

    // 订阅 AI 视频流（前端记录订阅状态）
    subscribeAIStream(camera.id, 'single');

    // 通知后端开始推送 AI 流
    enableAIStream(camera.id);

    console.log(`单画面 AI 视频流已启用: camera_id=${camera.id}`);
}

// ========================================
// 流状态和码率更新
// ========================================

// 启动流状态更新定时器
function startStreamStatusUpdate(containerId) {
    // 先清除旧的定时器
    stopStreamStatusUpdate();

    // 每1秒更新一次状态
    streamStatusInterval = setInterval(() => {
        updateStreamStatus(containerId);
    }, 1000);

    // 立即更新一次
    updateStreamStatus(containerId);
}

// 停止流状态更新定时器
function stopStreamStatusUpdate() {
    if (streamStatusInterval) {
        clearInterval(streamStatusInterval);
        streamStatusInterval = null;
    }
}

// 更新流状态和码率显示
async function updateStreamStatus(containerId) {
    const statusElement = document.getElementById('singleStreamStatus');
    const bitrateElement = document.getElementById('singleStreamBitrate');

    if (!statusElement || !bitrateElement) return;

    // 检查WebRTC播放器
    const webrtcPlayer = webrtcPlayers[containerId];
    if (webrtcPlayer) {
        try {
            // 获取WebRTC连接状态
            const pc = webrtcPlayer.pc;
            if (pc) {
                const state = pc.connectionState || pc.iceConnectionState;
                const stateText = {
                    'new': '新建',
                    'connecting': '连接中',
                    'connected': '已连接',
                    'completed': '已完成',
                    'disconnected': '断开',
                    'failed': '失败',
                    'closed': '关闭'
                };
                statusElement.textContent = `状态: ${stateText[state] || state}`;

                // 获取码率统计
                const stats = await pc.getStats();
                let bitrate = 0;

                stats.forEach(report => {
                    if (report.type === 'inbound-rtp' && report.mediaType === 'video') {
                        // 计算码率（bytes转换为kbps）
                        const bytesReceived = report.bytesReceived || 0;
                        const timestamp = report.timestamp || 0;

                        // 需要两次统计才能计算速率
                        if (webrtcPlayer.lastStats) {
                            const lastReport = webrtcPlayer.lastStats;
                            if (lastReport.bytesReceived && lastReport.timestamp) {
                                const timeDiff = timestamp - lastReport.timestamp;
                                const bytesDiff = bytesReceived - lastReport.bytesReceived;
                                bitrate = Math.round((bytesDiff * 8) / (timeDiff / 1000) / 1000); // kbps
                            }
                        }

                        webrtcPlayer.lastStats = {
                            bytesReceived: bytesReceived,
                            timestamp: timestamp
                        };
                    }
                });

                bitrateElement.textContent = `码率: ${bitrate} kbps`;
            }
        } catch (error) {
            console.warn('获取WebRTC状态失败:', error);
            statusElement.textContent = '状态: 获取失败';
            bitrateElement.textContent = '码率: -- kbps';
        }
        return;
    }

    // 检查Jessibuca播放器
    const jessibucaPlayer = jessibucaPlayers[containerId];
    if (jessibucaPlayer) {
        try {
            // Jessibuca API
            const playing = jessibucaPlayer.playing();
            statusElement.textContent = `状态: ${playing ? '播放中' : '未播放'}`;

            // Jessibuca提供码率信息
            // 注意：Jessibuca的API可能不同，这里尝试获取
            // 有些版本通过 .bps() 或 .getBitrate() 获取
            if (typeof jessibucaPlayer.bps === 'function') {
                const bitrate = Math.round(jessibucaPlayer.bps() / 1000); // 转换为kbps
                bitrateElement.textContent = `码率: ${bitrate} kbps`;
            } else {
                bitrateElement.textContent = `码率: -- kbps`;
            }
        } catch (error) {
            console.warn('获取Jessibuca状态失败:', error);
            statusElement.textContent = '状态: 获取失败';
            bitrateElement.textContent = '码率: -- kbps';
        }
        return;
    }

    // 没有播放器
    statusElement.textContent = '状态: 未连接';
    bitrateElement.textContent = '码率: 0 kbps';
}

// ========================================
// 全屏功能
// ========================================

function toggleFullscreen() {
    const monitorWall = document.getElementById('monitorWall');
    
    if (document.fullscreenElement) {
        document.exitFullscreen();
    } else {
        monitorWall.requestFullscreen();
    }
}

// ========================================
// 播放控制
// ========================================

function togglePlayPause(type) {
    const videoId = type === 'single' ? 'singleScreenVideo' : `screenVideo${selectedScreenIndex}`;
    const video = document.getElementById(videoId);
    const iconId = type === 'single' ? 'singlePlayIcon' : null;
    
    if (video.paused) {
        video.play();
        if (iconId) {
            document.getElementById(iconId).innerHTML = '<path d="M8 5v14l11-7z"/>';
        }
    } else {
        video.pause();
        if (iconId) {
            document.getElementById(iconId).innerHTML = '<path d="M6 19h4V5H6zm8-14v14h4V5z"/>';
        }
    }
}

function toggleMute(type) {
    const videoId = type === 'single' ? 'singleScreenVideo' : `screenVideo${selectedScreenIndex}`;
    const video = document.getElementById(videoId);
    const iconId = type === 'single' ? 'singleMuteIcon' : null;
    
    video.muted = !video.muted;
    
    if (iconId) {
        if (video.muted) {
            document.getElementById(iconId).innerHTML = '<path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.26c2.89.86 5 3.54 5 6.51zM1.27 0L0 1.27 4.28 5.55H0v11h4.5l5 5v-6.67l4.25 4.25 1.27-1.27-9.77-9.77zm11.73 7.23l-1.27 1.27L16 12.5l2.5 2.5 1.27-1.27-5.5-5.5z"/>';
        } else {
            document.getElementById(iconId).innerHTML = '<path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.7 7-8.77s-2.99-7.86-7-8.77z"/>';
        }
    }
}

// ========================================
// 截图功能
// ========================================

function captureSnapshot(type) {
    const videoId = type === 'single' ? 'singleScreenVideo' : `screenVideo${selectedScreenIndex}`;
    const video = document.getElementById(videoId);
    
    if (!video || video.readyState < 2) {
        showNotification('视频未准备好', 'error');
        return;
    }
    
    // 创建 canvas 截图
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    
    snapshotData = canvas.toDataURL('image/png');
    
    document.getElementById('snapshotImage').src = snapshotData;
    document.getElementById('snapshotModal').classList.add('show');
}

function closeSnapshotModal() {
    document.getElementById('snapshotModal').classList.remove('show');
}

function downloadSnapshot() {
    if (!snapshotData) return;
    
    const cameraName = currentSingleCamera ? 
        (currentSingleCamera.camera_name || currentSingleCamera.camera_id) : 
        'snapshot';
    
    const link = document.createElement('a');
    link.href = snapshotData;
    link.download = `${cameraName}_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.png`;
    link.click();
    
    closeSnapshotModal();
}

// ========================================
// 录制功能
// ========================================

function startRecording(type) {
    const videoId = type === 'single' ? 'singleScreenVideo' : `screenVideo${selectedScreenIndex}`;
    const video = document.getElementById(videoId);
    
    if (!video || video.readyState < 2) {
        showNotification('视频未准备好', 'error');
        return;
    }
    
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        stopRecording(type);
        return;
    }
    
    recordedChunks = [];
    
    try {
        const stream = video.captureStream();
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm' });
        
        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                recordedChunks.push(event.data);
            }
        };
        
        mediaRecorder.onstop = () => {
            const blob = new Blob(recordedChunks, { type: 'video/webm' });
            const url = URL.createObjectURL(blob);
            
            const cameraName = currentSingleCamera ? 
                (currentSingleCamera.camera_name || currentSingleCamera.camera_id) : 
                'recording';
            
            const link = document.createElement('a');
            link.href = url;
            link.download = `${cameraName}_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.webm`;
            link.click();
            
            URL.revokeObjectURL(url);
            showNotification('录制已保存', 'success');
        };
        
        mediaRecorder.start();
        
        const iconId = type === 'single' ? 'singleRecordIcon' : null;
        if (iconId) {
            document.getElementById(iconId).innerHTML = '<circle cx="12" cy="12" r="8" fill="#ff4d4f"/>';
        }
        
        showNotification('开始录制', 'success');
        
    } catch (error) {
        console.error('录制失败:', error);
        showNotification('录制失败', 'error');
    }
}

function stopRecording(type) {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        
        const iconId = type === 'single' ? 'singleRecordIcon' : null;
        if (iconId) {
            document.getElementById(iconId).innerHTML = '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>';
        }
    }
}

// ========================================
// 语音对讲
// ========================================

function startVoiceTalk() {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'error');
        return;
    }
    
    document.getElementById('voiceTalkCameraName').textContent = 
        `摄像机: ${currentSingleCamera.camera_name || currentSingleCamera.camera_id}`;
    
    document.getElementById('voiceTalkModal').classList.add('show');
}

function closeVoiceTalk() {
    document.getElementById('voiceTalkModal').classList.remove('show');
    
    if (voiceTalkActive) {
        toggleVoiceTalk();
    }
}

async function toggleVoiceTalk() {
    const btn = document.getElementById('voiceTalkBtn');
    const status = document.getElementById('voiceTalkStatus');
    
    if (!voiceTalkActive) {
        try {
            // 请求麦克风权限
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            voiceTalkActive = true;
            btn.classList.add('active');
            btn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/>
                </svg>
                正在对讲
            `;
            status.innerHTML = `
                <span class="status-indicator active"></span>
                <span>正在对讲...</span>
            `;
            
            // 实际项目中需要将音频流发送到后端
            // 通过 WebSocket 或 WebRTC 发送到摄像机
            console.log('开始语音对讲');
            
            showNotification('语音对讲已开启', 'success');
            
        } catch (error) {
            console.error('获取麦克风失败:', error);
            showNotification('无法访问麦克风', 'error');
        }
    } else {
        voiceTalkActive = false;
        btn.classList.remove('active');
        btn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/>
            </svg>
            开始对讲
        `;
        status.innerHTML = `
            <span class="status-indicator"></span>
            <span>准备就绪</span>
        `;
        
        showNotification('语音对讲已关闭', 'success');
    }
}

// ========================================
// 辅助函数
// ========================================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========================================
// 控制面板交互（海康威视风格）
// ========================================

let currentStreamType = 'sub';  // 当前码流类型（子码流）
let isRecording = false;  // 录像状态

// 码流切换显示
function toggleStreamSelector() {
    const streamTypeText = document.getElementById('streamTypeText');
    if (currentStreamType === 'main') {
        currentStreamType = 'sub';
        streamTypeText.textContent = '子码流';
    } else {
        currentStreamType = 'main';
        streamTypeText.textContent = '主码流';
    }
    console.log('切换码流:', currentStreamType);
    showNotification(`切换到${currentStreamType === 'main' ? '主码流' : '子码流'}`, 'success');
}

// 播放当前流
function playCurrentStream() {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    console.log('播放当前流:', currentStreamType);
    showNotification('开始播放', 'success');

    // 实际项目中需要根据码流类型重新请求播放地址
    // playStream('singleScreenVideo', currentSingleCamera);
}

// 录像控制
function toggleRecording() {
    isRecording = !isRecording;

    const recordBtnCircle = document.getElementById('recordBtnCircle');

    if (isRecording) {
        recordBtnCircle.classList.add('active');
        console.log('开始录像');
        showNotification('录像已开启', 'success');
    } else {
        recordBtnCircle.classList.remove('active');
        console.log('停止录像');
        showNotification('录像已停止', 'success');
    }

    // 实际项目中需要发送录像控制指令到后端
    // fetch('/api/record/toggle', {
    //     method: 'POST',
    //     headers: { 'Content-Type': 'application/json' },
    //     body: JSON.stringify({
    //         camera_id: currentSingleCamera.id,
    //         action: isRecording ? 'start' : 'stop'
    //     })
    // });
}

// 云台控制
function ptzControl(direction) {
    console.log('云台方向:', direction, '速度:', ptzSpeed);
    showNotification(`云台: ${getDirectionName(direction)}`, 'info');

    fetch('/api/ptz/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            direction: direction,
            speed: ptzSpeed,
            action: 'start'
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              console.log('云台控制成功:', data.message);
          } else {
              showNotification(data.error || '云台控制失败', 'error');
          }
      }).catch(error => {
          console.error('云台控制请求失败:', error);
          showNotification('云台控制请求失败', 'error');
      });
}

function ptzStop() {
    console.log('云台停止');
    showNotification('云台已停止', 'info');

    fetch('/api/ptz/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            action: 'stop'
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              console.log('云台停止成功:', data.message);
          } else {
              showNotification(data.error || '云台停止失败', 'error');
          }
      }).catch(error => {
          console.error('云台停止请求失败:', error);
          showNotification('云台停止请求失败', 'error');
      });
}

function ptzZoom(action) {
    console.log('变焦:', action);
    showNotification(`变焦${action === 'in' ? '+' : '-'}`, 'info');

    fetch('/api/ptz/zoom', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            action: action
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              console.log('变焦控制成功:', data.message);
          } else {
              showNotification(data.error || '变焦控制失败', 'error');
          }
      }).catch(error => {
          console.error('变焦控制请求失败:', error);
      });
}

function ptzFocus(action) {
    console.log('聚焦:', action);
    showNotification(`聚焦${action === 'in' ? '+' : '-'}`, 'info');

    fetch('/api/ptz/focus', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            action: action
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              console.log('聚焦控制成功:', data.message);
          } else {
              showNotification(data.error || '聚焦控制失败', 'error');
          }
      }).catch(error => {
          console.error('聚焦控制请求失败:', error);
      });
}

function ptzIris(action) {
    console.log('光圈:', action);
    showNotification(`光圈${action === 'in' ? '+' : '-'}`, 'info');

    fetch('/api/ptz/iris', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            action: action
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              console.log('光圈控制成功:', data.message);
          } else {
              showNotification(data.error || '光圈控制失败', 'error');
          }
      }).catch(error => {
          console.error('光圈控制请求失败:', error);
      });
}

// ========================================
// 其他PTZ功能（灯光、雨刷、辅助聚焦等）
// ========================================

function toggleLight() {
    console.log('切换灯光');
    showNotification('灯光功能暂不支持', 'warning');
}

function toggleWiper() {
    console.log('切换雨刷');
    showNotification('雨刷功能暂不支持', 'warning');
}

function auxFocus() {
    console.log('辅助聚焦');
    showNotification('辅助聚焦功能暂不支持', 'warning');
}

function lensInit() {
    console.log('镜头初始化');
    showNotification('镜头初始化功能暂不支持', 'warning');
}

function toggleManualTrack() {
    console.log('切换手动跟踪');
    showNotification('手动跟踪功能暂不支持', 'warning');
}

// 云台方向名称映射
function getDirectionName(direction) {
    const names = {
        'left_up': '左上',
        'up': '上',
        'right_up': '右上',
        'left': '左',
        'right': '右',
        'left_down': '左下',
        'down': '下',
        'right_down': '右下'
    };
    return names[direction] || direction;
}

// 云台速度滑块拖拽控制
function initPtzSlider() {
    const sliderButton = document.getElementById('ptzSliderButton');
    const sliderBar = document.getElementById('ptzSliderBar');
    const runway = document.querySelector('.ptz-slider-runway');

    if (!sliderButton || !sliderBar || !runway) return;

    let isDragging = false;

    sliderButton.addEventListener('mousedown', function(e) {
        isDragging = true;
        e.preventDefault();
    });

    document.addEventListener('mousemove', function(e) {
        if (!isDragging) return;

        const rect = runway.getBoundingClientRect();
        const percent = Math.min(Math.max((e.clientX - rect.left) / rect.width * 100, 0), 100);

        sliderBar.style.width = percent + '%';
        sliderButton.style.left = percent + '%';

        ptzSpeed = Math.round(percent / 20) + 1;
    });

    document.addEventListener('mouseup', function() {
        if (isDragging) {
            isDragging = false;
            console.log('云台速度:', ptzSpeed);
        }
    });
}

// 鼠标按下/释放云台控制（持续控制）
document.querySelectorAll('.ptz-direction-btn').forEach(btn => {
    btn.addEventListener('mousedown', function() {
        const direction = this.getAttribute('title');
        ptzControl(direction);
    });

    btn.addEventListener('mouseup', function() {
        ptzStop();
    });

    btn.addEventListener('mouseleave', function() {
        ptzStop();
    });
});

// 更新单画面对话框的摄像机信息
function updateSingleScreenInfo(camera) {
    currentSingleCamera = camera;

    const singleCameraName = document.getElementById('singleCameraName');
    const singleChName = document.getElementById('singleChName');

    if (singleCameraName) {
        singleCameraName.textContent = camera.name || camera.camera_name || '摄像机';
    }

    if (singleChName) {
        singleChName.textContent = camera.name || camera.camera_name || 'Camera 01';
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    initPtzSlider();
});

// 如果页面已加载，立即初始化
if (document.readyState === 'complete' || document.readyState === 'interactive') {
    initPtzSlider();
}

// ========================================
// 预置点/巡航管理功能
// ========================================

let presets = [];  // 预置点列表
let patrols = [];   // 巡航路径列表
let currentPtzTab = 'preset';  // 当前标签页

// 标签页切换
function switchPtzTab(tabName) {
    currentPtzTab = tabName;

    const tabPreset = document.getElementById('tabPreset');
    const tabPatrol = document.getElementById('tabPatrol');
    const presetContent = document.getElementById('presetContent');
    const patrolContent = document.getElementById('patrolContent');
    const activeBar = document.getElementById('ptzTabActiveBar');

    if (tabName === 'preset') {
        tabPreset.classList.add('active');
        tabPatrol.classList.remove('active');
        presetContent.style.display = 'block';
        patrolContent.style.display = 'none';
        activeBar.style.left = '0';
    } else {
        tabPreset.classList.remove('active');
        tabPatrol.classList.add('active');
        presetContent.style.display = 'none';
        patrolContent.style.display = 'block';
        activeBar.style.left = '50%';
    }
}

// 预置点管理
function gotoPreset(presetId) {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    console.log('调用预置点:', presetId);

    fetch('/api/ptz/preset/goto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            preset_id: presetId
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              showNotification(data.message, 'success');
          } else {
              showNotification(data.error || '调用预置点失败', 'error');
          }
      }).catch(error => {
          console.error('调用预置点请求失败:', error);
          showNotification('调用预置点请求失败', 'error');
      });
}

function setPreset(presetId) {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    console.log('设置预置点:', presetId);

    fetch('/api/ptz/preset/set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            preset_id: presetId,
            name: `预置点 ${presetId}`
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              showNotification(data.message, 'success');
              // 刷新预置点列表
              loadPresets(currentSingleCamera.id);
          } else {
              showNotification(data.error || '设置预置点失败', 'error');
          }
      }).catch(error => {
          console.error('设置预置点请求失败:', error);
          showNotification('设置预置点请求失败', 'error');
      });
}

function deletePreset(presetId) {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    if (!confirm(`确定删除预置点 ${presetId}？`)) {
        return;
    }

    console.log('删除预置点:', presetId);

    fetch('/api/ptz/preset/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            preset_id: presetId
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              showNotification(data.message, 'success');
              // 刷新预置点列表
              loadPresets(currentSingleCamera.id);
          } else {
              showNotification(data.error || '删除预置点失败', 'error');
          }
      }).catch(error => {
          console.error('删除预置点请求失败:', error);
          showNotification('删除预置点请求失败', 'error');
      });
}

function addNewPreset() {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    const presetName = prompt('请输入预置点名称:');
    if (!presetName) return;

    console.log('添加新预置点:', presetName);

    fetch('/api/ptz/preset/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            name: presetName
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              showNotification(data.message, 'success');
              // 刷新预置点列表
              loadPresets(currentSingleCamera.id);
          } else {
              showNotification(data.error || '添加预置点失败', 'error');
          }
      }).catch(error => {
          console.error('添加预置点请求失败:', error);
          showNotification('添加预置点请求失败', 'error');
      });
}

// 巡航路径管理
function startPatrol(patrolId) {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    console.log('开始巡航:', patrolId);
    showNotification(`开始巡航路径 ${patrolId}`, 'info');

    fetch('/api/ptz/patrol/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            patrol_id: patrolId
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              showNotification(data.message, 'success');
          } else {
              showNotification(data.error || '开始巡航失败', 'error');
          }
      }).catch(error => {
          console.error('开始巡航请求失败:', error);
          showNotification('开始巡航请求失败', 'error');
      });
}

function stopPatrol(patrolId) {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    console.log('停止巡航:', patrolId);

    fetch('/api/ptz/patrol/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            patrol_id: patrolId
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              showNotification(data.message, 'success');
          } else {
              showNotification(data.error || '停止巡航失败', 'error');
          }
      }).catch(error => {
          console.error('停止巡航请求失败:', error);
          showNotification('停止巡航请求失败', 'error');
      });
}

function editPatrol(patrolId) {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    console.log('编辑巡航路径:', patrolId);
    showNotification('编辑巡航路径功能待实现', 'info');
}

function deletePatrol(patrolId) {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    if (!confirm(`确定删除巡航路径 ${patrolId}？`)) {
        return;
    }

    console.log('删除巡航路径:', patrolId);

    fetch('/api/ptz/patrol/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            patrol_id: patrolId
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              showNotification(data.message, 'success');
              // 从UI中移除
              const patrolItem = document.querySelector(`.patrol-item[data-patrol-id="${patrolId}"]`);
              if (patrolItem) {
                  patrolItem.remove();
              }
          } else {
              showNotification(data.error || '删除巡航路径失败', 'error');
          }
      }).catch(error => {
          console.error('删除巡航路径请求失败:', error);
          showNotification('删除巡航路径请求失败', 'error');
      });
}

function addNewPatrol() {
    if (!currentSingleCamera) {
        showNotification('请先选择摄像机', 'warning');
        return;
    }

    const patrolName = prompt('请输入巡航路径名称:');
    if (!patrolName) return;

    console.log('添加新巡航路径:', patrolName);

    fetch('/api/ptz/patrol/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            name: patrolName,
            presets: [],
            durations: []
        })
    }).then(response => response.json())
      .then(data => {
          if (data.success) {
              const newPatrolId = data.patrol_id;
              patrols.push({
                  id: newPatrolId,
                  name: patrolName,
                  points: 0
              });
              showNotification(data.message, 'success');

              // 添加到UI
              const patrolList = document.querySelector('.patrol-list');
              const newPatrolItem = document.createElement('div');
              newPatrolItem.className = 'patrol-item';
              newPatrolItem.setAttribute('data-patrol-id', newPatrolId);
              newPatrolItem.innerHTML = `
                  <div class="patrol-item-content">
                      <span class="patrol-name">${patrolName}</span>
                      <div class="patrol-status">
                          <span class="patrol-points">预置点数: 0</span>
                      </div>
                      <div class="patrol-actions">
                          <button onclick="startPatrol(${newPatrolId})" title="调用">
                              <i class="fa fa-play"></i>
                          </button>
                          <button onclick="stopPatrol(${newPatrolId})" title="停止">
                              <i class="fa fa-stop"></i>
                          </button>
                          <button onclick="editPatrol(${newPatrolId})" title="编辑">
                              <i class="fa fa-edit"></i>
                          </button>
                          <button onclick="deletePatrol(${newPatrolId})" title="删除">
                              <i class="fa fa-trash"></i>
                          </button>
                      </div>
                  </div>
              `;
              patrolList.appendChild(newPatrolItem);
          } else {
              showNotification(data.error || '添加巡航路径失败', 'error');
          }
      }).catch(error => {
          console.error('添加巡航路径请求失败:', error);
          showNotification('添加巡航路径请求失败', 'error');
      });
}