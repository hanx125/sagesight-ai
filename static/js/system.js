/**
 * SageSight AI - 系统管理页面 JavaScript
 */
window.System = (function() {

// ========================================
// 全局变量
// ========================================

var servers = [];
var currentServerId = null;
var deleteServerId = null;
var users = [];
var deleteUserUserId = null;
var llmConfigs = [];
var currentLLMId = null;
var deleteLLMId = null;

// 资源监控图表（CPU/内存/磁盘/加速器4条波浪线）
var resourceChart = null;
// 历史采样数据缓冲（最多保留 30 个采样点）
var RESOURCE_HISTORY_MAX = 30;
var resourceHistory = {
    labels: [],
    cpu: [],
    memory: [],
    disk: [],
    accelerator: []   // GPU 或 NPU（按设备类型动态命名）
};

// ========================================
// 页面初始化
// ========================================

// 注册页面初始化函数（支持 pjax 局部加载）
SageSight.registerPage('/system', function() {
    loadUsers();
    loadStreamingServers();
    loadLLMConfigs();
    loadVoiceConfig();
    initWebSocket();
});

// ========================================
// WebSocket 初始化
// ========================================

function initWebSocket() {
    // 使用全局 socket 连接（由 main.js 初始化）
    const socket = getSocket();

    if (socket) {
        socket.on('streaming_server_created', function(data) {
            loadStreamingServers();
        });
        
        socket.on('streaming_server_updated', function(data) {
            loadStreamingServers();
        });
        
        socket.on('streaming_server_deleted', function(data) {
            loadStreamingServers();
        });
        
        socket.on('llm_config_created', function(data) {
            loadLLMConfigs();
        });
        
        socket.on('llm_config_updated', function(data) {
            loadLLMConfigs();
        });
        
        socket.on('llm_config_deleted', function(data) {
            loadLLMConfigs();
        });
    }
}

// ========================================
// 报警语音播报配置（全局 + MP3 预置文件管理）
// ========================================

// 加载全局语音配置与 MP3 文件列表
async function loadVoiceConfig() {
    try {
        const resp = await fetch('/api/system/voice-config');
        const data = await resp.json();
        const cfg = data.config || {};
        document.getElementById('voiceEnabled').checked = (String(cfg.enabled) === '1');
        document.getElementById('voiceLoopCount').value = cfg.loop_count || 1;
        document.getElementById('voiceIntervalMs').value = cfg.interval_ms || 1000;
        document.getElementById('voiceVolume').value = cfg.volume || 1.0;
        renderVoiceMp3Select(cfg.mp3 || '', data.files || []);
        renderVoiceFiles(data.files || []);
    } catch (e) {
        console.error('加载报警语音配置失败:', e);
        showNotification('加载报警语音配置失败', 'error');
    }
}

// 渲染默认 MP3 下拉选项
function renderVoiceMp3Select(current, files) {
    const select = document.getElementById('voiceMp3');
    select.innerHTML = '<option value="">（未选择）</option>';
    files.forEach(f => {
        const opt = document.createElement('option');
        opt.value = f.name;
        opt.textContent = f.name;
        if (f.name === current) opt.selected = true;
        select.appendChild(opt);
    });
}

// 渲染 MP3 文件列表
function renderVoiceFiles(files) {
    const list = document.getElementById('voiceFilesList');
    if (!files || files.length === 0) {
        list.innerHTML = '<p class="form-hint">暂无 MP3 预置文件，请上传。</p>';
        return;
    }
    list.innerHTML = files.map(f => `
        <div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--border,#e5e7eb);">
            <span style="flex:1;">${f.name} <span class="form-hint">(${f.size_display})</span></span>
            <button class="btn btn-sm" onclick="System.playVoiceFile('${f.name}')">试听</button>
            <button class="btn btn-sm btn-danger" onclick="System.deleteVoiceFile('${f.name}')">删除</button>
        </div>`).join('');
}

// 保存全局语音配置
async function saveVoiceConfig() {
    const payload = {
        enabled: document.getElementById('voiceEnabled').checked,
        mp3: document.getElementById('voiceMp3').value,
        loop_count: parseInt(document.getElementById('voiceLoopCount').value, 10) || 1,
        interval_ms: parseInt(document.getElementById('voiceIntervalMs').value, 10) || 1000,
        volume: parseFloat(document.getElementById('voiceVolume').value) || 1.0,
    };
    try {
        const resp = await fetch('/api/system/voice-config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) { showNotification(data.error || '保存失败', 'error'); return; }
        showNotification('全局报警语音配置已保存', 'success');
        loadVoiceConfig();
    } catch (e) {
        showNotification('保存失败', 'error');
    }
}

// 按当前表单配置试听
async function testVoice() {
    const payload = {
        mp3: document.getElementById('voiceMp3').value,
        loop_count: parseInt(document.getElementById('voiceLoopCount').value, 10) || 1,
        interval_ms: parseInt(document.getElementById('voiceIntervalMs').value, 10) || 1000,
        volume: parseFloat(document.getElementById('voiceVolume').value) || 1.0,
    };
    if (!payload.mp3) { showNotification('请先选择默认 MP3 语音或上传预置文件', 'error'); return; }
    try {
        const resp = await fetch('/api/system/voice-test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) { showNotification(data.error || '试听失败', 'error'); return; }
        showNotification('已触发服务端试听', 'success');
    } catch (e) {
        showNotification('试听失败', 'error');
    }
}

// 试听指定预置文件（服务端播放，由盒子本地声卡发声，与报警播报链路一致）
async function playVoiceFile(name) {
    try {
        const resp = await fetch('/api/system/voice-test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mp3: name }),
        });
        const data = await resp.json();
        if (!resp.ok) { showNotification(data.error || '试听失败', 'error'); return; }
        showNotification('已触发服务端试听', 'success');
    } catch (e) {
        showNotification('试听失败', 'error');
    }
}

// 上传 MP3 预置文件
async function uploadVoiceFile() {
    const input = document.getElementById('voiceFileInput');
    if (!input.files || input.files.length === 0) return;
    const formData = new FormData();
    formData.append('file', input.files[0]);
    try {
        const resp = await fetch('/api/system/voice-files', { method: 'POST', body: formData });
        const data = await resp.json();
        if (!resp.ok) { showNotification(data.error || '上传失败', 'error'); return; }
        showNotification(data.message || '上传成功', 'success');
        input.value = '';
        loadVoiceConfig();
    } catch (e) {
        showNotification('上传失败', 'error');
    }
}

// 删除 MP3 预置文件
async function deleteVoiceFile(name) {
    if (!confirm(`确定删除 MP3 预置文件「${name}」？`)) return;
    try {
        const resp = await fetch(`/api/system/voice-files/${encodeURIComponent(name)}`, { method: 'DELETE' });
        const data = await resp.json();
        if (!resp.ok) { showNotification(data.error || '删除失败', 'error'); return; }
        showNotification('已删除', 'success');
        loadVoiceConfig();
    } catch (e) {
        showNotification('删除失败', 'error');
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
                <button class="btn btn-primary" onclick="System.openCreateServerModal()">添加服务器</button>
            </div>
        `;
        return;
    }
    
    serversList.innerHTML = servers.map(server => {
        const typeLabel = getServerTypeLabel(server.server_type);

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
                    ${server.description ? `
                    <div class="server-detail-row">
                        <span class="detail-label">描述:</span>
                        <span class="detail-value">${escapeHtml(server.description)}</span>
                    </div>
                    ` : ''}
                </div>
                
                <div class="server-card-footer">
                    <div class="server-actions">
                        ${!server.is_default ? `
                        <button class="server-btn" onclick="System.setDefaultServer(${server.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/>
                            </svg>
                            设为默认
                        </button>
                        ` : ''}
                        <button class="server-btn" onclick="System.openEditServerModal(${server.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                            </svg>
                            编辑
                        </button>
                        ${!server.is_default ? `
                        <button class="server-btn danger" onclick="System.openDeleteServerModal(${server.id}, '${escapeHtml(server.name)}')">
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
                        <button class="btn-icon" title="编辑" onclick="System.openEditUserModal(${user.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>
                        </button>
                        <button class="btn-icon btn-icon-danger" title="删除" onclick="System.openDeleteUserModal(${user.id})">
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

async function loadMonitorData() {
    const container = document.getElementById('monitorContent');
    // pjax 导航后或非 system 页面时 DOM 元素可能不存在
    if (!container) return;

    try {
        const response = await fetch('/api/system/monitor');
        const data = await response.json();

        // fetch 期间 DOM 可能已被 pjax 替换，重新检查
        if (!document.getElementById('monitorContent')) return;

        if (response.ok) {
            renderMonitorData(data);
            updateResourceChart(data);
        } else {
            container.innerHTML = '<div class="monitor-empty">加载监控数据失败</div>';
        }
    } catch (error) {
        if (container) container.innerHTML = '<div class="monitor-empty">加载监控数据失败</div>';
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
    const accelerators = data.accelerators || data.gpu || [];
    if (accelerators && accelerators.length > 0) {
        gpuHtml = accelerators.map(acc => {
            // 依据 device_type 动态显示标签（GPU/NPU），不绑定具体厂商
            const isNpu = acc.device_type === 'npu';
            const typeLabel = isNpu ? 'NPU' : 'GPU';
            const utilLabel = isNpu ? 'NPU利用率' : 'GPU利用率';
            // NPU 共享系统内存、无独立显存，仅 GPU 显示显存使用行
            const memHtml = acc.has_memory === false ? '' : `
                <div class="monitor-stat">
                    <span class="monitor-stat-label">显存使用</span>
                    <span class="monitor-stat-value">${acc.memory_used_mb}MB / ${acc.memory_total_mb}MB (${acc.memory_percent}%)</span>
                </div>
                <div class="monitor-progress-bar">
                    <div class="monitor-progress-fill ${getBarClass(acc.memory_percent)}" style="width: ${acc.memory_percent}%"></div>
                </div>`;
            return `
            <div class="monitor-card gpu-card">
                <div class="monitor-card-header">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-9 3h2v2h-2V7zm0 4h2v2h-2v-2zm-4-4h2v2H7V7zm0 4h2v2H7v-2zm-3 4h12v2H4v-2zm3-8h2v2H7V7zm0 4h2v2H7v-2zm5 0h2v2h-2v-2zm0-4h2v2h-2V7z"/></svg>
                    <h4>${escapeHtml(acc.name)} (${typeLabel} ${acc.index})</h4>
                </div>
                <div class="monitor-stat">
                    <span class="monitor-stat-label">${utilLabel}</span>
                    <span class="monitor-stat-value">${acc.gpu_utilization}%</span>
                </div>
                <div class="monitor-progress-bar">
                    <div class="monitor-progress-fill ${getBarClass(acc.gpu_utilization)}" style="width: ${acc.gpu_utilization}%"></div>
                </div>${memHtml}
            </div>
        `;
        }).join('');
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
    `;

    // 系统信息渲染到独立容器（位于图表下方）
    const sysInfoContainer = document.getElementById('monitorSystemInfo');
    if (sysInfoContainer) {
        sysInfoContainer.innerHTML = `
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
}

// ========================================
// 资源动态可视化图表（CPU/内存/磁盘/加速器4条波浪线）
// ========================================

/**
 * 初始化资源趋势图表（波浪线折线图）
 * - 4 条曲线：CPU、内存、磁盘、加速器（GPU/NPU，按设备类型动态命名）
 * - tension: 0.4 产生平滑波浪线效果
 * - 保持最近 30 个采样点滚动显示
 */
function initResourceChart() {
    const canvas = document.getElementById('resourceChart');
    if (!canvas || typeof Chart === 'undefined') return;

    // pjax 导航后旧 canvas 可能已被替换，销毁失效的图表实例
    if (resourceChart && !document.body.contains(resourceChart.canvas)) {
        resourceChart.destroy();
        resourceChart = null;
    }

    // 已初始化且有效则不重复创建
    if (resourceChart) return;

    const ctx = canvas.getContext('2d');
    resourceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'CPU 使用率(%)',
                    data: [],
                    borderColor: '#ff6384',
                    backgroundColor: 'rgba(255, 99, 132, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,          // 波浪线平滑度
                    fill: false,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    cubicInterpolationMode: 'monotone'
                },
                {
                    label: '内存使用率(%)',
                    data: [],
                    borderColor: '#36a2eb',
                    backgroundColor: 'rgba(54, 162, 235, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    fill: false,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    cubicInterpolationMode: 'monotone'
                },
                {
                    label: '磁盘使用率(%)',
                    data: [],
                    borderColor: '#ffce56',
                    backgroundColor: 'rgba(255, 206, 86, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    fill: false,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    cubicInterpolationMode: 'monotone'
                },
                {
                    label: '加速器使用率(%)',
                    data: [],
                    borderColor: '#4bc0c0',
                    backgroundColor: 'rgba(75, 192, 192, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    fill: false,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    cubicInterpolationMode: 'monotone'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            animation: { duration: 300 },
            scales: {
                y: {
                    min: 0,
                    max: 100,
                    ticks: { callback: (v) => v + '%', font: { size: 11 } },
                    grid: { color: 'rgba(0,0,0,0.05)' }
                },
                x: {
                    ticks: { maxTicksLimit: 8, font: { size: 11 } },
                    grid: { display: false }
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                    align: 'end',
                    labels: { boxWidth: 12, font: { size: 12 }, usePointStyle: true }
                },
                tooltip: {
                    callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}%` }
                }
            }
        }
    });
}

/**
 * 根据最新监控数据更新图表（追加一个采样点，超出上限则滚动丢弃最旧点）
 * 加速器标签根据设备类型动态显示（GPU/NPU），不绑定具体厂商
 */
function updateResourceChart(data) {
    if (!resourceChart) return;

    const now = new Date();
    const label = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

    // 取第一个加速器（GPU/NPU）的使用率；无加速器设备时记录 0
    const accelerators = data.accelerators || data.gpu || [];
    const accUtil = accelerators.length > 0 ? (accelerators[0].gpu_utilization || 0) : 0;

    // 动态更新加速器数据集标签（GPU/NPU）
    if (accelerators.length > 0) {
        const accLabel = accelerators[0].device_type === 'npu' ? 'NPU 使用率(%)' : 'GPU 使用率(%)';
        if (resourceChart.data.datasets[3].label !== accLabel) {
            resourceChart.data.datasets[3].label = accLabel;
        }
    }

    // 追加新采样点
    resourceHistory.labels.push(label);
    resourceHistory.cpu.push(Number(data.cpu.percent) || 0);
    resourceHistory.memory.push(Number(data.memory.percent) || 0);
    resourceHistory.disk.push(Number(data.disk.percent) || 0);
    resourceHistory.accelerator.push(Number(accUtil) || 0);

    // 超出上限则丢弃最旧点（滚动窗口）
    if (resourceHistory.labels.length > RESOURCE_HISTORY_MAX) {
        resourceHistory.labels.shift();
        resourceHistory.cpu.shift();
        resourceHistory.memory.shift();
        resourceHistory.disk.shift();
        resourceHistory.accelerator.shift();
    }

    // 同步到图表
    resourceChart.data.labels = resourceHistory.labels;
    resourceChart.data.datasets[0].data = resourceHistory.cpu;
    resourceChart.data.datasets[1].data = resourceHistory.memory;
    resourceChart.data.datasets[2].data = resourceHistory.disk;
    resourceChart.data.datasets[3].data = resourceHistory.accelerator;
    resourceChart.update('none');   // 关闭过渡动画，避免刷新抖动
}

/**
 * 清空资源图表历史数据（离开监控页时调用）
 */
function clearResourceChart() {
    resourceHistory = { labels: [], cpu: [], memory: [], disk: [], accelerator: [] };
    if (resourceChart) {
        resourceChart.data.labels = [];
        resourceChart.data.datasets.forEach(ds => ds.data = []);
        resourceChart.update('none');
    }
}

// ========================================
// 标签页切换扩展
// ========================================

var _originalSwitchTab = switchTab;
switchTab = function(tabId) {
    _originalSwitchTab(tabId);

    // 切换到日志页时加载日志
    if (tabId === 'logs') {
        loadLogFiles();
        loadLogContent();
    }
    // 切换到监控页时加载数据
    if (tabId === 'monitor') {
        // 初始化图表（首次进入）
        initResourceChart();
        loadMonitorData();
        // 使用 TimerManager 启动系统监控定时器
        if (window.TimerManager) {
            TimerManager.startSystemMonitor();
        }
    } else {
        // 离开监控页：停止定时器并清理图表，避免后台空跑和内存占用
        if (window.TimerManager && TimerManager.stopSystemMonitor) {
            TimerManager.stopSystemMonitor();
        }
        clearResourceChart();
    }
    // 切换到 Web 服务配置页时加载配置
    if (tabId === 'webserver') {
        loadWebServerConfig();
    }
    // 切换到全局配置页时加载配置
    if (tabId === 'config') {
        loadSystemConfig();
    }
    // 切换到节点管理页时加载配置
    if (tabId === 'node') {
        loadNodeManagementConfig();
    }
    // 切换到数据库管理页时加载数据库信息
    if (tabId === 'database') {
        loadDatabaseInfo();
    }
    // 切换到大模型配置页时加载配置列表
    if (tabId === 'llm') {
        loadLLMConfigs();
    }
    // 切换到云平台配置页时加载单点登录配置
    if (tabId === 'cloud') {
        loadSSOConfig();
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

            // 边缘库比对连接配置（人脸库 / 车牌库，任务级库名称在任务「参数配置」中设置）
            const el = data.edge_library || {};
            document.getElementById('elEdgeApiUrl').value = el.edge_api_url || '';
            document.getElementById('elAgentToken').value = el.agent_token || '';
            document.getElementById('elTimeout').value = el.timeout || 5;
            document.getElementById('elCropMargin').value = (el.crop_margin !== undefined && el.crop_margin !== null) ? el.crop_margin : 0.2;

            // 实时通信配置已迁移至「Web服务配置」标签页，此处不再加载
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
        // 边缘库比对连接配置（人脸库 / 车牌库接口）
        edge_library: {
            edge_api_url: document.getElementById('elEdgeApiUrl').value.trim(),
            agent_token: document.getElementById('elAgentToken').value.trim(),
            timeout: parseInt(document.getElementById('elTimeout').value) || 5,
            crop_margin: parseFloat(document.getElementById('elCropMargin').value)
        }
        // 实时通信配置已迁移至「Web服务配置」标签页，此处不再保存
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

// ========================================
// 节点管理配置（向边缘平台注册/上报心跳的客户端配置）
// ========================================

/**
 * 加载节点管理配置到表单
 * 复用 /api/system/config 接口（返回完整 node_management 段）
 */
async function loadNodeManagementConfig() {
    try {
        const response = await fetch('/api/system/config');
        const data = await response.json();

        if (!response.ok) {
            showNotification('加载节点管理配置失败', 'error');
            return;
        }

        const nm = data.node_management || {};
        document.getElementById('nmEnabled').checked = nm.enabled === true;
        document.getElementById('nmNodeId').value = nm.node_id || '';
        document.getElementById('nmAgentToken').value = nm.agent_token || '';
        document.getElementById('nmControlPlaneUrl').value = nm.control_plane_url || 'http://127.0.0.1:5000/admin-api/node/agent';
        document.getElementById('nmHeartbeatInterval').value = nm.heartbeat_interval || 10;
        document.getElementById('nmAgentVersion').value = nm.agent_version || '1.0.0';
        document.getElementById('nmAgentListenHost').value = nm.agent_listen_host || '0.0.0.0';
        document.getElementById('nmAgentListenPort').value = nm.agent_listen_port || 9100;
        document.getElementById('nmAiRoot').value = nm.ai_root || '/opt/AI';
        document.getElementById('nmVideoRoot').value = nm.video_root || '/opt/VIDEO';
        document.getElementById('nmMediaClusterRoot').value = nm.media_cluster_root || '/opt/media-cluster';
    } catch (error) {
        showNotification('加载节点管理配置失败', 'error');
        console.error('加载节点管理配置失败:', error);
    }
}

/**
 * 保存节点管理配置
 */
async function saveNodeManagementConfig() {
    const configData = {
        node_management: {
            enabled: document.getElementById('nmEnabled').checked,
            node_id: document.getElementById('nmNodeId').value.trim(),
            agent_token: document.getElementById('nmAgentToken').value.trim(),
            control_plane_url: document.getElementById('nmControlPlaneUrl').value.trim(),
            heartbeat_interval: parseInt(document.getElementById('nmHeartbeatInterval').value) || 10,
            agent_version: document.getElementById('nmAgentVersion').value.trim(),
            agent_listen_host: document.getElementById('nmAgentListenHost').value.trim(),
            agent_listen_port: parseInt(document.getElementById('nmAgentListenPort').value) || 9100,
            ai_root: document.getElementById('nmAiRoot').value.trim(),
            video_root: document.getElementById('nmVideoRoot').value.trim(),
            media_cluster_root: document.getElementById('nmMediaClusterRoot').value.trim()
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
            // 节点管理服务随主进程启动，配置变更需重启生效
            promptRestart((result.message || '节点管理配置已保存') + '，需重启服务后生效');
        } else {
            showNotification(result.error || '保存失败', 'error');
        }
    } catch (error) {
        showNotification('保存节点管理配置失败', 'error');
        console.error('保存节点管理配置失败:', error);
    }
}

// ========================================
// Web 服务配置管理（端口/调试/CORS/Socket.IO/原生 WebSocket）
// ========================================

/**
 * 加载 Web 服务配置到表单
 * 复用 /api/system/config 接口（返回完整 web_server 段）
 */
async function loadWebServerConfig() {
    try {
        const response = await fetch('/api/system/config');
        const data = await response.json();

        if (!response.ok) {
            showNotification('加载 Web 服务配置失败', 'error');
            return;
        }

        const webServer = data.web_server || {};

        // 基础服务配置
        document.getElementById('wsHost').value = webServer.host || '';
        document.getElementById('wsPort').value = webServer.port || 5556;
        document.getElementById('wsCorsEnabled').value = String(webServer.cors_enabled !== false);
        document.getElementById('wsDebug').value = String(webServer.debug === true);
        document.getElementById('wsStaticDir').value = webServer.static_dir || 'static';
        document.getElementById('wsTemplateDir').value = webServer.template_dir || 'templates';

        // Socket.IO 通道
        const socketio = webServer.socketio || {};
        document.getElementById('wsSocketioEnabled').checked = socketio.enabled !== false;
        document.getElementById('wsSocketioNamespace').value = socketio.namespace || '/ws';
        document.getElementById('wsSocketioCors').value = socketio.cors_allowed_origins || '*';

        // 原生 WebSocket 通道
        const rawWs = webServer.raw_websocket || {};
        document.getElementById('wsRawWebsocketEnabled').checked = rawWs.enabled !== false;
        document.getElementById('wsRawWebsocketPath').value = rawWs.path || '/ws/raw';

        // 动态生成第三方接入地址（用当前访问的 host:port，避免硬编码端口）
        const wsProto = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const wsPath = rawWs.path || '/ws/raw';
        const wsUrlEl = document.getElementById('wsRawWebsocketUrl');
        if (wsUrlEl) {
            wsUrlEl.value = `${wsProto}://${window.location.host}${wsPath}`;
        }
    } catch (error) {
        showNotification('加载 Web 服务配置失败', 'error');
        console.error('加载 Web 服务配置失败:', error);
    }
}

/**
 * 保存 Web 服务配置
 * 提交完整 web_server 段（端口/调试/CORS/Socket.IO/原生 WebSocket）
 * 端口、静态/模板目录需重启服务生效；其余配置即时保存
 */
async function saveWebServerConfig() {
    const port = parseInt(document.getElementById('wsPort').value);
    if (!port || port < 1 || port > 65535) {
        showNotification('请输入有效端口（1-65535）', 'error');
        return;
    }

    const configData = {
        web_server: {
            host: document.getElementById('wsHost').value.trim(),
            port: port,
            cors_enabled: document.getElementById('wsCorsEnabled').value === 'true',
            debug: document.getElementById('wsDebug').value === 'true',
            static_dir: document.getElementById('wsStaticDir').value.trim() || 'static',
            template_dir: document.getElementById('wsTemplateDir').value.trim() || 'templates',
            socketio: {
                enabled: document.getElementById('wsSocketioEnabled').checked,
                namespace: document.getElementById('wsSocketioNamespace').value.trim() || '/ws',
                cors_allowed_origins: document.getElementById('wsSocketioCors').value.trim() || '*'
            },
            raw_websocket: {
                enabled: document.getElementById('wsRawWebsocketEnabled').checked,
                path: document.getElementById('wsRawWebsocketPath').value.trim() || '/ws/raw'
            }
        }
    };

    try {
        const response = await fetch('/api/system/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData)
        });

        const result = await response.json();

        if (response.ok) {
            // 端口/静态/模板目录/Socket.IO/原生 WebSocket 均随服务启动初始化，需重启生效
            const msg = result.message || '配置已保存';
            const oldPort = window.location.port;
            const newPort = String(port);
            const portTip = (oldPort && oldPort !== newPort)
                ? `端口已变更（${oldPort} → ${newPort}），重启后需通过新端口访问。`
                : '';
            promptRestart(`${msg}。修改端口、静态/模板目录、Socket.IO、原生 WebSocket 配置需重启服务后生效。${portTip}`);
        } else {
            showNotification(result.error || '保存失败', 'error');
        }
    } catch (error) {
        showNotification('保存 Web 服务配置失败', 'error');
        console.error('保存 Web 服务配置失败:', error);
    }
}

// ========================================
// 数据库管理（路径设置 + 备份/恢复）
// ========================================

async function loadDatabaseInfo() {
    try {
        const response = await fetch('/api/system/database/info');
        const data = await response.json();

        if (response.ok) {
            document.getElementById('dbCurrentPath').value = data.path || '未配置';
            document.getElementById('dbType').value = data.type || 'sqlite';
            document.getElementById('dbFileSize').value = data.exists ? data.size_display : '文件不存在';
            document.getElementById('dbBackupCount').value = (data.backup_count || 0) + ' 个备份';
            document.getElementById('dbPathInput').value = data.path || 'data/tasks.db';

            // 渲染备份列表
            renderBackupList(data.backups || []);
        }
    } catch (error) {
        showNotification('加载数据库信息失败', 'error');
        console.error('加载数据库信息失败:', error);
    }
}

function renderBackupList(backups) {
    const list = document.getElementById('dbBackupList');
    if (!list) return;

    if (!backups || backups.length === 0) {
        list.innerHTML = '<div class="notify-empty">暂无备份</div>';
        return;
    }

    list.innerHTML = backups.map(b => {
        const date = new Date(b.created_at * 1000);
        const dateStr = `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
        return `
            <div class="backup-item" style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--border-color,#f0f0f0);">
                <div>
                    <div style="font-size:13px;color:var(--text-primary);">${b.filename}</div>
                    <div style="font-size:12px;color:var(--text-secondary);">${dateStr} · ${b.size_display}</div>
                </div>
                <button class="btn btn-secondary btn-sm" style="color:#f44336;font-size:12px;padding:4px 8px;" onclick="System.deleteBackup('${b.filename}')">删除</button>
            </div>
        `;
    }).join('');
}

async function backupDatabase() {
    try {
        const response = await fetch('/api/system/database/backup', { method: 'POST' });
        const data = await response.json();

        if (response.ok) {
            showNotification(data.message || '备份成功', 'success');
            loadDatabaseInfo();
        } else {
            showNotification(data.error || '备份失败', 'error');
        }
    } catch (error) {
        showNotification('备份失败', 'error');
        console.error('数据库备份失败:', error);
    }
}

async function saveDatabasePath() {
    const newPath = document.getElementById('dbPathInput').value.trim();
    if (!newPath) {
        showNotification('路径不能为空', 'error');
        return;
    }

    if (!newPath.endsWith('.db') && !newPath.endsWith('.sqlite') && !newPath.endsWith('.sqlite3')) {
        showNotification('路径必须以 .db 或 .sqlite 结尾', 'error');
        return;
    }

    try {
        const response = await fetch('/api/system/database/path', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: newPath })
        });
        const data = await response.json();

        if (response.ok) {
            promptRestart(data.message || '路径已保存，需重启服务后生效');
        } else {
            showNotification(data.error || '保存失败', 'error');
        }
    } catch (error) {
        showNotification('保存路径失败', 'error');
        console.error('保存数据库路径失败:', error);
    }
}

async function restoreDatabase(input) {
    const file = input.files[0];
    if (!file) return;

    if (!confirm(`确定要从 "${file.name}" 恢复数据库吗？\n\n当前数据库将被覆盖（系统会先自动备份当前数据库），重启后生效。`)) {
        input.value = '';
        return;
    }

    try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch('/api/system/database/restore', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (response.ok) {
            promptRestart(data.message || '恢复成功，需重启服务后生效');
        } else {
            showNotification(data.error || '恢复失败', 'error');
        }
    } catch (error) {
        showNotification('恢复失败', 'error');
        console.error('数据库恢复失败:', error);
    }
    input.value = '';
}

async function deleteBackup(filename) {
    if (!confirm(`确定要删除备份 "${filename}" 吗？`)) return;

    try {
        const response = await fetch(`/api/system/database/backups/${encodeURIComponent(filename)}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (response.ok) {
            showNotification(data.message || '已删除', 'success');
            loadDatabaseInfo();
        } else {
            showNotification(data.error || '删除失败', 'error');
        }
    } catch (error) {
        showNotification('删除失败', 'error');
        console.error('删除备份失败:', error);
    }
}

// ========================================
// 大模型配置管理（OpenAI/Anthropic 接入 + 事件描述/去重）
// ========================================

async function loadLLMConfigs() {
    try {
        const response = await fetch('/api/system/llm-configs');
        llmConfigs = await response.json();
        
        renderLLMConfigsList();
        
        // 加载大模型功能总开关状态
        try {
            const swResp = await fetch('/api/system/llm-enabled');
            const swData = await swResp.json();
            const sw = document.getElementById('llmEnabledSwitch');
            if (sw) sw.checked = swData.llm_enabled;
        } catch (e) {
            console.error('加载大模型总开关状态失败:', e);
        }
        
    } catch (error) {
        console.error('加载大模型配置失败:', error);
        showNotification('加载大模型配置失败', 'error');
    }
}

async function toggleLLMEnabled(enabled) {
    const sw = document.getElementById('llmEnabledSwitch');
    try {
        const response = await fetch('/api/system/llm-enabled', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ llm_enabled: enabled })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || '更新失败');
        }
        showNotification(data.message, 'success');
    } catch (error) {
        console.error('更新大模型总开关失败:', error);
        showNotification('更新大模型总开关失败', 'error');
        // 回滚开关状态
        if (sw) sw.checked = !enabled;
    }
}

// ========================================
// 云平台单点登录（SSO）配置管理
// ========================================

async function loadSSOConfig() {
    try {
        const response = await fetch('/api/auth/sso/config');
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || '加载失败');
        }
        const cfg = await response.json();

        document.getElementById('ssoCloudBaseUrl').value = cfg.cloud_base_url || '';
        document.getElementById('ssoAuthorizeBaseUrl').value = cfg.authorize_base_url || '';
        document.getElementById('ssoClientId').value = cfg.client_id || '';
        document.getElementById('ssoClientSecret').value = cfg.client_secret || '';
        document.getElementById('ssoRedirectUri').value = cfg.redirect_uri || '';
        document.getElementById('ssoScope').value = cfg.scope || '';
        document.getElementById('ssoUsePkce').checked = !!cfg.use_pkce;
        document.getElementById('ssoEnabledSwitch').checked = !!cfg.enabled;
    } catch (error) {
        console.error('加载云平台配置失败:', error);
        showNotification('加载云平台配置失败: ' + error.message, 'error');
    }
}

async function saveSSOConfig() {
    const payload = {
        cloud_base_url: document.getElementById('ssoCloudBaseUrl').value.trim(),
        authorize_base_url: document.getElementById('ssoAuthorizeBaseUrl').value.trim(),
        client_id: document.getElementById('ssoClientId').value.trim(),
        client_secret: document.getElementById('ssoClientSecret').value,
        redirect_uri: document.getElementById('ssoRedirectUri').value.trim(),
        scope: document.getElementById('ssoScope').value.trim(),
        use_pkce: document.getElementById('ssoUsePkce').checked
    };

    if (!payload.cloud_base_url || !payload.client_id || !payload.redirect_uri) {
        showNotification('云平台 API 地址、客户端 ID、回调地址为必填项', 'error');
        return;
    }

    try {
        const response = await fetch('/api/auth/sso/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || '保存失败');
        }
        showNotification(data.message, 'success');
    } catch (error) {
        console.error('保存云平台配置失败:', error);
        showNotification('保存云平台配置失败: ' + error.message, 'error');
    }
}

async function toggleSSOEnabled(enabled) {
    const sw = document.getElementById('ssoEnabledSwitch');
    try {
        const response = await fetch('/api/auth/sso/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabled })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || '更新失败');
        }
        showNotification(enabled ? '单点登录已开启，登录页将显示入口' : '单点登录已关闭，登录页不再显示入口', 'success');
    } catch (error) {
        console.error('更新单点登录开关失败:', error);
        showNotification('更新单点登录开关失败', 'error');
        if (sw) sw.checked = !enabled;
    }
}

function getProviderLabel(provider) {
    const labels = {
        'openai': 'OpenAI 兼容',
        'anthropic': 'Anthropic'
    };
    return labels[provider] || provider;
}

function renderLLMConfigsList() {
    const container = document.getElementById('llmConfigsList');
    if (!container) return;
    
    if (llmConfigs.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                </svg>
                <p>暂无大模型配置</p>
                <button class="btn btn-primary" onclick="System.openCreateLLMModal()">添加配置</button>
            </div>
        `;
        return;
    }
    
    container.innerHTML = llmConfigs.map(config => {
        const enabledBadges = [];
        if (config.enable_description) enabledBadges.push('<span class="server-type">事件描述</span>');
        if (config.enable_dedup) enabledBadges.push('<span class="server-type">语义去重</span>');
        
        return `
            <div class="server-card ${config.is_default ? 'default' : ''} ${!config.enabled ? 'disabled' : ''}">
                <div class="server-card-header">
                    <div class="server-icon">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                        </svg>
                    </div>
                    <div class="server-info">
                        <h4 class="server-name">${escapeHtml(config.name)}</h4>
                        <span class="server-type">${getProviderLabel(config.provider)} · ${escapeHtml(config.model)}</span>
                    </div>
                    <div class="server-info" style="margin-left: 12px;">
                        ${enabledBadges.join('')}
                    </div>
                    ${config.is_default ? '<span class="default-badge">默认</span>' : ''}
                </div>
                
                <div class="server-card-body">
                    <div class="server-detail-row">
                        <span class="detail-label">API 地址:</span>
                        <span class="detail-value">${escapeHtml(config.base_url || (config.provider === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'))}</span>
                    </div>
                    <div class="server-detail-row">
                        <span class="detail-label">API 密钥:</span>
                        <span class="detail-value">${escapeHtml(config.api_key || '未设置')}</span>
                    </div>
                    <div class="server-detail-row">
                        <span class="detail-label">温度:</span>
                        <span class="detail-value">${config.temperature}</span>
                    </div>
                    <div class="server-detail-row">
                        <span class="detail-label">最大 Tokens:</span>
                        <span class="detail-value">${config.max_tokens}</span>
                    </div>
                    ${config.description ? `
                    <div class="server-detail-row">
                        <span class="detail-label">描述:</span>
                        <span class="detail-value">${escapeHtml(config.description)}</span>
                    </div>
                    ` : ''}
                </div>
                
                <div class="server-card-footer">
                    <div class="server-actions">
                        <button class="server-btn" id="llm-test-btn-${config.id}" onclick="System.testLLMConfig(${config.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z"/>
                            </svg>
                            <span>测试</span>
                        </button>
                        ${!config.is_default ? `
                        <button class="server-btn" onclick="System.setDefaultLLMConfig(${config.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/>
                            </svg>
                            设为默认
                        </button>
                        ` : ''}
                        <button class="server-btn" onclick="System.openEditLLMModal(${config.id})">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                            </svg>
                            编辑
                        </button>
                        ${!config.is_default ? `
                        <button class="server-btn danger" onclick="System.openDeleteLLMModal(${config.id}, '${escapeHtml(config.name)}')">
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

function openCreateLLMModal() {
    currentLLMId = null;
    document.getElementById('llmModalTitle').textContent = '添加大模型配置';
    document.getElementById('llmForm').reset();
    document.getElementById('llmProvider').value = 'openai';
    document.getElementById('llmTemperature').value = '';
    document.getElementById('llmMaxTokens').value = '';
    document.getElementById('llmEnableDescription').checked = true;
    document.getElementById('llmEnableDedup').checked = true;
    document.getElementById('llmEnabled').checked = true;
    document.getElementById('llmDefault').checked = false;
    document.getElementById('llmModal').classList.add('show');
}

function openEditLLMModal(configId) {
    const config = llmConfigs.find(c => c.id === configId);
    if (!config) return;
    
    currentLLMId = configId;
    document.getElementById('llmModalTitle').textContent = '编辑大模型配置';
    document.getElementById('llmId').value = configId;
    document.getElementById('llmName').value = config.name;
    document.getElementById('llmProvider').value = config.provider;
    document.getElementById('llmModel').value = config.model;
    document.getElementById('llmBaseUrl').value = config.base_url || '';
    document.getElementById('llmApiKey').value = config.api_key || '';
    document.getElementById('llmTemperature').value = config.temperature;
    document.getElementById('llmMaxTokens').value = config.max_tokens;
    document.getElementById('llmEnableDescription').checked = config.enable_description;
    document.getElementById('llmEnableDedup').checked = config.enable_dedup;
    document.getElementById('llmSupportImages').checked = config.support_images !== false;
    document.getElementById('llmDescription').value = config.description || '';
    document.getElementById('llmEnabled').checked = config.enabled;
    document.getElementById('llmDefault').checked = config.is_default;
    
    document.getElementById('llmModal').classList.add('show');
}

function closeLLMModal() {
    document.getElementById('llmModal').classList.remove('show');
}

async function saveLLMConfig() {
    const name = document.getElementById('llmName').value.trim();
    const model = document.getElementById('llmModel').value.trim();
    
    if (!name) {
        showNotification('请输入配置名称', 'error');
        return;
    }
    if (!model) {
        showNotification('请输入模型名称', 'error');
        return;
    }
    
    const apiKey = document.getElementById('llmApiKey').value;
    const llmData = {
        name: name,
        provider: document.getElementById('llmProvider').value,
        base_url: document.getElementById('llmBaseUrl').value.trim(),
        model: model,
        temperature: parseFloat(document.getElementById('llmTemperature').value) || 0.3,
        max_tokens: parseInt(document.getElementById('llmMaxTokens').value) || 500,
        enable_description: document.getElementById('llmEnableDescription').checked,
        enable_dedup: document.getElementById('llmEnableDedup').checked,
        support_images: document.getElementById('llmSupportImages').checked,
        description: document.getElementById('llmDescription').value,
        enabled: document.getElementById('llmEnabled').checked,
        is_default: document.getElementById('llmDefault').checked
    };
    // 编辑时空值表示保留原密钥，不提交 api_key 字段
    if (apiKey) {
        llmData.api_key = apiKey;
    }
    
    try {
        let response;
        
        if (currentLLMId) {
            response = await fetch(`/api/system/llm-configs/${currentLLMId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(llmData)
            });
        } else {
            response = await fetch('/api/system/llm-configs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(llmData)
            });
        }
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification(currentLLMId ? '大模型配置更新成功' : '大模型配置创建成功', 'success');
            closeLLMModal();
            loadLLMConfigs();
        } else {
            showNotification(result.error || '保存失败', 'error');
        }
        
    } catch (error) {
        console.error('保存大模型配置失败:', error);
        showNotification('保存大模型配置失败', 'error');
    }
}

function openDeleteLLMModal(configId, configName) {
    deleteLLMId = configId;
    document.getElementById('deleteLLMName').textContent = configName;
    document.getElementById('deleteLLMModal').classList.add('show');
}

function closeDeleteLLMModal() {
    document.getElementById('deleteLLMModal').classList.remove('show');
    deleteLLMId = null;
}

async function confirmDeleteLLMConfig() {
    if (!deleteLLMId) return;
    
    try {
        const response = await fetch(`/api/system/llm-configs/${deleteLLMId}`, {
            method: 'DELETE'
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification('大模型配置删除成功', 'success');
            closeDeleteLLMModal();
            loadLLMConfigs();
        } else {
            showNotification(result.error || '删除失败', 'error');
        }
        
    } catch (error) {
        console.error('删除大模型配置失败:', error);
        showNotification('删除大模型配置失败', 'error');
    }
}

async function setDefaultLLMConfig(configId) {
    try {
        const response = await fetch(`/api/system/llm-configs/${configId}/set-default`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showNotification('已设置为默认大模型配置', 'success');
            loadLLMConfigs();
        } else {
            showNotification(result.error || '设置失败', 'error');
        }
        
    } catch (error) {
        console.error('设置默认大模型配置失败:', error);
        showNotification('设置默认大模型配置失败', 'error');
    }
}

// 轻量 Toast 提示（即时视觉反馈，不依赖全局通知中心）
function showLLMToast(message, type) {
    let toast = document.getElementById('llmToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'llmToast';
        document.body.appendChild(toast);
    }
    toast.className = 'llm-toast ' + (type || 'info');
    toast.textContent = message;
    toast.style.display = 'block';
    clearTimeout(showLLMToast._timer);
    showLLMToast._timer = setTimeout(function() {
        toast.style.display = 'none';
    }, 2500);
}

async function testLLMConfig(configId) {
    // 按钮进入测试中状态，防止重复点击
    const btn = document.getElementById('llm-test-btn-' + configId);
    if (btn) {
        btn.disabled = true;
        const label = btn.querySelector('span');
        if (label) {
            btn.dataset.original = label.textContent;
            label.textContent = '测试中...';
        }
    }
    showLLMToast('正在测试大模型连接...', 'info');
    
    try {
        const response = await fetch(`/api/system/llm-configs/${configId}/test`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showLLMToast(`连接成功: ${result.reply || ''}`, 'success');
        } else {
            showLLMToast(result.error || '连接失败', 'error');
        }
        
    } catch (error) {
        console.error('测试大模型连接失败:', error);
        showLLMToast('测试大模型连接失败', 'error');
    } finally {
        // 恢复按钮状态
        if (btn) {
            btn.disabled = false;
            const label = btn.querySelector('span');
            if (label) {
                label.textContent = btn.dataset.original || '测试';
            }
        }
    }
}

// ========================================
// 系统重启（手动重启 + 需重启配置保存后的统一提示）
// ========================================

/**
 * 整系统重启：清理任务进程后主进程退出，由 systemd 自动拉起。
 * 重启期间显示全屏遮罩，轮询 /api/health 待服务恢复后自动刷新页面。
 */
async function restartSystem(skipConfirm) {
    if (!skipConfirm && !confirm('确定要重启系统吗？\n\n重启期间所有检测任务将暂停，系统会安全清理任务进程后自动重启，页面将在服务恢复后自动刷新。')) {
        return;
    }

    const overlay = document.getElementById('restartOverlay');
    if (overlay) overlay.style.display = 'flex';

    try {
        const resp = await fetch('/api/system/restart', { method: 'POST' });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.error || ('HTTP ' + resp.status));
        }
    } catch (e) {
        if (overlay) overlay.style.display = 'none';
        showNotification('重启请求失败: ' + e.message, 'error');
        return;
    }

    // 服务退出后请求会暂时失败，恢复后 /api/health 可访问即刷新页面
    const POLL_INTERVAL = 2000;
    const POLL_MAX = 120;
    let polled = 0;
    const timer = setInterval(async () => {
        polled++;
        try {
            const resp = await fetch('/api/health?t=' + Date.now(), { cache: 'no-store' });
            if (resp.ok) {
                clearInterval(timer);
                window.location.reload();
                return;
            }
        } catch (e) { /* 服务重启中，继续轮询 */ }
        if (polled >= POLL_MAX) {
            clearInterval(timer);
            if (overlay) overlay.style.display = 'none';
            showNotification('等待重启超时，请手动刷新页面确认服务状态', 'error');
        }
    }, POLL_INTERVAL);
}

