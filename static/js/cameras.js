/**
 * SageSight AI - 摄像机管理页面 JavaScript
 * 支持ONVIF协议发现和注册摄像机
 */
window.Cameras = (function() {

// ========================================
// 全局变量（使用 var 以支持 pjax 多页面脚本共存，避免 let 重复声明冲突）
// ========================================

var cameras = [];
var deleteCameraId = null;
var currentCameraId = null;
var viewMode = 'card';

// 分页相关变量
var currentPage = 1;
var pageSize = 20;
var totalPages = 1;
var totalItems = 0;

// ========================================
// 页面初始化
// ========================================

// 注册页面初始化函数（支持 pjax 局部加载）
SageSight.registerPage('/cameras', function() {
    loadCameras();
    loadStatistics();
    initWebSocket();
});

// ========================================
// WebSocket 初始化
// ========================================

function initWebSocket() {
    // 使用全局 socket 连接（由 main.js 初始化）
    const socket = getSocket();

    if (socket) {
        socket.on('camera_created', function(data) {
            console.log('摄像机已创建，刷新列表');
            // 清除筛选条件，确保新摄像机能够显示
            document.getElementById('filterStatus').value = '';
            document.getElementById('filterEnabled').value = '';
            document.getElementById('filterSearch').value = '';
            // 重置到第一页
            currentPage = 1;
            // 加载摄像机列表
            loadCameras();
            loadStatistics();
        });
        
        socket.on('camera_updated', function(data) {
            console.log('摄像机已更新，刷新列表');
            loadCameras();
            loadStatistics();
        });
        
        socket.on('camera_deleted', function(data) {
            console.log('摄像机已删除，刷新列表');
            loadCameras();
            loadStatistics();
        });
        
        socket.on('stream_status_changed', function(data) {
            console.log('流状态变化，刷新列表');
            loadCameras();
            loadStatistics();
        });
    }
}

// ========================================
// 加载摄像机列表
// ========================================

async function loadCameras() {
    try {
        // 获取筛选条件
        const status = document.getElementById('filterStatus').value;
        const enabled = document.getElementById('filterEnabled').value;
        const search = document.getElementById('filterSearch').value;
        
        // 构建查询参数（后端分页）
        const params = new URLSearchParams({
            page: currentPage,
            per_page: pageSize
        });
        if (status) params.append('status', status);
        if (enabled) params.append('enabled', enabled);
        if (search) params.append('search', search);
        
        const response = await fetch(`/api/cameras?${params.toString()}`);
        const data = await response.json();
        
        // 后端返回分页数据
        cameras = data.cameras || [];
        totalItems = data.total || 0;
        totalPages = data.total_pages || 1;
        
        renderCameras();
        
    } catch (error) {
        console.error('加载摄像机失败:', error);
        showNotification('加载摄像机失败', 'error');
    }
}

// ========================================
// 加载统计信息
// ========================================

async function loadStatistics() {
    try {
        const response = await fetch('/api/cameras/statistics');
        const stats = await response.json();

        // pjax 导航后 DOM 可能已被替换，操作前检查元素是否存在
        const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
        setText('statTotal', stats.total || 0);
        setText('statOnline', stats.online || 0);
        setText('statOffline', stats.offline || 0);
        setText('statEnabled', stats.enabled || 0);

    } catch (error) {
        console.error('加载统计信息失败:', error);
    }
}

// ========================================
// 渲染摄像机列表
// ========================================

function renderCameras() {
    const camerasList = document.getElementById('camerasList');
    const paginationContainer = document.getElementById('paginationContainer');

    // pjax 导航后 DOM 可能已被替换
    if (!camerasList || !paginationContainer) return;

    // 后端分页：cameras已经是当前页的数据，不需要前端切片
    if (cameras.length === 0) {
        camerasList.innerHTML = '';  // 显示空白列表
        paginationContainer.style.display = 'none';
        return;
    }
    
    paginationContainer.style.display = 'flex';
    
    // 直接使用后端返回的当前页数据
    const pageCameras = cameras;
    
    if (viewMode === 'list') {
        camerasList.className = 'cameras-list list-view';
        camerasList.innerHTML = `
            <div class="cameras-list-header">
                <span></span>
                <span>摄像机名称</span>
                <span>状态</span>
                <span>接入协议</span>
                <span>AI检测</span>
                <span>音频对讲</span>
                <span>IP地址</span>
                <span>制造商/型号</span>
                <span>启用</span>
                <span>操作</span>
            </div>
            ${pageCameras.map(camera => renderCameraListItem(camera)).join('')}
        `;
    } else {
        camerasList.className = 'cameras-list';
        camerasList.innerHTML = pageCameras.map(camera => renderCameraCard(camera)).join('');
    }
    
    // 更新分页组件
    updatePagination();
}

// ========================================
// 渲染摄像机卡片
// ========================================

function renderCameraCard(camera) {
    const statusClass = `status-${camera.status}`;
    const statusText = getStatusText(camera.status);
    const manufacturerModel = camera.manufacturer && camera.model 
        ? `${camera.manufacturer} ${camera.model}` 
        : (camera.manufacturer || camera.model || '未知');
    
    const protocolText = camera.protocol || 'rtsp';
    const protocolDisplay = protocolText.toUpperCase();
    const aiDetectionText = camera.ai_detection ? '启用' : '禁用';
    const aiDetectionClass = camera.ai_detection ? 'ai-enabled' : 'ai-disabled';
    const aiDetectionTitle = camera.ai_detection ? 'AI检测已启用' : 'AI检测已禁用';
    const audioTalkText = camera.support_audio_talk ? '支持' : '不支持';
    const audioTalkClass = camera.support_audio_talk ? 'audio-enabled' : 'audio-disabled';
    const audioTalkTitle = camera.support_audio_talk ? '支持音频对讲' : '不支持音频';
    
    return `
        <div class="camera-card" onclick="Cameras.showCameraDetail('${camera.id}')">
            <div class="camera-card-header">
                <div class="camera-icon">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>
                    </svg>
                </div>
                <div class="camera-info">
                    <div class="camera-name">${escapeHtml(camera.name)}</div>
                    <div class="camera-id">${escapeHtml(camera.ip || '无IP')}</div>
                </div>
                <div class="camera-header-badges">
                    ${camera.enabled ? 
                        '<span class="camera-enabled-badge">已启用</span>' : 
                        '<span class="camera-enabled-badge disabled">已禁用</span>'
                    }
                    <span class="camera-status-badge ${statusClass}">${statusText}</span>
                </div>
            </div>
            <div class="camera-card-body">
                <div class="camera-info-grid">
                    <div class="camera-info-item">
                        <span class="camera-info-label">设备型号</span>
                        <span class="camera-info-value" title="${escapeHtml(manufacturerModel)}">${escapeHtml(manufacturerModel)}</span>
                    </div>
                    <div class="camera-info-item">
                        <span class="camera-info-label">接入协议</span>
                        <span class="camera-info-value">
                            <span class="protocol-badge" title="${protocolDisplay}">${protocolDisplay}</span>
                        </span>
                    </div>
                    <div class="camera-info-item">
                        <span class="camera-info-label">AI检测</span>
                        <span class="camera-info-value">
                            <span class="ai-badge ${aiDetectionClass}" title="${aiDetectionTitle}">${aiDetectionText}</span>
                        </span>
                    </div>
                    <div class="camera-info-item">
                        <span class="camera-info-label">音频对讲</span>
                        <span class="camera-info-value">
                            <span class="audio-badge ${audioTalkClass}" title="${audioTalkTitle}">${audioTalkText}</span>
                        </span>
                    </div>
                </div>
            </div>
            <div class="camera-card-footer">
                <div class="camera-actions">
                    <button class="camera-action-btn stream ${camera.status === 'online' ? 'stop' : 'start'}" 
                            onclick="Cameras.toggleStream('${camera.id}', '${camera.status}', event)" 
                            title="${camera.status === 'online' ? '停止流' : '启动流'}">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            ${camera.status === 'online' ? 
                                '<path d="M6 6h12v12H6z"/>' : 
                                '<path d="M8 5v14l11-7z"/>'
                            }
                        </svg>
                        ${camera.status === 'online' ? '停止' : '启动'}
                    </button>
                    <button class="camera-action-btn edit" onclick="Cameras.openEditModal('${camera.id}', event)">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                        </svg>
                        编辑
                    </button>
                    <button class="camera-action-btn delete" onclick="Cameras.openDeleteModal('${camera.id}', '${escapeHtml(camera.name)}', event)">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                        </svg>
                        删除
                    </button>
                </div>
            </div>
        </div>
    `;
}

// ========================================
// 渲染摄像机列表项
// ========================================

function renderCameraListItem(camera) {
    const statusClass = `status-${camera.status}`;
    const statusText = getStatusText(camera.status);
    const manufacturerModel = camera.manufacturer && camera.model 
        ? `${camera.manufacturer} ${camera.model}` 
        : (camera.manufacturer || camera.model || '未知');
    
    const protocolText = camera.protocol || 'rtsp';
    const protocolDisplay = protocolText.toUpperCase();
    const aiDetectionText = camera.ai_detection ? '启用' : '禁用';
    const aiDetectionClass = camera.ai_detection ? 'ai-enabled' : 'ai-disabled';
    const audioTalkText = camera.support_audio_talk ? '支持' : '不支持';
    const audioTalkClass = camera.support_audio_talk ? 'audio-enabled' : 'audio-disabled';
    
    return `
        <div class="camera-list-item" onclick="Cameras.showCameraDetail('${camera.id}')">
            <div class="camera-list-icon">
                <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>
                </svg>
            </div>
            <div class="camera-list-info">
                <div class="camera-list-name">${escapeHtml(camera.name)}</div>
            </div>
            <div class="camera-list-status">
                <span class="camera-status-badge ${statusClass}">${statusText}</span>
            </div>
            <div class="camera-list-protocol">
                <span class="protocol-badge">${protocolDisplay}</span>
            </div>
            <div class="camera-list-ai">
                <span class="ai-badge ${aiDetectionClass}">${aiDetectionText}</span>
            </div>
            <div class="camera-list-audio">
                <span class="audio-badge ${audioTalkClass}">${audioTalkText}</span>
            </div>
            <div class="camera-list-url">${escapeHtml(camera.ip || '无IP')}</div>
            <div class="camera-list-model">${escapeHtml(manufacturerModel)}</div>
            <div class="camera-list-enabled">
                ${camera.enabled ? 
                    '<span style="color: var(--success-color);">启用</span>' : 
                    '<span style="color: var(--text-muted);">禁用</span>'
                }
            </div>
            <div class="camera-list-actions">
                <button class="camera-action-btn stream ${camera.status === 'online' ? 'stop' : 'start'}" 
                        onclick="Cameras.toggleStream('${camera.id}', '${camera.status}', event)" 
                        title="${camera.status === 'online' ? '停止流' : '启动流'}">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        ${camera.status === 'online' ? 
                            '<path d="M6 6h12v12H6z"/>' : 
                            '<path d="M8 5v14l11-7z"/>'
                        }
                    </svg>
                </button>
                <button class="camera-action-btn edit" onclick="Cameras.openEditModal('${camera.id}', event)">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                    </svg>
                </button>
                <button class="camera-action-btn delete" onclick="Cameras.openDeleteModal('${camera.id}', '${escapeHtml(camera.name)}', event)">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                    </svg>
                </button>
            </div>
        </div>
    `;
}

// ========================================
// ONVIF发现
// ========================================

function openDiscoverModal() {
    document.getElementById('discoverModal').classList.add('show');
    // 重置所有Tab状态
    switchDiscoverTab('scan');
    document.getElementById('discoverResults').style.display = 'none';
    document.getElementById('discoverLoading').style.display = 'none';
    document.getElementById('networkScanResults').style.display = 'none';
    document.getElementById('networkScanLoading').style.display = 'none';
}

function closeDiscoverModal() {
    document.getElementById('discoverModal').classList.remove('show');
}

// Tab切换函数
function switchDiscoverTab(tabName) {
    // 更新Tab按钮状态
    const tabs = document.querySelectorAll('.discover-tab');
    tabs.forEach(tab => tab.classList.remove('active'));
    
    // 找到对应的Tab按钮并激活
    const activeTab = document.querySelector(`.discover-tab[onclick*="${tabName}"]`);
    if (activeTab) {
        activeTab.classList.add('active');
    }
    
    // 更新Tab内容显示
    const tabContents = document.querySelectorAll('.discover-tab-content');
    tabContents.forEach(content => content.classList.remove('active'));
    
    const activeContent = document.getElementById(`${tabName}Tab`);
    if (activeContent) {
        activeContent.classList.add('active');
    }
}

// 打开手动添加模态框
function openManualAddModal() {
    document.getElementById('manualAddModal').classList.add('show');
    document.getElementById('onvifRegisterForm').reset();
    // 注意：测试连接功能已取消，无需隐藏测试结果元素
}

// 关闭手动添加模态框
function closeManualAddModal() {
    document.getElementById('manualAddModal').classList.remove('show');
}

// 兼容原有函数名（用于从ONVIF扫描结果点击注册）
function openOnvifRegisterModal(ip, port) {
    // 调试：打印接收的参数
    console.log('openOnvifRegisterModal 参数:', { ip, port });
    closeDiscoverModal();
    openManualAddModal();
    
    // 预填充IP地址和端口
    setTimeout(() => {
        console.log('预填充IP和端口:', { ip, port });
        const ipInput = document.getElementById('onvifIp');
        const portInput = document.getElementById('onvifPort');
        
        if (ipInput) {
            ipInput.value = ip;
            console.log('IP已填充:', ipInput.value);
        } else {
            console.error('找不到onvifIp输入框');
        }
        
        if (portInput && port) {
            portInput.value = port;
            console.log('端口已填充:', portInput.value);
        } else {
            console.error('找不到onvifPort输入框或port参数无效');
        }
    }, 100);
}

function closeOnvifRegisterModal() {
    closeManualAddModal();
}

async function startDiscover() {
    const discoverBtn = document.getElementById('discoverBtn');
    const discoverLoading = document.getElementById('discoverLoading');
    const discoverResults = document.getElementById('discoverResults');
    const discoverList = document.getElementById('discoverList');
    
    discoverBtn.disabled = true;
    discoverLoading.style.display = 'block';
    discoverResults.style.display = 'none';
    
    try {
        const response = await fetch('/api/cameras/onvif/discover?timeout=5');
        const devices = await response.json();
        
        // 调试：打印扫描结果
        console.log('ONVIF扫描结果:', devices);
        if (devices.length > 0) {
            console.log('第一个设备的端口:', devices[0].port);
        }
        
        discoverLoading.style.display = 'none';
        discoverResults.style.display = 'block';
        
        if (devices.length === 0) {
            discoverList.innerHTML = '<p class="no-devices">未发现ONVIF设备</p>';
        } else {
            // 使用表格列表显示发现的设备
            discoverList.innerHTML = `
                <table class="discover-table">
                    <thead>
                        <tr>
                            <th style="width: 150px;">IP地址</th>
                            <th>设备地址</th>
                            <th style="width: 100px;">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${devices.map(device => `
                            <tr>
                                <td>
                                    <div class="discover-device-ip">${escapeHtml(device.ip)}</div>
                                </td>
                                <td>
                                    <div class="discover-device-xaddrs" title="${escapeHtml(device.xaddrs ? device.xaddrs.join(', ') : '')}">
                                        ${escapeHtml(device.xaddrs ? device.xaddrs[0] : '')}
                                    </div>
                                </td>
                                <td class="discover-actions-cell">
                                    <button class="btn btn-primary btn-sm" onclick="Cameras.registerDiscoveredDevice('${escapeHtml(device.ip)}', ${device.port || 80})">
                                        注册
                                    </button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        }
        
    } catch (error) {
        discoverLoading.style.display = 'none';
        discoverResults.style.display = 'block';
        discoverList.innerHTML = `<p class="error-text">发现失败: ${escapeHtml(error.message)}</p>`;
    }
    
    discoverBtn.disabled = false;
}

function registerDiscoveredDevice(ip, port) {
    // 调试：打印传递的参数
    console.log('registerDiscoveredDevice 参数:', { ip, port });
    closeDiscoverModal();
    openOnvifRegisterModal(ip, port);
}

// ========================================
// ONVIF注册
// ========================================

async function registerOnvifCamera() {
    const ip = document.getElementById('onvifIp').value;
    const port = document.getElementById('onvifPort').value || 80;
    const username = document.getElementById('onvifUsername').value;
    const password = document.getElementById('onvifPassword').value;
    const name = document.getElementById('onvifName').value;
    
    if (!ip) {
        showNotification('请输入IP地址', 'error');
        return;
    }
    
    try {
        const response = await fetch('/api/cameras/onvif/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip, port: parseInt(port), username, password, name })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification('摄像机注册成功', 'success');
            closeOnvifRegisterModal();
            loadCameras();
            loadStatistics();
        } else {
            showNotification(data.error || '注册失败', 'error');
        }
        
    } catch (error) {
        showNotification('注册失败: ' + error.message, 'error');
    }
}

// ========================================
// 网络端口扫描
// ========================================

async function startNetworkScan() {
    const ipRange = document.getElementById('scanIpRange').value.trim();
    const ports = document.getElementById('scanPorts').value.trim();
    const timeout = parseInt(document.getElementById('scanTimeout').value) || 3;

    if (!ipRange) {
        showNotification('请输入IP范围', 'error');
        return;
    }

    const scanBtn = document.getElementById('networkScanBtn');
    const scanLoading = document.getElementById('networkScanLoading');
    const scanResults = document.getElementById('networkScanResults');
    const scanList = document.getElementById('networkScanList');
    const scanProgress = document.getElementById('networkScanProgress');

    scanBtn.disabled = true;
    scanLoading.style.display = 'block';
    scanResults.style.display = 'none';
    scanProgress.textContent = '正在扫描网络端口...';

    try {
        const response = await fetch('/api/cameras/network/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ip_range: ipRange,
                ports: ports,
                check_onvif: true,
                timeout: timeout
            })
        });

        const data = await response.json();

        scanLoading.style.display = 'none';
        scanResults.style.display = 'block';

        if (data.devices && data.devices.length > 0) {
            scanList.innerHTML = `
                <table class="discover-table">
                    <thead>
                        <tr>
                            <th>IP地址</th>
                            <th>端口</th>
                            <th>制造商</th>
                            <th>型号</th>
                            <th>认证</th>
                            <th>操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.devices.map(device => `
                            <tr>
                                <td>
                                    <div class="discover-device-ip">${escapeHtml(device.ip)}</div>
                                </td>
                                <td>${device.port}</td>
                                <td>${escapeHtml(device.manufacturer || '-')}</td>
                                <td>${escapeHtml(device.model || '-')}</td>
                                <td>
                                    ${device.need_auth ?
                                        '<span style="color: #ff9800;">需要认证</span>' :
                                        (device.onvif ?
                                            '<span style="color: #52c41a;">已识别</span>' :
                                            '<span style="color: #999;">端口开放</span>')
                                    }
                                </td>
                                <td class="discover-actions-cell">
                                    <button class="algorithm-action-btn edit" onclick="Cameras.registerNetworkDevice('${escapeHtml(device.ip)}', ${device.port})">
                                        <svg viewBox="0 0 24 24" fill="currentColor">
                                            <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
                                        </svg>
                                        注册
                                    </button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
            
            showNotification(`扫描完成，发现 ${data.devices.length} 个设备`, 'success');
        } else {
            scanList.innerHTML = '<p class="no-devices">未发现ONVIF设备</p>';
            showNotification('扫描完成，未发现设备', 'info');
        }
        
    } catch (error) {
        scanLoading.style.display = 'none';
        scanResults.style.display = 'block';
        scanList.innerHTML = `<p class="error-text">扫描失败: ${escapeHtml(error.message)}</p>`;
        showNotification('扫描失败: ' + error.message, 'error');
    }
    
    scanBtn.disabled = false;
}

