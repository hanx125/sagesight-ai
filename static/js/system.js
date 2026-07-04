/**
 * SageSight AI - 系统管理页面 JavaScript
 */

// ========================================
// 全局变量
// ========================================

let servers = [];
let currentServerId = null;
let deleteServerId = null;
let users = [];
let deleteUserUserId = null;

// ========================================
// 页面初始化
// ========================================

document.addEventListener('DOMContentLoaded', function() {
    loadUsers();
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

// ========================================
// 用户管理
// ========================================

async function loadUsers() {
    try {
        const response = await fetch('/api/auth/users');
        if (!response.ok) {
            if (response.status === 403) {
                document.getElementById('usersList').innerHTML = '<div class="empty-state"><p>权限不足，无法查看用户列表</p></div>';
                return;
            }
            throw new Error('加载失败');
        }
        const data = await response.json();
        users = data.users || [];
        renderUsers();
    } catch (error) {
        console.error('加载用户列表失败:', error);
        document.getElementById('usersList').innerHTML = '<div class="empty-state"><p>加载用户列表失败</p></div>';
    }
}

function renderUsers() {
    const container = document.getElementById('usersList');
    
    if (users.length === 0) {
        container.innerHTML = '<div class="empty-state"><p>暂无用户</p></div>';
        return;
    }
    
    const roleMap = { 'admin': '管理员', 'user': '普通用户' };
    const roleClass = { 'admin': 'role-admin', 'user': 'role-user' };
    
    container.innerHTML = `
        <div class="users-table">
            <div class="users-table-header">
                <div>用户名</div>
                <div>显示名称</div>
                <div>邮箱</div>
                <div>角色</div>
                <div>状态</div>
                <div>创建时间</div>
                <div>操作</div>
            </div>
            ${users.map(user => `
                <div class="users-table-row" data-id="${user.id}">
                    <div class="user-cell-username">${escapeHtml(user.username)}</div>
                    <div>${escapeHtml(user.display_name) || '-'}</div>
                    <div>${escapeHtml(user.email) || '-'}</div>
                    <div><span class="role-badge ${roleClass[user.role] || ''}">${roleMap[user.role] || user.role}</span></div>
                    <div>
                        ${user.enabled == 1 
                            ? '<span class="status-badge status-enabled">启用</span>' 
                            : '<span class="status-badge status-disabled">禁用</span>'}
                    </div>
                    <div class="user-cell-time">${formatTime(user.created_at)}</div>
                    <div class="user-cell-actions">
                        <button class="btn-icon" title="编辑" onclick="openEditUserModal(${user.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>
                        </button>
                        <button class="btn-icon btn-icon-danger" title="删除" onclick="openDeleteUserModal(${user.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                        </button>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function formatTime(timeStr) {
    if (!timeStr) return '-';
    const d = new Date(timeStr);
    if (isNaN(d)) return timeStr;
    return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function openCreateUserModal() {
    document.getElementById('userModalTitle').textContent = '添加用户';
    document.getElementById('userId').value = '';
    document.getElementById('userUsername').value = '';
    document.getElementById('userUsername').disabled = false;
    document.getElementById('userDisplayName').value = '';
    document.getElementById('userEmail').value = '';
    document.getElementById('userRole').value = 'user';
    document.getElementById('userPassword').value = '';
    document.getElementById('userPassword').required = true;
    document.getElementById('userPasswordLabel').textContent = '密码 *';
    document.getElementById('userPassword').placeholder = '请输入密码';
    document.getElementById('userEnabled').checked = true;
    document.getElementById('userModal').classList.add('show');
}

function openEditUserModal(userId) {
    const user = users.find(u => u.id === userId);
    if (!user) return;
    
    document.getElementById('userModalTitle').textContent = '编辑用户';
    document.getElementById('userId').value = user.id;
    document.getElementById('userUsername').value = user.username;
    document.getElementById('userUsername').disabled = true;
    document.getElementById('userDisplayName').value = user.display_name || '';
    document.getElementById('userEmail').value = user.email || '';
    document.getElementById('userRole').value = user.role;
    document.getElementById('userPassword').value = '';
    document.getElementById('userPassword').required = false;
    document.getElementById('userPasswordLabel').textContent = '新密码（留空不修改）';
    document.getElementById('userPassword').placeholder = '留空则不修改密码';
    document.getElementById('userEnabled').checked = user.enabled == 1;
    document.getElementById('userModal').classList.add('show');
}

function closeUserModal() {
    document.getElementById('userModal').classList.remove('show');
}

async function saveUser() {
    const userId = document.getElementById('userId').value;
    const username = document.getElementById('userUsername').value.trim();
    const displayName = document.getElementById('userDisplayName').value.trim();
    const email = document.getElementById('userEmail').value.trim();
    const role = document.getElementById('userRole').value;
    const password = document.getElementById('userPassword').value;
    const enabled = document.getElementById('userEnabled').checked ? 1 : 0;
    
    if (!username) {
        showNotification('用户名不能为空', 'error');
        return;
    }
    
    if (!userId && !password) {
        showNotification('请输入密码', 'error');
        return;
    }
    
    const data = { username, display_name: displayName, email, role, enabled };
    if (password) {
        data.password = password;
    }
    
    try {
        let response;
        if (userId) {
            response = await fetch(`/api/auth/users/${userId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
        } else {
            response = await fetch('/api/auth/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
        }
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification(userId ? '用户更新成功' : '用户创建成功', 'success');
            closeUserModal();
            loadUsers();
        } else {
            showNotification(result.error || '操作失败', 'error');
        }
    } catch (error) {
        console.error('保存用户失败:', error);
        showNotification('保存用户失败', 'error');
    }
}

function openDeleteUserModal(userId) {
    const user = users.find(u => u.id === userId);
    if (!user) return;
    
    deleteUserUserId = userId;
    document.getElementById('deleteUserName').textContent = user.username;
    document.getElementById('deleteUserModal').classList.add('show');
}

function closeDeleteUserModal() {
    document.getElementById('deleteUserModal').classList.remove('show');
    deleteUserUserId = null;
}

async function confirmDeleteUser() {
    if (!deleteUserUserId) return;
    
    try {
        const response = await fetch(`/api/auth/users/${deleteUserUserId}`, {
            method: 'DELETE'
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification('用户删除成功', 'success');
            closeDeleteUserModal();
            loadUsers();
        } else {
            showNotification(result.error || '删除失败', 'error');
        }
    } catch (error) {
        console.error('删除用户失败:', error);
        showNotification('删除用户失败', 'error');
    }
}

// ========================================
// 日志管理
// ========================================

async function loadLogFiles() {
    try {
        const response = await fetch('/api/system/logs/files');
        const data = await response.json();
        
        const select = document.getElementById('logFileSelect');
        const currentVal = select.value;
        select.innerHTML = '';
        
        if (data.files && data.files.length > 0) {
            data.files.forEach(file => {
                const option = document.createElement('option');
                option.value = file.name;
                option.textContent = `${file.name} (${file.size_mb}MB)`;
                select.appendChild(option);
            });
            // 保持当前选择
            if (data.files.find(f => f.name === currentVal)) {
                select.value = currentVal;
            }
        } else {
            select.innerHTML = '<option value="">无日志文件</option>';
        }
    } catch (error) {
        console.error('加载日志文件列表失败:', error);
    }
}

async function loadLogContent() {
    const filename = document.getElementById('logFileSelect').value;
    const level = document.getElementById('logLevelFilter').value;
    const lines = document.getElementById('logLineCount').value;
    
    if (!filename) {
        document.getElementById('logContent').innerHTML = '<div class="log-empty">无日志文件</div>';
        return;
    }
    
    const container = document.getElementById('logContent');
    container.innerHTML = '<div class="log-empty">加载中...</div>';
    
    try {
        const params = new URLSearchParams({ file: filename, lines: lines });
        if (level) params.set('level', level);
        
        const response = await fetch(`/api/system/logs/read?${params}`);
        const data = await response.json();
        
        if (response.ok) {
            if (data.lines && data.lines.length > 0) {
                container.innerHTML = data.lines.map(line => {
                    const levelClass = getLogLevelClass(line);
                    return `<div class="log-line ${levelClass}">${escapeHtml(line)}</div>`;
                }).join('');
                // 滚动到底部
                container.scrollTop = container.scrollHeight;
            } else {
                container.innerHTML = '<div class="log-empty">无日志内容</div>';
            }
        } else {
            container.innerHTML = `<div class="log-empty">${escapeHtml(data.error || '加载失败')}</div>`;
        }
    } catch (error) {
        container.innerHTML = '<div class="log-empty">加载日志失败</div>';
        console.error('加载日志内容失败:', error);
    }
}

function getLogLevelClass(line) {
    if (line.includes('- ERROR -') || line.includes('[ERROR]')) return 'log-error';
    if (line.includes('- WARNING -') || line.includes('[WARNING]') || line.includes('[WARN]')) return 'log-warning';
    if (line.includes('- DEBUG -') || line.includes('[DEBUG]')) return 'log-debug';
    if (line.includes('- INFO -') || line.includes('[INFO]')) return 'log-info';
    if (line.includes('- CRITICAL -') || line.includes('[CRITICAL]')) return 'log-critical';
    return '';
}

async function clearLogFile() {
    const filename = document.getElementById('logFileSelect').value;
    if (!filename) return;
    
    if (!confirm(`确定要清空日志文件 ${filename} 吗？`)) return;
    
    try {
        const response = await fetch(`/api/system/logs/clear?file=${encodeURIComponent(filename)}`, {
            method: 'POST'
        });
        const data = await response.json();
        
        if (response.ok) {
            showNotification('日志文件已清空', 'success');
            loadLogContent();
        } else {
            showNotification(data.error || '清空失败', 'error');
        }
    } catch (error) {
        showNotification('清空日志失败', 'error');
        console.error('清空日志失败:', error);
    }
}

function downloadLogFile() {
    const filename = document.getElementById('logFileSelect').value;
    if (!filename) return;
    window.open(`/api/system/logs/download?file=${encodeURIComponent(filename)}`, '_blank');
}

// ========================================
// 系统监控
// ========================================

let monitorInterval = null;

async function loadMonitorData() {
    const container = document.getElementById('monitorContent');
    
    try {
        const response = await fetch('/api/system/monitor');
        const data = await response.json();
        
        if (response.ok) {
            renderMonitorData(data);
        } else {
            container.innerHTML = '<div class="monitor-empty">加载监控数据失败</div>';
        }
    } catch (error) {
        container.innerHTML = '<div class="monitor-empty">加载监控数据失败</div>';
        console.error('加载监控数据失败:', error);
    }
}

function renderMonitorData(data) {
    const container = document.getElementById('monitorContent');
    
    const cpuPercent = data.cpu.percent;
    const memPercent = data.memory.percent;
    const diskPercent = data.disk.percent;
    
    const getBarClass = (percent) => {
        if (percent >= 85) return 'danger';
        if (percent >= 60) return 'warning';
        return 'normal';
    };
    
    let gpuHtml = '';
    if (data.gpu && data.gpu.length > 0) {
        gpuHtml = data.gpu.map(gpu => `
            <div class="monitor-card gpu-card">
                <div class="monitor-card-header">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-9 3h2v2h-2V7zm0 4h2v2h-2v-2zm-4-4h2v2H7V7zm0 4h2v2H7v-2zm-3 4h12v2H4v-2zm3-8h2v2H7V7zm0 4h2v2H7v-2zm5 0h2v2h-2v-2zm0-4h2v2h-2V7z"/></svg>
                    <h4>${escapeHtml(gpu.name)} (GPU ${gpu.index})</h4>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">GPU利用率</span>
                    <span class="monitor-stat-value">${gpu.gpu_utilization}%</span>
                </div>
                <div class="monitor-progress-bar">
                    <div class="monitor-progress-fill ${getBarClass(gpu.gpu_utilization)}" style="width: ${gpu.gpu_utilization}%"></div>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">显存使用</span>
                    <span class="monitor-stat-value">${gpu.memory_used_mb}MB / ${gpu.memory_total_mb}MB (${gpu.memory_percent}%)</span>
                </div>
                <div class="monitor-progress-bar">
                    <div class="monitor-progress-fill ${getBarClass(gpu.memory_percent)}" style="width: ${gpu.memory_percent}%"></div>
                </div>
            </div>
        `).join('');
    }
    
    container.innerHTML = `
        <div class="monitor-grid">
            <div class="monitor-card">
                <div class="monitor-card-header">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 6h18V4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h4v-2H4V6zm6 2h8v6h-2v4h4v-2h2V4h-6v2h2v4h-4V8h-4v2h4v4h-4V8z"/></svg>
                    <h4>CPU</h4>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">使用率</span>
                    <span class="monitor-stat-value">${cpuPercent}%</span>
                </div>
                <div class="monitor-progress-bar">
                    <div class="monitor-progress-fill ${getBarClass(cpuPercent)}" style="width: ${cpuPercent}%"></div>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">核心数</span>
                    <span class="monitor-stat-value">${data.cpu.count} 核</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">主频</span>
                    <span class="monitor-stat-value">${data.cpu.freq_mhz} MHz</span>
                </div>
            </div>
            
            <div class="monitor-card">
                <div class="monitor-card-header">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 7h2v2H2V7zm0 4h2v2H2v-2zm0 4h2v2H2v-2zM20 7h2v2h-2V7zm0 4h2v2h-2v-2zm0 4h2v2h-2v-2zM2 5h20v2H2V5zm0 12h20v2H2v-2zM6 7h2v2H6V7zm0 4h2v2H6v-2zm0 4h2v2H6v-2zm4-8h2v2h-2V7zm0 4h2v2h-2v-2zm0 4h2v2h-2v-2zm4-8h2v2h-2V7zm0 4h2v2h-2v-2zm0 4h2v2h-2v-2z"/></svg>
                    <h4>内存</h4>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">使用率</span>
                    <span class="monitor-stat-value">${memPercent}%</span>
                </div>
                <div class="monitor-progress-bar">
                    <div class="monitor-progress-fill ${getBarClass(memPercent)}" style="width: ${memPercent}%"></div>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">已用/总量</span>
                    <span class="monitor-stat-value">${data.memory.used_gb}GB / ${data.memory.total_gb}GB</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">可用</span>
                    <span class="monitor-stat-value">${(data.memory.total_gb - data.memory.used_gb).toFixed(2)}GB</span>
                </div>
            </div>
            
            <div class="monitor-card">
                <div class="monitor-card-header">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 2v6h.01L5 8.01V20h14V8.01L18.99 8H19V2H5zm12 12H7v-2h10v2zm0-4H7V8h10v2z"/></svg>
                    <h4>磁盘</h4>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">使用率</span>
                    <span class="monitor-stat-value">${diskPercent}%</span>
                </div>
                <div class="monitor-progress-bar">
                    <div class="monitor-progress-fill ${getBarClass(diskPercent)}" style="width: ${diskPercent}%"></div>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">已用/总量</span>
                    <span class="monitor-stat-value">${data.disk.used_gb}GB / ${data.disk.total_gb}GB</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">剩余</span>
                    <span class="monitor-stat-value">${(data.disk.total_gb - data.disk.used_gb).toFixed(2)}GB</span>
                </div>
            </div>
            
            ${gpuHtml}
        </div>
        
        <div class="monitor-system-info">
            <h4>系统信息</h4>
            <div class="monitor-grid" style="grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));">
                <div class="monitor-stat">
                    <span class="monitor-stat-label">操作系统</span>
                    <span class="monitor-stat-value">${escapeHtml(data.system.platform)}</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">主机名</span>
                    <span class="monitor-stat-value">${escapeHtml(data.system.hostname)}</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">Python版本</span>
                    <span class="monitor-stat-value">${escapeHtml(data.system.python_version)}</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">运行时间</span>
                    <span class="monitor-stat-value">${data.system.uptime_str}</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">进程数</span>
                    <span class="monitor-stat-value">${data.system.process_count}</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">网络发送</span>
                    <span class="monitor-stat-value">${data.network.sent_mb}MB</span>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">网络接收</span>
                    <span class="monitor-stat-value">${data.network.recv_mb}MB</span>
                </div>
            </div>
        </div>
    `;
}

// ========================================
// 标签页切换扩展
// ========================================

const _originalSwitchTab = switchTab;
switchTab = function(tabId) {
    _originalSwitchTab(tabId);
    
    // 切换到日志页时加载日志
    if (tabId === 'logs') {
        loadLogFiles();
        loadLogContent();
    }
    // 切换到监控页时加载数据
    if (tabId === 'monitor') {
        loadMonitorData();
        // 启动自动刷新
        if (monitorInterval) clearInterval(monitorInterval);
        monitorInterval = setInterval(loadMonitorData, 5000);
    } else {
        // 离开监控页停止刷新
        if (monitorInterval) {
            clearInterval(monitorInterval);
            monitorInterval = null;
        }
    }
    // 切换到全局配置页时加载配置
    if (tabId === 'config') {
        loadSystemConfig();
    }
};

// ========================================
// 全局配置管理（存储/MQTT/WebSocket）
// ========================================

async function loadSystemConfig() {
    try {
        const response = await fetch('/api/system/config');
        const data = await response.json();
        
        if (response.ok) {
            // MinIO
            const minio = data.storage?.minio || {};
            document.getElementById('minioEnabled').checked = minio.enabled !== false;
            document.getElementById('minioEndpoint').value = minio.endpoint || 'localhost:9000';
            document.getElementById('minioAccessKey').value = minio.access_key || '';
            document.getElementById('minioSecretKey').value = minio.secret_key || '';
            document.getElementById('minioSecure').value = String(minio.secure || false);
            document.getElementById('minioPresignedExpires').value = minio.presigned_expires || 3600;
            
            // FTP
            const ftp = data.storage?.ftp || {};
            document.getElementById('ftpEnabled').checked = ftp.enabled !== false;
            document.getElementById('ftpHost').value = ftp.host || 'localhost';
            document.getElementById('ftpPort').value = ftp.port || 21;
            document.getElementById('ftpUsername').value = ftp.username || '';
            document.getElementById('ftpPassword').value = ftp.password || '';
            document.getElementById('ftpPassiveMode').value = String(ftp.passive_mode || true);
            
            // MQTT
            const mqtt = data.mqtt || {};
            document.getElementById('mqttEnabled').checked = mqtt.enabled === true;
            const mqttConfig = mqtt.config || {};
            document.getElementById('mqttBroker').value = mqttConfig.broker || 'mqtt://localhost:1883';
            document.getElementById('mqttTopic').value = mqttConfig.topic || 'sagesight/events';
            document.getElementById('mqttQos').value = String(mqttConfig.qos ?? 1);
            document.getElementById('mqttRetain').value = String(mqttConfig.retain || false);
            
            // WebSocket
            const ws = data.websocket || {};
            document.getElementById('websocketEnabled').checked = ws.enabled === true;
            const wsConfig = ws.config || {};
            document.getElementById('websocketHost').value = wsConfig.host || '0.0.0.0';
            document.getElementById('websocketPort').value = wsConfig.port || 8765;
        }
    } catch (error) {
        showNotification('加载配置失败', 'error');
        console.error('加载系统配置失败:', error);
    }
}

async function saveSystemConfig() {
    const configData = {
        storage: {
            minio: {
                enabled: document.getElementById('minioEnabled').checked,
                endpoint: document.getElementById('minioEndpoint').value,
                access_key: document.getElementById('minioAccessKey').value,
                secret_key: document.getElementById('minioSecretKey').value,
                secure: document.getElementById('minioSecure').value === 'true',
                presigned_expires: parseInt(document.getElementById('minioPresignedExpires').value) || 3600
            },
            ftp: {
                enabled: document.getElementById('ftpEnabled').checked,
                host: document.getElementById('ftpHost').value,
                port: parseInt(document.getElementById('ftpPort').value) || 21,
                username: document.getElementById('ftpUsername').value,
                password: document.getElementById('ftpPassword').value,
                passive_mode: document.getElementById('ftpPassiveMode').value === 'true'
            }
        },
        mqtt: {
            enabled: document.getElementById('mqttEnabled').checked,
            config: {
                broker: document.getElementById('mqttBroker').value,
                topic: document.getElementById('mqttTopic').value,
                qos: parseInt(document.getElementById('mqttQos').value),
                retain: document.getElementById('mqttRetain').value === 'true'
            }
        },
        websocket: {
            enabled: document.getElementById('websocketEnabled').checked,
            config: {
                host: document.getElementById('websocketHost').value,
                port: parseInt(document.getElementById('websocketPort').value) || 8765
            }
        }
    };
    
    try {
        const response = await fetch('/api/system/config', {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(configData)
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification(result.message || '配置保存成功', 'success');
        } else {
            showNotification(result.error || '保存失败', 'error');
        }
    } catch (error) {
        showNotification('保存配置失败', 'error');
        console.error('保存系统配置失败:', error);
    }
}