/**
 * 需重启生效的配置保存后的统一提示，支持立即重启
 */
function promptRestart(message) {
    if (confirm(message + '\n\n是否立即重启系统？')) {
        restartSystem(true);
    }
}

// ========================================
// 暴露命名空间：供 HTML onclick/onchange 调用
// ========================================
return {
    switchTab: switchTab,
    openCreateUserModal: openCreateUserModal,
    openCreateServerModal: openCreateServerModal,
    openEditServerModal: openEditServerModal,
    openDeleteServerModal: openDeleteServerModal,
    closeServerModal: closeServerModal,
    saveServer: saveServer,
    closeDeleteServerModal: closeDeleteServerModal,
    confirmDeleteServer: confirmDeleteServer,
    setDefaultServer: setDefaultServer,
    openEditUserModal: openEditUserModal,
    openDeleteUserModal: openDeleteUserModal,
    closeUserModal: closeUserModal,
    saveUser: saveUser,
    closeDeleteUserModal: closeDeleteUserModal,
    confirmDeleteUser: confirmDeleteUser,
    loadLogContent: loadLogContent,
    downloadLogFile: downloadLogFile,
    clearLogFile: clearLogFile,
    loadMonitorData: loadMonitorData,
    saveWebServerConfig: saveWebServerConfig,
    saveSystemConfig: saveSystemConfig,
    saveNodeManagementConfig: saveNodeManagementConfig,
    loadDatabaseInfo: loadDatabaseInfo,
    backupDatabase: backupDatabase,
    restoreDatabase: restoreDatabase,
    saveDatabasePath: saveDatabasePath,
    deleteBackup: deleteBackup,
    openCreateLLMModal: openCreateLLMModal,
    openEditLLMModal: openEditLLMModal,
    closeLLMModal: closeLLMModal,
    saveLLMConfig: saveLLMConfig,
    openDeleteLLMModal: openDeleteLLMModal,
    closeDeleteLLMModal: closeDeleteLLMModal,
    confirmDeleteLLMConfig: confirmDeleteLLMConfig,
    setDefaultLLMConfig: setDefaultLLMConfig,
    testLLMConfig: testLLMConfig,
    toggleLLMEnabled: toggleLLMEnabled,
    loadSSOConfig: loadSSOConfig,
    saveSSOConfig: saveSSOConfig,
    toggleSSOEnabled: toggleSSOEnabled,
    saveVoiceConfig: saveVoiceConfig,
    testVoice: testVoice,
    playVoiceFile: playVoiceFile,
    uploadVoiceFile: uploadVoiceFile,
    deleteVoiceFile: deleteVoiceFile,
    restartSystem: restartSystem,
    promptRestart: promptRestart
};

})();