function registerNetworkDevice(ip, port) {
    closeDiscoverModal();
    openManualAddModal();
    
    // 预填充IP地址和端口
    setTimeout(() => {
        document.getElementById('onvifIp').value = ip;
        document.getElementById('onvifPort').value = port;
    }, 100);
}

// ========================================
// 创建/编辑摄像机
// ========================================

async function openEditModal(cameraId, event) {
    if (event) event.stopPropagation();
    
    try {
        const response = await fetch(`/api/cameras/${cameraId}`);
        const camera = await response.json();
        
        document.getElementById('modalTitle').textContent = '编辑摄像机';
        document.getElementById('cameraId').value = camera.id;
        
        document.getElementById('cameraName').value = camera.name || '';
        document.getElementById('cameraSource').value = camera.source || '';
        document.getElementById('cameraIp').value = camera.ip || '';
        document.getElementById('cameraPort').value = camera.port || 80;
        document.getElementById('cameraUsername').value = camera.username || '';
        document.getElementById('cameraPassword').value = camera.password || '';
        document.getElementById('cameraManufacturer').value = camera.manufacturer || '';
        document.getElementById('cameraModel').value = camera.model || '';
        document.getElementById('cameraDescription').value = camera.description || '';
        document.getElementById('cameraEnabled').value = camera.enabled ? 'true' : 'false';
        
        // 加载目录选项并设置当前目录
        await loadDirectories();
        document.getElementById('cameraDirectory').value = camera.directory_id || '';
        
        document.getElementById('cameraModal').classList.add('show');
        
    } catch (error) {
        showNotification('加载摄像机详情失败', 'error');
    }
}

