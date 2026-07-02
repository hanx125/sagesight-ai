/**
 * SageSight AI - 系统管理页面 JavaScript
 */

// ========================================
// 全局变量
// ========================================

let servers = [];
let currentServerId = null;
let deleteServerId = null;

// ========================================
// 页面初始化
// ========================================

document.addEventListener('DOMContentLoaded', function() {
    loadStreamingServers();
    initWebSocket();
});

// ========================================
// WebSocket 初始化
// ========================================

function initWebSocket() {
    if (typeof io !== 'undefined') {
        const socket = io('/ws', {
            transports: ['websocket', 'polling']
        });
        
        socket.on('streaming_server_created', function(data) {
            loadStreamingServers();
        });
        
        socket.on('streaming_server_updated', function(data) {
            loadStreamingServers();
        });
        
        socket.on('streaming_server_deleted', function(data) {
            loadStreamingServers();
        });
    }
}

// ========================================
// 标签页切换
// ========================================

function switchTab(tabId) {
    // 更新标签按钮状态
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    
    // 更新标签面板状态
    document.querySelectorAll('.tab-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === tabId + 'Tab');
    });
}

// ========================================
// 加载流媒体服务器列表
// ========================================

async function loadStreamingServers() {
    try {
        const response = await fetch('/api/system/streaming-servers');
        servers = await response.json();
        
        renderServersList();
        
    } catch (error) {
        console.error('加载流媒体服务器失败:', error);
        showNotification('加载流媒体服务器失败', 'error');
    }
}

// ========================================
// 渲染服务器列表
// ========================================

