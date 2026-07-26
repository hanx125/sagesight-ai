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
let aiStreamCanvas = {};  // {screenIndex: canvasElement} AI 流 Canvas 元素（叠加层）
let aiStreamSocket = null;  // AI 流 WebSocket 连接
let singleScreenAIMode = false;  // 单画面是否使用 AI 模式

// ✅ 新增：检测数据缓冲和帧对齐
let detectionBuffer = {};  // {camera_id: [{timestamp, frame_id, detections, tracks, trajectories}]}
let frameSynchronizer = {
    maxBufferMs: 500,  // 最大缓冲500ms
    dropThreshold: 200,  // 丢弃阈值200ms
    bufferSize: 30  // 缓冲队列大小
};

// ✅ 新增：AI播放器（延迟播放缓存帧）
let aiPlayers = {};  // {camera_id: AIPlayer实例}

// ✅ 关键新增：全局消息限流（防止浏览器卡死）
let _globalMessageCounter = 0;
let _globalMessageLimit = 10;  // ✅ 修复：从20改为10，更激进的限流
let _globalMessageResetTime = Date.now();

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
});

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
        
        // ✅ 改造：接收检测数据（JSON格式），不再是base64图片
        socket.on('detection_frame', function(data) {
            // ✅ 调试：输出接收到的检测数据
            console.log('[WebSocket] 收到detection_frame事件:', {
                camera_id: data.camera_id,
                frame_id: data.frame_id,
                frame_seq: data.frame_seq,
                timestamp: data.timestamp,
                detections_count: data.detections ? data.detections.length : 0,
                tracks_count: data.tracks ? data.tracks.length : 0
            });

            // ✅ 性能监控：WebSocket统计
            if (window.PerfMonitor) {
                PerfMonitor.monitorWebSocket('detection_frame', data);
            }

            // ✅ 全局消息限流：超过限制则丢弃
            const now = Date.now();
            if (now - _globalMessageResetTime > 1000) {
                // 每秒重置计数器
                _globalMessageCounter = 0;
                _globalMessageResetTime = now;
            }

            _globalMessageCounter++;
            if (_globalMessageCounter > _globalMessageLimit) {
                // 超过限制，丢弃这条消息
                aiStats.droppedFrames++;
                return;
            }

            const {camera_id, frame_id, frame_seq, timestamp, detections, tracks, trajectories} = data;

            // ✅ 新增：AI播放器处理检测数据（延迟播放）
            if (aiPlayers[camera_id]) {
                const aiPlayer = aiPlayers[camera_id];

                // ✅ 绝对对齐：记录时间戳→帧序号映射
                if (timestamp !== undefined && frame_seq !== undefined) {
                    // 后端时间戳是视频PTS（毫秒）
                    const timestampSec = timestamp / 1000;

                    // 检查前端视频播放时间
                    const video = aiPlayer.videoElement;
                    if (video) {
                        const currentTime = video.currentTime;
                        const diff = Math.abs(currentTime - timestampSec);

                        // ✅ 记录初始偏移（用于绝对对齐）
                        if (!window.timestampOffset) {
                            window.timestampOffset = timestampSec - currentTime;
                            console.log(`[绝对对齐] 记录初始偏移: ${window.timestampOffset.toFixed(2)}s (${(window.timestampOffset*1000).toFixed(0)}ms)`);
                        }

                        // 计算校正后的时间戳
                        const adjustedTimestamp = timestampSec - window.timestampOffset;
                        const adjustedDiff = Math.abs(currentTime - adjustedTimestamp);

                        // 输出对齐信息（仅在有检测目标时）
                        if (detections && detections.length > 0) {
                            console.log(`[绝对对齐] frame_seq=${frame_seq}, 原始diff=${(diff*1000).toFixed(0)}ms, 校正后diff=${(adjustedDiff*1000).toFixed(0)}ms ${adjustedDiff < 0.5 ? '✓' : '✗'}`);
                        }

                        // 建立 currentTime → frame_seq 映射
                        if (!window.timeToFrameSeq) window.timeToFrameSeq = {};
                        const timeKey = currentTime.toFixed(2);
                        window.timeToFrameSeq[timeKey] = frame_seq;
                    }
                }

                // ✅ 关键：第一次接收检测数据时，同步帧序号
                if (!aiPlayer.isFrameSeqSynchronized && frame_seq !== undefined) {
                    aiPlayer.frameSeqOffset = frame_seq;
                    aiPlayer.isFrameSeqSynchronized = true;
                    console.log(`[AIPlayer-${camera_id}] 第一次同步帧序号: offset=${frame_seq}`);
                }

                console.log(`[AIPlayer-${camera_id}] 添加检测数据到缓存: frame_seq=${frame_seq}, timestamp=${timestamp}`);

                // ✅ 修复：添加检测数据到AIPlayer缓存（包含frame_seq）
                aiPlayer.addDetectionData({
                    frame_id: frame_id,
                    frame_seq: frame_seq,  // ✅ 关键：传递frame_seq
                    timestamp: timestamp,
                    detections: detections || [],
                    tracks: tracks || [],
                    trajectories: trajectories || {}
                });

                // 更新统计
                aiStats.totalFrames++;
                return;  // ✅ 返回，不使用旧的缓冲逻辑
            } else {
                console.warn(`[AIPlayer] 未找到camera_id=${camera_id}的AI播放器`);
            }

            // ✅ 备用方案：旧的缓冲逻辑（保留兼容性）
            // 添加到检测数据缓冲队列
            if (!detectionBuffer[camera_id]) {
                detectionBuffer[camera_id] = [];
            }

            // 构造检测帧数据
            const detectionFrame = {
                timestamp: Date.now(),  // 接收时间戳
                frame_id: frame_id,
                detections: detections || [],
                tracks: tracks || [],
                trajectories: trajectories || {}
            };

            // 添加到缓冲队列
            detectionBuffer[camera_id].push(detectionFrame);

            // 限制缓冲队列长度
            if (detectionBuffer[camera_id].length > frameSynchronizer.bufferSize) {
                detectionBuffer[camera_id].shift();
            }

            // 更新统计
            aiStats.totalFrames++;

            // 触发渲染（使用requestAnimationFrame节流）
            if (!_rafScheduled) {
                _rafScheduled = true;
                requestAnimationFrame(_flushDetections);
            }
        });

        // ✅ 新增：接收AI视频流帧（用于视频文件播放）
        socket.on('ai_stream_frame', function(data) {
            // ✅ 检查是否订阅了AI流（避免接收不需要的流）
            const {camera_id} = data;
            if (!aiStreamSubscriptions[camera_id] || aiStreamSubscriptions[camera_id].screenIndexes.size === 0) {
                // 没有订阅，丢弃消息（避免浪费内存）
                return;
            }

            // ✅ 性能监控：WebSocket统计
            if (window.PerfMonitor) {
                PerfMonitor.monitorWebSocket('ai_stream_frame', data);
            }

            // ✅ 全局消息限流：超过限制则丢弃
            const now = Date.now();
            if (now - _globalMessageResetTime > 1000) {
                // 每秒重置计数器
                _globalMessageCounter = 0;
                _globalMessageResetTime = now;
            }

            _globalMessageCounter++;
            if (_globalMessageCounter > _globalMessageLimit) {
                // 超过限制，丢弃这条消息
                aiStats.droppedFrames++;
                return;
            }

            const {task_id, frame, timestamp} = data;

            // 调试：打印接收到的消息（降低频率）
            if (_globalMessageCounter === 1) {
                console.log(`[AI流] 收到消息: task_id=${task_id}, camera_id=${camera_id}, 计数=${_globalMessageCounter}/${_globalMessageLimit}`);
            }

            // 渲染AI视频帧到Canvas（用于视频文件类型）
            renderAIStreamFrame(camera_id, frame);

            // 更新统计
            aiStats.totalFrames++;
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
        ai_mode: useAIMode || isVideoFile,  // 视频文件强制AI模式
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
    // ✅ 新增：停止AI播放器
    // 从containerId中提取screenIndex（例如：'screenContainer0' -> 0）
    const screenIndexMatch = containerId.match(/(\d+)$/);
    if (screenIndexMatch) {
        const screenIndex = parseInt(screenIndexMatch[1]);
        if (screens[screenIndex] && screens[screenIndex].camera_id) {
            const cameraId = screens[screenIndex].camera_id;

            // 停止AI播放器
            if (aiPlayers[cameraId]) {
                aiPlayers[cameraId].stop();
                console.log(`[AIPlayer-${cameraId}] 已停止`);
                delete aiPlayers[cameraId];
            }
        }
    }

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
// AI 视频流播放（Canvas 渲染）
// ========================================

/**
 * ✅ 改造：AI模式播放
 *
 * 架构：
 * - ZLM原始流播放器（底层，WebRTC）
 * - Canvas叠加层（透明，绘制检测框）
 * - WebSocket检测数据（JSON，坐标已映射到原始分辨率）
 */
function playWithAIStream(screenIndex, camera) {
    const containerId = `screenContainer${screenIndex}`;
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`找不到播放容器: ${containerId}`);
        return;
    }

    // ✅ 改造：创建video元素（ZLM原始流）+ Canvas叠加层
    container.innerHTML = `
        <div style="position: relative; width: 100%; height: 100%;">
            <!-- 视频播放器（ZLM原始流） -->
            <video id="webrtcVideoAI${screenIndex}"
                   autoplay
                   playsinline
                   style="width:100%;height:100%;background-color:#000;object-fit:contain;">
            </video>

            <!-- Canvas叠加层（透明，绘制检测框） -->
            <canvas id="aiCanvas${screenIndex}"
                    style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10;">
            </canvas>
        </div>
    `;

    const canvas = document.getElementById(`aiCanvas${screenIndex}`);
    aiStreamCanvas[screenIndex] = canvas;

    // ✅ 改造：播放ZLM原始流（使用WebRTC）
    playWebRTCForAI(screenIndex, camera);

    // ✅ 不再启用后端AI流（已禁用）
    // enableAIStream(camera.id);

    console.log(`AI 模式已启用: camera_id=${camera.id}, screen_index=${screenIndex}`);
}