function closeModal() {
    document.getElementById('cameraModal').classList.remove('show');
}

async function saveCamera() {
    const cameraId = document.getElementById('cameraId').value;
    const isEdit = cameraId !== '';
    
    const directoryId = document.getElementById('cameraDirectory').value;
    
    const formData = {
        name: document.getElementById('cameraName').value,
        source: document.getElementById('cameraSource').value,
        ip: document.getElementById('cameraIp').value,
        port: parseInt(document.getElementById('cameraPort').value) || 80,
        username: document.getElementById('cameraUsername').value,
        password: document.getElementById('cameraPassword').value,
        manufacturer: document.getElementById('cameraManufacturer').value,
        model: document.getElementById('cameraModel').value,
        description: document.getElementById('cameraDescription').value,
        enabled: document.getElementById('cameraEnabled').value === 'true',
        directory_id: directoryId ? parseInt(directoryId) : null
    };
    
    if (!formData.name || !formData.source) {
        showNotification('请填写必填字段', 'error');
        return;
    }
    
    try {
        const url = isEdit ? `/api/cameras/${cameraId}` : '/api/cameras';
        const method = isEdit ? 'PUT' : 'POST';
        
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification(isEdit ? '摄像机已更新' : '摄像机已创建', 'success');
            closeModal();
            loadCameras();
            loadStatistics();
        } else {
            showNotification(data.error || '保存失败', 'error');
        }
    } catch (error) {
        showNotification('保存失败', 'error');
    }
}