function renderServersList() {
    const serversList = document.getElementById('serversList');
    
    if (servers.length === 0) {
        serversList.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>
                </svg>
                <p>暂无流媒体服务器配置</p>
                <button class="btn btn-primary" onclick="openCreateServerModal()">添加服务器</button>
            </div>
        `;
        return;
    }
    
    serversList.innerHTML = servers.map(server => {
        const statusClass = server.status === 'online' ? 'online' : 
                           (server.status === 'offline' ? 'offline' : 'unknown');
        const statusText = server.status === 'online' ? '在线' : 
                          (server.status === 'offline' ? '离线' : '未知');
        
        const typeLabel = getServerTypeLabel(server.server_type);
        const protocolLabel = getProtocolLabel(server.protocol);
        
        return `
            <div class="server-card ${server.is_default ? 'default' : ''} ${!server.enabled ? 'disabled' : ''}">
                <div class="server-card-header">
                    <div class="server-icon">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>
                        </svg>
                    </div>
                    <div class="server-info">
                        <h4 class="server-name">${escapeHtml(server.name)}</h4>
                        <span class="server-type">${typeLabel}</span>
                    </div>
                    <div class="server-status-badge ${statusClass}">
                        ${statusText}
                    </div>
                    ${server.is_default ? '<span class="default-badge">默认</span>' : ''}
                </div>
                
                <div class="server-card-body">
                    <div class="server-detail-row">
                        <span class="detail-label">主机地址:</span>
                        <span class="detail-value">${escapeHtml(server.host)}</span>
                    </div>
                    <div class="server-detail-row">
                        <span class="detail-label">RTMP端口:</span>
                        <span class="detail-value">${server.rtmp_port || 1935}</span>
                    </div>
                    <div class="server-detail-row">
                        <span class="detail-label">RTSP端口:</span>
                        <span class="detail-value">${server.rtsp_port || 554}</span>
                    </div>
                    <div class="server-detail-row">
                        <span class="detail-label">HTTP端口:</span>
                        <span class="detail-value">${server.http_port || 88}</span>
                    </div>
                    <div class="server-detail-row">
                        <span class="detail-label">RTC端口:</span>
                        <span class="detail-value">${server.rtc_port || 8000}</span>
                    </div>
                    ${server.secret ? `
                    <div class="server-detail-row">
                        <span class="detail-label">API密钥:</span>
                        <span class="detail-value">${escapeHtml(server.secret)}</span>
                    </div>
                    ` : ''}
                    ${server.media_server_id ? `
                    <div class="server-detail-row">
                        <span class="detail-label">服务器ID:</span>
                        <span class="detail-value">${escapeHtml(server.media_server_id)}</span>
                    </div>
                    ` : ''}
                    ${server.hook_url_prefix ? `
                    <div class="server-detail-row">
                        <span class="detail-label">回调URL前缀:</span>
                        <span class="detail-value">${escapeHtml(server.hook_url_prefix)}</span>
                    </div>
                    ` : ''}
                    <div class="server-detail-row">
                        <span class="detail-label">协议:</span>
                        <span class="detail-value">${protocolLabel}</span>
                    </div>
                    ${server.description ? `
                    <div class="server-detail-row">
                        <span class="detail-label">描述:</span>
                        <span class="detail-value">${escapeHtml(server.description)}</span>
                    </div>
                    ` : ''}
                </div>
                
                <div class="server-card-footer">
                    <div class="server-actions">
                        <button class="server-btn" onclick="checkServerStatus(${server.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17h-2v-2h2v2zm0-4h-2V7h2v8z"/>
                            </svg>
                            检测状态
                        </button>
                        ${!server.is_default ? `
                        <button class="server-btn" onclick="setDefaultServer(${server.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/>
                            </svg>
                            设为默认
                        </button>
                        ` : ''}
                        <button class="server-btn" onclick="openEditServerModal(${server.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                            </svg>
                            编辑
                        </button>
                        ${!server.is_default ? `
                        <button class="server-btn danger" onclick="openDeleteServerModal(${server.id}, '${escapeHtml(server.name)}')">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                            </svg>
                            删除
                        </button>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

// ========================================
// 获取服务器类型标签
// ========================================

function getServerTypeLabel(type) {
    const labels = {
        'zlmediakit': 'ZLMediaKit'
    };
    return labels[type] || type;
}

// ========================================
// 获取协议标签
// ========================================

function getProtocolLabel(protocol) {
    const labels = {
        'rtmp': 'RTMP',
        'rtsp': 'RTSP',
        'hls': 'HLS',
        'webrtc': 'WebRTC'
    };
    return labels[protocol] || protocol;
}

// ========================================
// 添加/编辑服务器模态框
// ========================================

function openCreateServerModal() {
    currentServerId = null;
    document.getElementById('serverModalTitle').textContent = '添加流媒体服务器';
    document.getElementById('serverForm').reset();
    document.getElementById('serverEnabled').checked = true;
    document.getElementById('serverModal').classList.add('show');
}

function openEditServerModal(serverId) {
    currentServerId = serverId;
    const server = servers.find(s => s.id === serverId);
    
    if (!server) return;
    
    document.getElementById('serverModalTitle').textContent = '编辑流媒体服务器';
    document.getElementById('serverId').value = serverId;
    document.getElementById('serverName').value = server.name;
    document.getElementById('serverType').value = server.server_type;
    document.getElementById('serverHost').value = server.host;
    document.getElementById('serverRtmpPort').value = server.rtmp_port || 1935;
    document.getElementById('serverRtspPort').value = server.rtsp_port || 554;
    document.getElementById('serverHttpPort').value = server.http_port || 88;
    document.getElementById('serverRtcPort').value = server.rtc_port || 8000;
    document.getElementById('serverSecret').value = server.secret || '';
    document.getElementById('serverMediaServerId').value = server.media_server_id || '';
    document.getElementById('serverHookUrlPrefix').value = server.hook_url_prefix || '';
    document.getElementById('serverProtocol').value = server.protocol;
    document.getElementById('serverDescription').value = server.description || '';
    document.getElementById('serverEnabled').checked = server.enabled;
    document.getElementById('serverDefault').checked = server.is_default;
    
    document.getElementById('serverModal').classList.add('show');
}

function closeServerModal() {
    document.getElementById('serverModal').classList.remove('show');
}

// ========================================
// 保存服务器
// ========================================

async function saveServer() {
    const name = document.getElementById('serverName').value.trim();
    
    if (!name) {
        showNotification('请输入服务器名称', 'error');
        return;
    }
    
    const serverData = {
        name: name,
        server_type: document.getElementById('serverType').value,
        host: document.getElementById('serverHost').value || 'localhost',
        rtmp_port: parseInt(document.getElementById('serverRtmpPort').value) || 1935,
        rtsp_port: parseInt(document.getElementById('serverRtspPort').value) || 554,
        http_port: parseInt(document.getElementById('serverHttpPort').value) || 88,
        rtc_port: parseInt(document.getElementById('serverRtcPort').value) || 8000,
        secret: document.getElementById('serverSecret').value || '',
        media_server_id: document.getElementById('serverMediaServerId').value || '',
        hook_url_prefix: document.getElementById('serverHookUrlPrefix').value || '',
        protocol: document.getElementById('serverProtocol').value,
        description: document.getElementById('serverDescription').value,
        enabled: document.getElementById('serverEnabled').checked,
        is_default: document.getElementById('serverDefault').checked
    };
    
    try {
        let response;
        
        if (currentServerId) {
            // 更新服务器
            response = await fetch(`/api/system/streaming-servers/${currentServerId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(serverData)
            });
        } else {
            // 创建服务器
            response = await fetch('/api/system/streaming-servers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(serverData)
            });
        }
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification(currentServerId ? '服务器更新成功' : '服务器创建成功', 'success');
            closeServerModal();
            loadStreamingServers();
        } else {
            showNotification(result.error || '保存失败', 'error');
        }
        
    } catch (error) {
        console.error('保存服务器失败:', error);
        showNotification('保存服务器失败', 'error');
    }
}