/**
 * ✅ 新增：为AI模式播放WebRTC流（复用ZLMRTCClient逻辑）
 */
async function playWebRTCForAI(screenIndex, camera) {
    const videoElement = document.getElementById(`webrtcVideoAI${screenIndex}`);

    // 检查 ZLMRTCClient 是否已加载
    if (typeof window.ZLMRTCClient === 'undefined') {
        console.warn('ZLMRTCClient 未加载，尝试动态加载...');
        loadZLMRTCClient().then(() => {
            playWebRTCForAI(screenIndex, camera);
        }).catch(err => {
            console.error('ZLMRTCClient 加载失败:', err);
        });
        return;
    }

    // 优先使用 RTC 流地址
    const rtcUrl = camera.rtc_stream || camera.rtcs_stream;

    if (!rtcUrl) {
        console.error('没有RTC流地址');
        return;
    }

    console.log('AI模式 WebRTC播放地址:', rtcUrl);

    // ✅ 关键修复1：通知后端开始推送检测数据，并获取当前frame_seq
    let currentFrameSeq = 0;
    try {
        const response = await fetch(`/api/ai-stream/start/${camera.id}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                screen_index: screenIndex
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        currentFrameSeq = data.frame_seq || 0;

        console.log(`[AI模式] 后端通知成功，当前frame_seq=${currentFrameSeq}`);

    } catch (error) {
        console.error('[AI模式] 通知后端失败:', error);
    }

    // ✅ 改造：创建AI播放器（使用后端返回的frame_seq）
    if (!aiPlayers[camera.id]) {
        aiPlayers[camera.id] = new AIPlayer(camera.id, {
            cacheDuration: 20,       // 缓存20秒
            cacheFps: 25,            // 与后端解码帧率一致（25fps）
            cacheWidth: 640,         // 与检测分辨率一致
            cacheHeight: 640,        // 与检测分辨率一致
            playbackDelay: 3000,     // ✅ 调整：延迟3秒播放（进一步减少延迟）
            playbackFps: 25,         // 播放帧率与捕获帧率一致（25fps）
            initialFrameSeq: currentFrameSeq  // 初始帧序号
        });
    }

    const aiPlayer = aiPlayers[camera.id];
    const canvas = aiStreamCanvas[screenIndex];

    // ✅ 关键：隐藏video元素（实时流），显示Canvas（缓存帧）
    videoElement.style.display = 'none';  // 隐藏实时流
    canvas.style.display = 'block';       // 显示缓存帧

    try {
        const containerId = `screenContainer${screenIndex}`;

        // 销毁已有实例
        if (webrtcPlayers[containerId]) {
            webrtcPlayers[containerId].close();
            webrtcPlayers[containerId] = null;
        }

        // 创建WebRTC播放器（后台捕获实时流）
        const player = new window.ZLMRTCClient.Endpoint({
            element: videoElement,
            debug: false,
            zlmsdpUrl: rtcUrl,
            simulecast: false,
            useCamera: false,
            audioEnable: true,  // ✅ 保留音频（实时流音频）
            videoEnable: true,
            recvOnly: true,
            usedatachannel: false
        });

        webrtcPlayers[containerId] = player;

        // 播放成功回调
        player.on(window.ZLMRTCClient.Events.WEBRTC_ON_REMOTE_STREAMS, (e) => {
            console.log('AI模式 WebRTC播放成功（后台捕获实时流）', e.streams);

            // ✅ 调试：输出视频元素信息
            console.log('视频元素信息:', {
                videoWidth: videoElement.videoWidth,
                videoHeight: videoElement.videoHeight,
                readyState: videoElement.readyState
            });

            // ✅ 关键：启动AI播放器
            if (canvas && videoElement.videoWidth > 0) {
                canvas.width = 640;
                canvas.height = 640;
                console.log(`Canvas画布尺寸: ${canvas.width}x${canvas.height}（显示缓存帧）`);

                // 启动AI播放器（后台捕获实时流 + 延迟播放）
                aiPlayer.start(videoElement, canvas);
                console.log(`[AIPlayer-${camera.id}] 已启动（延迟3秒播放）`);

            } else {
                console.warn('视频元素尺寸为0，延迟启动AI播放器');

                // 监听loadedmetadata事件
                videoElement.addEventListener('loadedmetadata', () => {
                    canvas.width = 640;
                    canvas.height = 640;
                    console.log(`Canvas画布尺寸（延迟）: ${canvas.width}x${canvas.height}`);

                    aiPlayer.start(videoElement, canvas);
                    console.log(`[AIPlayer-${camera.id}] 已启动（延迟）`);
                });
            }
        });

        // ICE协商出错
        player.on(window.ZLMRTCClient.Events.WEBRTC_ICE_CANDIDATE_ERROR, (e) => {
            console.error('AI模式 WebRTC ICE协商出错:', e);
        });

        // offer/answer交换失败
        player.on(window.ZLMRTCClient.Events.WEBRTC_OFFER_ANWSER_EXCHANGE_FAILED, (e) => {
            console.error('AI模式 WebRTC offer/answer交换失败:', e);
        });

        // 连接状态变化
        player.on(window.ZLMRTCClient.Events.WEBRTC_ON_CONNECTION_STATE_CHANGE, (state) => {
            console.log('AI模式 WebRTC连接状态:', state);
        });

    } catch (error) {
        console.error('AI模式 WebRTC播放器创建失败:', error);
    }
}

// ========================================
// 视频文件AI流播放（纯Canvas，无ZLM流）
// ========================================

/**
 * 视频文件 + AI模式：使用Canvas + WebSocket接收AI流
 * 后端推送的画框帧，Canvas直接显示完整帧
 */
function playVideoFileWithAI(screenIndex, camera) {
    const containerId = `screenContainer${screenIndex}`;
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`找不到播放容器: ${containerId}`);
        return;
    }

    // 创建Canvas容器（没有video元素）
    container.innerHTML = `
        <div style="position: relative; width: 100%; height: 100%; background: #000;">
            <!-- Canvas显示AI视频流 -->
            <!-- Canvas尺寸由renderAIStreamFrame动态计算，保持原始宽高比 -->
            <canvas id="aiStreamCanvas${screenIndex}">
            </canvas>

            <!-- 状态提示（可选） -->
            <div id="aiStreamStatus${screenIndex}" style="position:absolute;top:10px;left:10px;color:#fff;font-size:12px;background:rgba(0,0,0,0.5);padding:4px 8px;border-radius:4px;">
                等待AI视频流...
            </div>
        </div>
    `;

    const canvas = document.getElementById(`aiStreamCanvas${screenIndex}`);
    aiStreamCanvas[screenIndex] = canvas;

    // 订阅AI视频流
    subscribeAIStream(camera.id, screenIndex);

    console.log(`视频文件AI流模式已启用: camera_id=${camera.id}, screen_index=${screenIndex}`);
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
// 旧版函数（向后兼容）
// ========================================

/**
 * @deprecated 使用 playVideoFileWithAI 代替
 * 视频文件播放：只使用AI流（后端推送的画框帧）
 * 没有 ZLM 原始流，Canvas 直接显示后端推送的完整帧
 */
function playWithAIStreamOnly(screenIndex, camera) {
    playVideoFileWithAI(screenIndex, camera);
}

/**
 * 订阅AI视频流（WebSocket）
 */
let aiStreamSubscriptions = {}; // cameraId -> {screenIndexes: Set, image: Image}

function subscribeAIStream(cameraId, screenIndex) {
    // 初始化订阅记录
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
 * 渲染AI视频帧到Canvas
 *
 * ✅ 修复：添加帧率限制，防止浏览器卡死
 */
function renderAIStreamFrame(cameraId, frameBase64) {
    // 查找使用该摄像机的画面
    let rendered = false;
    
    screens.forEach((screen, index) => {
        if (screen && screen.camera_id === cameraId && aiStreamMode[index]) {
            const canvas = aiStreamCanvas[index];
            if (!canvas) {
                console.warn(`[AI流] 找不到Canvas: screen_index=${index}`);
                return;
            }
            
            const ctx = canvas.getContext('2d');
            if (!ctx) {
                console.warn(`[AI流] 找不到Canvas上下文: screen_index=${index}`);
                return;
            }
            
            // 创建Image对象并渲染
            const img = new Image();
            img.onload = function() {
                // 调整Canvas尺寸匹配图片
                if (canvas.width !== img.width || canvas.height !== img.height) {
                    canvas.width = img.width;
                    canvas.height = img.height;
                }
                
                // 清空Canvas并绘制新帧
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, 0, 0);
                rendered = true;
            };
            img.onerror = function() {
                console.error(`[AI流] 图片加载失败: camera_id=${cameraId}`);
            };
            img.src = 'data:image/jpeg;base64,' + frameBase64;
        }
    });
    
    // 单画面弹窗渲染
    if (currentSingleCamera && currentSingleCamera.id === cameraId && singleScreenAIMode) {
        const canvas = document.getElementById('aiCanvasSingle');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            const img = new Image();
            img.onload = function() {
                if (canvas.width !== img.width || canvas.height !== img.height) {
                    canvas.width = img.width;
                    canvas.height = img.height;
                }
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, 0, 0);
            };
            img.src = 'data:image/jpeg;base64,' + frameBase64;
        }
    }
}

// ========================================
// AI 流渲染优化：rAF节流 + Image复用
// ========================================

let _latestAIFrames = {};   // cameraId -> 最新帧base64
let _rafScheduled = false;
let _aiImageCache = {};      // 复用Image对象: key -> Image

function _getAIImage(key) {
    if (!_aiImageCache[key]) {
        _aiImageCache[key] = new Image();
    }
    return _aiImageCache[key];
}

/**
 * ✅ 改造：刷新检测数据到Canvas叠加层
 *
 * 架构：
 * - ZLM原始流播放器（底层）
 * - Canvas叠加层（透明，绘制检测框）
 * - WebSocket检测数据（JSON，坐标已映射到原始分辨率）
 *
 * ✅ 新增：性能监控 + 主线程保护
 */
function _flushDetections() {
    // ✅ 性能监控：记录函数开始时间
    const startTime = performance.now();

    _rafScheduled = false;

    // ✅ 主线程保护：如果上次渲染还未完成，跳过
    if (_flushDetections.running) {
        console.warn('[性能警告] _flushDetections 正在运行，跳过本次调用');
        return;
    }

    _flushDetections.running = true;

    try {
        // 遍历所有摄像机
        for (const cameraId in detectionBuffer) {
            const buffer = detectionBuffer[cameraId];
            if (buffer.length === 0) continue;

            // 帧对齐：找到最新的检测帧
            const latestFrame = buffer[buffer.length - 1];

            // ✅ 优化：限制检测框数量（避免绘制过多）
            const maxDetections = 20;
            const limitedDetections = latestFrame.detections.slice(0, maxDetections);
            const limitedTracks = latestFrame.tracks.slice(0, maxDetections);

            // 多画面渲染
            screens.forEach((screen, index) => {
                if (screen && screen.camera_id === cameraId && aiStreamMode[index]) {
                    renderDetectionsOnCanvas(index, {
                        ...latestFrame,
                        detections: limitedDetections,
                        tracks: limitedTracks
                    });
                }
            });

            // 单画面弹窗渲染
            if (currentSingleCamera && currentSingleCamera.id === cameraId && singleScreenAIMode) {
                renderDetectionsOnCanvas('single', {
                    ...latestFrame,
                    detections: limitedDetections,
                    tracks: limitedTracks
                });
            }

            // ✅ 优化：限制缓冲区大小（避免内存堆积）
            if (buffer.length > 10) {
                detectionBuffer[cameraId] = buffer.slice(-10);
            }

            // 清理过期的检测帧
            const now = Date.now();
            detectionBuffer[cameraId] = buffer.filter(
                frame => now - frame.timestamp < frameSynchronizer.maxBufferMs
            );
        }
    } finally {
        _flushDetections.running = false;

        // ✅ 性能监控：记录函数执行时间
        const endTime = performance.now();
        const duration = endTime - startTime;

        if (duration > 16.67) {  // 超过一帧时间（60fps）
            console.warn(`[性能警告] _flushDetections 执行时间过长: ${duration.toFixed(2)}ms`);
        }

        // 记录到性能监控
        if (window.PerfMonitor) {
            PerfMonitor.monitorCanvasRender(duration);
        }
    }
}

/**
 * 在Canvas叠加层上绘制检测框
 */
function renderDetectionsOnCanvas(screenIndex, detectionFrame) {
    const canvas = aiStreamCanvas[screenIndex];
    if (!canvas) return;

    const ctx = canvas.getContext('2d');

    // 清空Canvas（保持透明）
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 绘制检测框
    _drawDetections(ctx, detectionFrame.detections);

    // 绘制跟踪框
    _drawTracks(ctx, detectionFrame.tracks);

    // 绘制轨迹
    _drawTrajectories(ctx, detectionFrame.trajectories);

    // 更新统计
    aiStats.renderedFrames++;
}

/**
 * 绘制检测框
 */
function _drawDetections(ctx, detections) {
    if (!detections || detections.length === 0) return;

    ctx.strokeStyle = '#00FF00';
    ctx.lineWidth = 2;
    ctx.font = '14px Arial';
    ctx.fillStyle = '#00FF00';

    detections.forEach(det => {
        const {bbox, class_name, confidence} = det;

        // 绘制矩形框
        ctx.strokeRect(bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]);

        // 绘制标签
        const label = `${class_name}: ${(confidence * 100).toFixed(0)}%`;
        const textWidth = ctx.measureText(label).width;

        // 绘制标签背景
        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        ctx.fillRect(bbox[0], bbox[1] - 20, textWidth + 10, 20);

        // 绘制标签文本
        ctx.fillStyle = '#00FF00';
        ctx.fillText(label, bbox[0] + 5, bbox[1] - 5);
    });
}

/**
 * 绘制跟踪框
 */
function _drawTracks(ctx, tracks) {
    if (!tracks || tracks.length === 0) return;

    ctx.strokeStyle = '#FF0000';
    ctx.lineWidth = 2;
    ctx.font = '12px Arial';
    ctx.fillStyle = '#FF0000';

    tracks.forEach(track => {
        const {bbox, track_id, class_name} = track;

        // 绘制矩形框
        ctx.strokeRect(bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]);

        // 绘制ID
        const label = `ID: ${track_id}`;
        ctx.fillText(label, bbox[0], bbox[3] + 20);
    });
}

/**
 * 绘制轨迹
 */
function _drawTrajectories(ctx, trajectories) {
    if (!trajectories || Object.keys(trajectories).length === 0) return;

    ctx.strokeStyle = '#FFFF00';
    ctx.lineWidth = 2;

    Object.values(trajectories).forEach(points => {
        if (!points || points.length < 2) return;

        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);

        points.forEach(point => {
            ctx.lineTo(point.x, point.y);
        });

        ctx.stroke();
    });
}

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

    // 如果从 AI 模式切换到普通模式，禁用 AI 流
    if (currentAIMode) {
        disableAIStream(screen.camera.id);
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

    singleScreenAIMode = !singleScreenAIMode;

    // 停止当前播放
    stopStream('singleScreenVideo');

    // 如果从 AI 模式切换到普通模式，禁用 AI 流
    if (!singleScreenAIMode) {
        disableAIStream(currentSingleCamera.id);
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
                style="width:100%;height:100%;background-color:#000;object-fit:contain;">
        </canvas>
    `;

    const canvas = document.getElementById('singleAICanvas');
    aiStreamCanvas['single'] = canvas;

    // 启用 AI 流
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