// ========================================
// 删除摄像机
// ========================================

function openDeleteModal(cameraId, cameraName, event) {
    event.stopPropagation();
    
    deleteCameraId = cameraId;
    document.getElementById('deleteCameraName').textContent = cameraName;
    document.getElementById('deleteModal').classList.add('show');
}

function closeDeleteModal() {
    document.getElementById('deleteModal').classList.remove('show');
    deleteCameraId = null;
}

async function confirmDelete() {
    if (!deleteCameraId) return;
    
    try {
        const response = await fetch(`/api/cameras/${deleteCameraId}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showNotification('摄像机已删除', 'success');
            closeDeleteModal();
            loadCameras();
            loadStatistics();
        } else {
            const data = await response.json();
            showNotification(data.error || '删除失败', 'error');
        }
    } catch (error) {
        showNotification('删除失败', 'error');
    }
}

// ========================================
// 摄像机详情
// ========================================

async function showCameraDetail(cameraId) {
    currentCameraId = cameraId;
    
    try {
        const response = await fetch(`/api/cameras/${cameraId}`);
        const camera = await response.json();
        
        document.getElementById('cameraDetailTitle').textContent = camera.name;
        
        document.getElementById('cameraDetailContent').innerHTML = `
            <div class="camera-detail-section">
                <h4>基本信息</h4>
                <div class="detail-grid">
                    <div class="detail-item">
                        <span class="detail-label">摄像机ID</span>
                        <span class="detail-value" title="${escapeHtml(camera.id)}">${escapeHtml(camera.id)}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">名称</span>
                        <span class="detail-value" title="${escapeHtml(camera.name)}">${escapeHtml(camera.name)}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">状态</span>
                        <span class="detail-value"><span class="camera-status-badge status-${camera.status}">${getStatusText(camera.status)}</span></span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">启用</span>
                        <span class="detail-value">${camera.enabled ? '是' : '否'}</span>
                    </div>
                </div>
            </div>
            
            <div class="camera-detail-section">
                <h4>连接信息</h4>
                <div class="detail-grid">
                    <div class="detail-item">
                        <span class="detail-label">IP地址</span>
                        <span class="detail-value" title="${escapeHtml(camera.ip || '未设置')}">${escapeHtml(camera.ip || '未设置')}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">端口</span>
                        <span class="detail-value">${camera.port || '未设置'}</span>
                    </div>
                    <div class="detail-item full-width">
                        <span class="detail-label">取流地址</span>
                        <span class="detail-value detail-value-ellipsis" title="${escapeHtml(camera.source)}">${escapeHtml(camera.source)}</span>
                    </div>
                </div>
            </div>
            
            <div class="camera-detail-section">
                <h4>设备信息</h4>
                <div class="detail-grid">
                    <div class="detail-item">
                        <span class="detail-label">制造商</span>
                        <span class="detail-value" title="${escapeHtml(camera.manufacturer || '未知')}">${escapeHtml(camera.manufacturer || '未知')}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">型号</span>
                        <span class="detail-value" title="${escapeHtml(camera.model || '未知')}">${escapeHtml(camera.model || '未知')}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">固件版本</span>
                        <span class="detail-value" title="${escapeHtml(camera.firmware_version || '未知')}">${escapeHtml(camera.firmware_version || '未知')}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">序列号</span>
                        <span class="detail-value" title="${escapeHtml(camera.serial_number || '未知')}">${escapeHtml(camera.serial_number || '未知')}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">MAC地址</span>
                        <span class="detail-value" title="${escapeHtml(camera.mac || '未知')}">${escapeHtml(camera.mac || '未知')}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">PTZ支持</span>
                        <span class="detail-value">${camera.support_move ? '支持' : '不支持'}</span>
                    </div>
                </div>
            </div>
            
            <div class="camera-detail-section">
                <h4>位置信息</h4>
                <div class="detail-grid">
                    <div class="detail-item">
                        <span class="detail-label">安装地址</span>
                        <span class="detail-value" title="${escapeHtml(camera.location || '未设置')}">${escapeHtml(camera.location || '未设置')}</span>
                    </div>
                </div>
            </div>
            
            <div class="camera-detail-section">
                <h4>描述</h4>
                <p class="detail-description">${escapeHtml(camera.description || '无描述')}</p>
            </div>
        `;
        
        document.getElementById('cameraDetailModal').classList.add('show');
        
    } catch (error) {
        showNotification('加载摄像机详情失败', 'error');
    }
}

function closeCameraDetailModal() {
    document.getElementById('cameraDetailModal').classList.remove('show');
    currentCameraId = null;
}

function editCurrentCamera() {
    if (currentCameraId) {
        closeCameraDetailModal();
        openEditModal(currentCameraId);
    }
}

// ========================================
// 视图切换
// ========================================

function setViewMode(mode) {
    viewMode = mode;
    
    document.getElementById('cardViewBtn').classList.toggle('active', mode === 'card');
    document.getElementById('listViewBtn').classList.toggle('active', mode === 'list');
    
    renderCameras();
}

// ========================================
// 筛选重置
// ========================================

function resetFilters() {
    document.getElementById('filterStatus').value = '';
    document.getElementById('filterEnabled').value = '';
    document.getElementById('filterSearch').value = '';
    currentPage = 1;  // 重置到第一页
    loadCameras();
}

function applyFilters() {
    currentPage = 1;  // 筛选条件变化时重置到第一页
    loadCameras();
}

// ========================================
// 辅助函数
// ========================================

function getStatusText(status) {
    const statusMap = {
        'online': '在线',
        'offline': '离线',
        'error': '错误'
    };
    return statusMap[status] || status;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========================================
// 流控制功能
// ========================================

async function toggleStream(cameraId, currentStatus, event) {
    // 阻止事件冒泡，避免触发卡片点击
    if (event) {
        event.stopPropagation();
    }
    
    const isOnline = currentStatus === 'online';
    const action = isOnline ? 'stop' : 'start';
    const actionText = isOnline ? '停止流' : '启动流';
    
    // 确认操作
    if (!confirm(`确定要${actionText}吗？`)) {
        return;
    }
    
    try {
        // 调用流管理API
        const response = await fetch(`/api/stream/${action}/${cameraId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(`${actionText}成功`, 'success');
            
            // 直接更新本地数据中的摄像机状态
            const camera = cameras.find(c => c.id == cameraId);
            if (camera) {
                // 更新状态
                camera.status = isOnline ? 'offline' : 'online';
                
                // 更新流地址（从play_urls中获取）
                if (result.play_urls) {
                    camera.rtmp_stream = result.play_urls.rtmp || '';
                    camera.http_stream = result.play_urls['http-flv'] || '';
                } else if (isOnline) {
                    // 停止流时清空流地址
                    camera.rtmp_stream = '';
                    camera.http_stream = '';
                }
            }
            
            // 重新渲染当前页
            renderCameras();
        } else {
            showNotification(`${actionText}失败: ${result.error || '未知错误'}`, 'error');
        }
        
    } catch (error) {
        console.error(`${actionText}失败:`, error);
        showNotification(`${actionText}失败: ${error.message}`, 'error');
    }
}

// ========================================
// 分页功能
// ========================================

function updatePagination() {
    const paginationInfo = document.getElementById('paginationInfo');
    const pageNumbers = document.getElementById('pageNumbers');
    const prevBtn = document.getElementById('prevPageBtn');
    const nextBtn = document.getElementById('nextPageBtn');

    // pjax 导航后 DOM 可能已被替换
    if (!paginationInfo || !pageNumbers || !prevBtn || !nextBtn) return;
    
    // 更新信息
    const startItem = (currentPage - 1) * pageSize + 1;
    const endItem = Math.min(currentPage * pageSize, totalItems);
    paginationInfo.textContent = `第 ${startItem}-${endItem} 条，共 ${totalItems} 条`;
    
    // 更新按钮状态
    prevBtn.disabled = currentPage === 1;
    nextBtn.disabled = currentPage === totalPages;
    
    // 生成页码按钮
    let pageHtml = '';
    const maxVisiblePages = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);
    
    if (endPage - startPage < maxVisiblePages - 1) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }
    
    if (startPage > 1) {
        pageHtml += `<button class="pagination-btn" onclick="Cameras.goToPage(1)">1</button>`;
        if (startPage > 2) {
            pageHtml += `<span class="pagination-ellipsis">...</span>`;
        }
    }
    
    for (let i = startPage; i <= endPage; i++) {
        pageHtml += `<button class="pagination-btn ${i === currentPage ? 'active' : ''}" onclick="Cameras.goToPage(${i})">${i}</button>`;
    }
    
    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            pageHtml += `<span class="pagination-ellipsis">...</span>`;
        }
        pageHtml += `<button class="pagination-btn" onclick="Cameras.goToPage(${totalPages})">${totalPages}</button>`;
    }
    
    pageNumbers.innerHTML = pageHtml;
}