// ========================================
// 删除服务器
// ========================================

function openDeleteServerModal(serverId, serverName) {
    deleteServerId = serverId;
    document.getElementById('deleteServerName').textContent = serverName;
    document.getElementById('deleteServerModal').classList.add('show');
}

function closeDeleteServerModal() {
    document.getElementById('deleteServerModal').classList.remove('show');
    deleteServerId = null;
}

async function confirmDeleteServer() {
    if (!deleteServerId) return;
    
    try {
        const response = await fetch(`/api/system/streaming-servers/${deleteServerId}`, {
            method: 'DELETE'
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification('服务器删除成功', 'success');
            closeDeleteServerModal();
            loadStreamingServers();
        } else {
            showNotification(result.error || '删除失败', 'error');
        }
        
    } catch (error) {
        console.error('删除服务器失败:', error);
        showNotification('删除服务器失败', 'error');
    }
}

// ========================================
// 检查服务器状态
// ========================================

async function checkServerStatus(serverId) {
    try {
        const response = await fetch(`/api/system/streaming-servers/${serverId}/status`);
        const result = await response.json();
        
        showNotification(`服务器状态: ${result.status === 'online' ? '在线' : (result.status === 'offline' ? '离线' : '未知')}`, 'info');
        loadStreamingServers();
        
    } catch (error) {
        console.error('检查服务器状态失败:', error);
        showNotification('检查服务器状态失败', 'error');
    }
}

// ========================================
// 设置默认服务器
// ========================================

async function setDefaultServer(serverId) {
    try {
        const response = await fetch(`/api/system/streaming-servers/${serverId}/set-default`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification('已设置为默认服务器', 'success');
            loadStreamingServers();
        } else {
            showNotification(result.error || '设置失败', 'error');
        }
        
    } catch (error) {
        console.error('设置默认服务器失败:', error);
        showNotification('设置默认服务器失败', 'error');
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

function showNotification(message, type) {
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    
    document.body.appendChild(notification);
    
    setTimeout(() => notification.classList.add('show'), 10);
    
    setTimeout(() => {
        notification.classList.remove('show');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}