function goToPage(page) {
    currentPage = page;
    loadCameras();
}

function goToPrevPage() {
    if (currentPage > 1) {
        currentPage--;
        loadCameras();
    }
}

function goToNextPage() {
    if (currentPage < totalPages) {
        currentPage++;
        loadCameras();
    }
}

function changePageSize() {
    pageSize = parseInt(document.getElementById('pageSizeSelect').value);
    currentPage = 1;  // 重置到第一页
    loadCameras();
}

// ========================================
// 目录管理功能
// ========================================

var directories = [];
var currentDirectoryId = null;
var editingDirectoryId = null;
var directoryCameras = [];
var selectedCameraIds = [];

async function loadDirectories() {
    try {
        const response = await fetch('/api/directories');
        const data = await response.json();
        
        if (data.success) {
            directories = data.directories;
            // 更新摄像机表单中的目录选择
            updateDirectorySelect();
        }
        
    } catch (error) {
        console.error('加载目录失败:', error);
    }
}

function updateDirectorySelect() {
    const directorySelect = document.getElementById('cameraDirectory');
    if (!directorySelect) return;
    
    directorySelect.innerHTML = '<option value="">默认目录</option>';
    
    directories.forEach(dir => {
        directorySelect.innerHTML += `<option value="${dir.id}">${escapeHtml(dir.name)}</option>`;
    });
}

function openDirectoryModal() {
    document.getElementById('directoryModal').style.display = 'flex';
    currentDirectoryId = null;
    selectedCameraIds = [];
    loadDirectoryList();
    clearDirectoryCameras();
}

function closeDirectoryModal() {
    document.getElementById('directoryModal').style.display = 'none';
    currentDirectoryId = null;
    selectedCameraIds = [];
}

function clearDirectoryCameras() {
    document.getElementById('directoryCamerasTitle').textContent = '选中目录查看摄像机';
    document.getElementById('directoryCamerasActions').style.display = 'none';
    document.getElementById('directoryCamerasList').innerHTML = '<div class="empty-message">请从左侧选择一个目录查看摄像机</div>';
}

async function loadDirectoryList() {
    try {
        const response = await fetch('/api/directories');
        const data = await response.json();
        
        if (data.success) {
            directories = data.directories;
            renderDirectoryList();
        }
        
    } catch (error) {
        console.error('加载目录列表失败:', error);
        showNotification('加载目录列表失败', 'error');
    }
}

function renderDirectoryList() {
    const directoryList = document.getElementById('directoryList');
    
    if (directories.length === 0) {
        directoryList.innerHTML = '<div class="empty-message">暂无目录，请点击"新建目录"按钮创建</div>';
        return;
    }
    
    // 添加表头
    let html = `
        <div class="directory-list-header">
            <span>目录名称</span>
            <span>数量</span>
            <span>操作</span>
        </div>
    `;
    
    // 添加目录项
    html += directories.map(dir => `
        <div class="directory-item ${currentDirectoryId === dir.id ? 'selected' : ''}" data-directory-id="${dir.id}" data-directory-name="${escapeHtml(dir.name)}">
            <div class="directory-info">
                <svg viewBox="0 0 24 24" fill="currentColor" class="directory-icon">
                    <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/>
                </svg>
                <span class="directory-name">${escapeHtml(dir.name)}</span>
            </div>
            <div class="directory-count">${dir.camera_count || 0}</div>
            <div class="directory-actions">
                <button class="btn-icon" data-edit-id="${dir.id}" title="编辑">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                    </svg>
                </button>
                ${dir.id !== 1 ? `
                    <button class="btn-icon btn-icon-danger" data-delete-id="${dir.id}" data-delete-name="${escapeHtml(dir.name)}" title="删除">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                        </svg>
                    </button>
                ` : ''}
            </div>
        </div>
    `).join('');
    
    directoryList.innerHTML = html;
    
    // 绑定事件
    document.querySelectorAll('.directory-item').forEach(item => {
        item.addEventListener('click', function(e) {
            // 如果点击的是操作按钮，不触发选择
            if (e.target.closest('.btn-icon')) return;
            
            const directoryId = parseInt(this.dataset.directoryId);
            const directoryName = this.dataset.directoryName;
            selectDirectory(directoryId, directoryName);
        });
    });
    
    // 编辑按钮事件
    document.querySelectorAll('[data-edit-id]').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const directoryId = parseInt(this.dataset.editId);
            openEditDirectoryModal(directoryId);
        });
    });
    
    // 删除按钮事件
    document.querySelectorAll('[data-delete-id]').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const directoryId = parseInt(this.dataset.deleteId);
            const directoryName = this.dataset.deleteName;
            deleteDirectory(directoryId, directoryName);
        });
    });
}

async function selectDirectory(directoryId, directoryName) {
    currentDirectoryId = directoryId;
    selectedCameraIds = [];
    
    // 更新选中状态
    renderDirectoryList();
    
    // 加载该目录下的摄像机
    try {
        const response = await fetch(`/api/directories/${directoryId}/cameras`);
        const data = await response.json();
        
        if (data.success) {
            directoryCameras = data.cameras;
            renderDirectoryCameras(directoryName);
        } else {
            showNotification('加载摄像机失败', 'error');
        }
        
    } catch (error) {
        console.error('加载目录摄像机失败:', error);
        showNotification('加载摄像机失败', 'error');
    }
}

function renderDirectoryCameras(directoryName) {
    const titleEl = document.getElementById('directoryCamerasTitle');
    const actionsEl = document.getElementById('directoryCamerasActions');
    const listEl = document.getElementById('directoryCamerasList');
    
    titleEl.textContent = `${directoryName} - 摄像机列表`;
    
    if (directoryCameras.length === 0) {
        actionsEl.style.display = 'none';
        listEl.innerHTML = '<div class="empty-message">该目录下暂无摄像机</div>';
        return;
    }
    
    // 显示移动操作
    actionsEl.style.display = 'flex';
    
    // 加载目标目录选项
    const moveSelect = document.getElementById('moveToDirectorySelect');
    moveSelect.innerHTML = '<option value="">选择目标目录</option>';
    
    directories.forEach(dir => {
        if (dir.id !== currentDirectoryId) {
            moveSelect.innerHTML += `<option value="${dir.id}">${escapeHtml(dir.name)}</option>`;
        }
    });
    
    // 渲染摄像机列表
    listEl.innerHTML = directoryCameras.map(camera => `
        <div class="directory-camera-item">
            <div class="directory-camera-checkbox">
                <input type="checkbox" 
                       id="camera_${camera.id}" 
                       value="${camera.id}"
                       onchange="toggleCameraSelection('${camera.id}')">
            </div>
            <div class="directory-camera-info">
                <div class="directory-camera-icon">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>
                    </svg>
                </div>
                <span class="directory-camera-name">${escapeHtml(camera.name)}</span>
                <span class="directory-camera-status ${camera.status === 'online' ? 'online' : 'offline'}">
                    ${camera.status === 'online' ? '在线' : '离线'}
                </span>
            </div>
        </div>
    `).join('');
}

function toggleCameraSelection(cameraId) {
    const index = selectedCameraIds.indexOf(cameraId);
    
    if (index > -1) {
        selectedCameraIds.splice(index, 1);
    } else {
        selectedCameraIds.push(cameraId);
    }
}

async function moveSelectedCameras() {
    if (selectedCameraIds.length === 0) {
        showNotification('请先选择要移动的摄像机', 'error');
        return;
    }
    
    const targetDirectoryId = document.getElementById('moveToDirectorySelect').value;
    
    if (!targetDirectoryId) {
        showNotification('请选择目标目录', 'error');
        return;
    }
    
    try {
        const response = await fetch(`/api/cameras/batch-update-directory`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                camera_ids: selectedCameraIds,
                directory_id: parseInt(targetDirectoryId)
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(`成功移动 ${selectedCameraIds.length} 个摄像机`, 'success');
            
            // 清空选择
            selectedCameraIds = [];
            
            // 重新加载目录列表和摄像机列表
            loadDirectoryList();
            selectDirectory(currentDirectoryId, directories.find(d => d.id === currentDirectoryId)?.name || '');
            
            // 更新主列表
            loadCameras();
        } else {
            showNotification(result.error || '移动失败', 'error');
        }
        
    } catch (error) {
        console.error('移动摄像机失败:', error);
        showNotification('移动摄像机失败', 'error');
    }
}

function openAddDirectoryModal() {
    editingDirectoryId = null;
    document.getElementById('directoryEditTitle').textContent = '新建目录';
    document.getElementById('directoryName').value = '';
    document.getElementById('directoryDescription').value = '';
    document.getElementById('directorySortOrder').value = '0';
    
    // 加载父目录选项
    loadParentDirectoryOptions();
    
    document.getElementById('directoryEditModal').style.display = 'flex';
}

function openEditDirectoryModal(directoryId) {
    editingDirectoryId = directoryId;
    document.getElementById('directoryEditTitle').textContent = '编辑目录';
    
    // 加载目录信息
    const directory = directories.find(d => d.id === directoryId);
    if (directory) {
        document.getElementById('directoryName').value = directory.name;
        document.getElementById('directoryDescription').value = directory.description || '';
        document.getElementById('directorySortOrder').value = directory.sort_order || 0;
        
        // 加载父目录选项
        loadParentDirectoryOptions(directoryId);
        
        // 设置父目录
        document.getElementById('directoryParent').value = directory.parent_id || '';
    }
    
    document.getElementById('directoryEditModal').style.display = 'flex';
}

function closeDirectoryEditModal() {
    document.getElementById('directoryEditModal').style.display = 'none';
}

function loadParentDirectoryOptions(excludeId = null) {
    const parentSelect = document.getElementById('directoryParent');
    parentSelect.innerHTML = '<option value="">无（根目录）</option>';
    
    directories.forEach(dir => {
        if (dir.id !== excludeId) {
            parentSelect.innerHTML += `<option value="${dir.id}">${escapeHtml(dir.name)}</option>`;
        }
    });
}

async function saveDirectory() {
    const name = document.getElementById('directoryName').value.trim();
    const parentId = document.getElementById('directoryParent').value;
    const description = document.getElementById('directoryDescription').value.trim();
    const sortOrder = parseInt(document.getElementById('directorySortOrder').value) || 0;
    
    if (!name) {
        showNotification('请输入目录名称', 'error');
        return;
    }
    
    const data = {
        name: name,
        parent_id: parentId ? parseInt(parentId) : null,
        description: description,
        sort_order: sortOrder
    };
    
    try {
        let url = '/api/directories';
        let method = 'POST';
        
        if (editingDirectoryId) {
            url = `/api/directories/${editingDirectoryId}`;
            method = 'PUT';
        }
        
        const response = await fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(editingDirectoryId ? '目录更新成功' : '目录创建成功', 'success');
            closeDirectoryEditModal();
            loadDirectoryList();
        } else {
            showNotification(result.error || '操作失败', 'error');
        }
        
    } catch (error) {
        console.error('保存目录失败:', error);
        showNotification('保存目录失败', 'error');
    }
}

async function deleteDirectory(directoryId, directoryName) {
    if (!confirm(`确定要删除目录 "${directoryName}" 吗？\n该目录下的摄像机将移动到默认目录。`)) {
        return;
    }

    try {
        const response = await fetch(`/api/directories/${directoryId}`, {
            method: 'DELETE'
        });

        const result = await response.json();

        if (result.success) {
            showNotification('目录删除成功', 'success');
            loadDirectoryList();
        } else {
            showNotification(result.error || '删除失败', 'error');
        }

    } catch (error) {
        console.error('删除目录失败:', error);
        showNotification('删除目录失败', 'error');
    }
}

// ========================================
// 暴露命名空间：供 HTML onclick/onchange 调用
// ========================================
return {
    applyFilters: applyFilters,
    resetFilters: resetFilters,
    setViewMode: setViewMode,
    goToPage: goToPage,
    goToPrevPage: goToPrevPage,
    goToNextPage: goToNextPage,
    changePageSize: changePageSize,
    openDirectoryModal: openDirectoryModal,
    openDiscoverModal: openDiscoverModal,
    openAddDirectoryModal: openAddDirectoryModal,
    moveSelectedCameras: moveSelectedCameras,
    closeDirectoryModal: closeDirectoryModal,
    closeDirectoryEditModal: closeDirectoryEditModal,
    saveDirectory: saveDirectory,
    closeDiscoverModal: closeDiscoverModal,
    switchDiscoverTab: switchDiscoverTab,
    startDiscover: startDiscover,
    startNetworkScan: startNetworkScan,
    registerOnvifCamera: registerOnvifCamera,
    closeOnvifRegisterModal: closeOnvifRegisterModal,
    openEditModal: openEditModal,
    openManualAddModal: openManualAddModal,
    openDeleteModal: openDeleteModal,
    closeModal: closeModal,
    closeDeleteModal: closeDeleteModal,
    confirmDelete: confirmDelete,
    saveCamera: saveCamera,
    editCurrentCamera: editCurrentCamera,
    showCameraDetail: showCameraDetail,
    closeCameraDetailModal: closeCameraDetailModal,
    toggleStream: toggleStream
};

})();