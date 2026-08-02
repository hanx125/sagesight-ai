/**
 * SageSight AI - 主 JavaScript 文件
 */

// ========================================
// WebSocket 连接
// ========================================

let socket = null;

/**
 * 获取全局 Socket.IO 连接
 * @returns {Object|null} Socket.IO 连接对象
 */
function getSocket() {
    if (!socket) {
        initSocketIO();
    }
    return socket;
}

/**
 * 重置 WebSocket 连接（pjax 导航时调用）
 * 断开旧连接（清除所有页面特定的监听器），重新建立连接并注册核心监听器
 */
function resetSocket() {
    if (socket) {
        try {
            socket.removeAllListeners();
            socket.disconnect();
        } catch (e) {
            console.error('重置 WebSocket 失败:', e);
        }
        socket = null;
    }
    // 重新初始化（注册 main.js 的核心监听器）
    initSocketIO();
}

function initSocketIO() {
    if (socket) return socket;

    try {
        // ✅ 修复：显式使用 HTTP 协议，避免浏览器 HSTS 缓存导致的 HTTPS 强制升级
        // 浏览器 HSTS 缓存会强制将 HTTP 升级为 HTTPS，导致 Socket.IO 连接失败
        // 所以我们需要显式指定使用 HTTP 协议
        socket = io('/ws', {
            path: '/socket.io',
            transports: ['polling', 'websocket'],  // ✅ 改为 polling 优先（更稳定）
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionAttempts: 10
        });

        socket.on('connect', function() {
            console.log('WebSocket 已连接');
            // ✅ 紧急修复：禁用连接通知，防止主线程阻塞
            // showNotification('已连接到服务器', 'success');
        });

        socket.on('disconnect', function() {
            console.log('WebSocket 已断开');
            // ✅ 紧急修复：禁用断开通知
            // showNotification('连接已断开，正在重连...', 'warning');
        });
        
        socket.on('connect_error', function(error) {
            console.error('WebSocket 连接错误:', error);
        });
        
        // 任务事件
        socket.on('task_created', function(data) {
            showNotification(`新任务已创建 (ID: ${data.task_id})`, 'success');
        });
        
        socket.on('task_updated', function(data) {
            showNotification('任务已更新', 'info');
        });
        
        socket.on('task_deleted', function(data) {
            showNotification('任务已删除', 'info');
        });
        
        socket.on('task_status_changed', function(data) {
            const statusText = data.status === 'running' ? '已启动' : '已停止';
            showNotification(`任务 ${statusText}`, 'success');
        });
        
        // 事件通知
        socket.on('new_event', function(event) {
            // ✅ 性能监控：通知统计
            if (window.PerfMonitor) {
                PerfMonitor.monitorNotification();
            }

            // ✅ 优化：使用异步通知，不阻塞主线程
            showNotification(`新事件: ${event.description || event.type}`, 'warning');
        });
        
        socket.on('event_acknowledged', function(data) {
            console.log('事件已确认:', data.event_id);
        });
        
        return socket;
    } catch (error) {
        console.error('初始化 WebSocket 失败:', error);
        return null;
    }
}

// ========================================
// 通知系统
// ========================================

// ✅ 新增：通知节流（防止通知堆积导致卡死）
let _notificationCounter = 0;
let _notificationLimit = 1;  // ✅ 修复：从3改为1，每秒最多1个通知（更激进）
let _notificationResetTime = Date.now();
let _lastNotificationMessage = '';
let _lastNotificationTime = 0;

// ✅ 新增：批量通知队列（不阻塞主线程）
let _notificationQueue = [];
let _isProcessingNotifications = false;

// ✅ 标题栏通知中心：未读列表 + 历史记录
let _notifyUnread = [];       // 未读通知列表（{message, type, time}）
let _notifyHistory = [];      // 全部历史（最多保留 50 条）
const _NOTIFY_HISTORY_MAX = 50;

function showNotification(message, type = 'info', duration = 2000) {
    // ✅ 节流：每秒最多1个通知
    const now = Date.now();
    if (now - _notificationResetTime > 1000) {
        _notificationCounter = 0;
        _notificationResetTime = now;
    }

    _notificationCounter++;
    if (_notificationCounter > _notificationLimit) {
        // 超过限制，丢弃这个通知
        return;
    }

    // ✅ 合并相同通知：如果2秒内有相同通知，跳过（更激进）
    if (message === _lastNotificationMessage && now - _lastNotificationTime < 2000) {
        return;
    }

    _lastNotificationMessage = message;
    _lastNotificationTime = now;

    // ✅ 只加入标题栏通知中心，不再弹出右上角 toast
    _addNotifyToCenter(message, type);
}

// ✅ 标题栏通知中心：添加一条未读通知
function _addNotifyToCenter(message, type) {
    const item = { message, type, time: Date.now() };
    _notifyUnread.unshift(item);
    _notifyHistory.unshift(item);
    if (_notifyHistory.length > _NOTIFY_HISTORY_MAX) {
        _notifyHistory.length = _NOTIFY_HISTORY_MAX;
    }
    _updateNotifyBadge();
    _renderNotifyList();
}

// ✅ 更新铃铛未读数 badge
function _updateNotifyBadge() {
    const badge = document.getElementById('notifyBadge');
    if (!badge) return;
    const count = _notifyUnread.length;
    if (count > 0) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = 'flex';
    } else {
        badge.style.display = 'none';
    }
}

// ✅ 渲染通知下拉列表（未读优先，空时显示历史）
function _renderNotifyList() {
    const list = document.getElementById('notifyPanelList');
    if (!list) return;

    const items = _notifyUnread.length > 0 ? _notifyUnread : _notifyHistory;
    if (items.length === 0) {
        list.innerHTML = '<div class="notify-empty">暂无通知</div>';
        return;
    }

    list.innerHTML = items.map(item => `
        <div class="notify-item">
            <div class="notify-item-icon ${item.type}"></div>
            <div class="notify-item-content">
                <div class="notify-item-message">${_escapeHtml(item.message)}</div>
                <div class="notify-item-time">${_formatNotifyTime(item.time)}</div>
            </div>
        </div>
    `).join('');
}

// ✅ HTML 转义（防止通知内容注入）
function _escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ✅ 格式化通知时间
function _formatNotifyTime(ts) {
    const diff = Date.now() - ts;
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
    if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前';
    const d = new Date(ts);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// ✅ 切换通知下拉面板
function toggleNotifyPanel() {
    const panel = document.getElementById('notifyPanel');
    if (!panel) return;
    const isVisible = panel.classList.contains('show');
    panel.classList.toggle('show', !isVisible);
    // 打开面板时标记已读（清除未读数，但保留历史显示）
    if (!isVisible) {
        _notifyUnread = [];
        _updateNotifyBadge();
        _renderNotifyList();
    }
}

// ✅ 全部已读（清空未读 + 历史）
function clearAllNotifications() {
    _notifyUnread = [];
    _notifyHistory = [];
    _updateNotifyBadge();
    _renderNotifyList();
}

// ========================================
// 工具函数
// ========================================

/**
 * 深拷贝对象
 */
function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
}

/**
 * 防抖函数
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * 节流函数
 */
function throttle(func, limit) {
    let inThrottle;
    return function(...args) {
        if (!inThrottle) {
            func.apply(this, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

/**
 * 格式化日期时间
 */
function formatDateTime(date, format = 'YYYY-MM-DD HH:mm:ss') {
    const d = new Date(date);
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hours = String(d.getHours()).padStart(2, '0');
    const minutes = String(d.getMinutes()).padStart(2, '0');
    const seconds = String(d.getSeconds()).padStart(2, '0');
    
    return format
        .replace('YYYY', year)
        .replace('MM', month)
        .replace('DD', day)
        .replace('HH', hours)
        .replace('mm', minutes)
        .replace('ss', seconds);
}

/**
 * 获取相对时间字符串
 */
function getRelativeTime(date) {
    const now = new Date();
    const then = new Date(date);
    const diff = now - then;
    
    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);
    
    if (seconds < 60) return '刚刚';
    if (minutes < 60) return `${minutes} 分钟前`;
    if (hours < 24) return `${hours} 小时前`;
    if (days < 7) return `${days} 天前`;
    
    return formatDateTime(date, 'MM-DD HH:mm');
}

/**
 * HTML 转义
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * URL 参数解析
 */
function parseUrlParams() {
    const params = {};
    const searchParams = new URLSearchParams(window.location.search);
    for (const [key, value] of searchParams) {
        params[key] = value;
    }
    return params;
}

/**
 * 复制到剪贴板
 */
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        showNotification('已复制到剪贴板', 'success');
        return true;
    } catch (err) {
        console.error('复制失败:', err);
        return false;
    }
}

/**
 * 下载文件
 */
function downloadFile(content, filename, contentType = 'text/plain') {
    const blob = new Blob([content], { type: contentType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

/**
 * 请求封装
 */
async function apiRequest(url, options = {}) {
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json'
        }
    };
    
    const mergedOptions = {
        ...defaultOptions,
        ...options,
        headers: {
            ...defaultOptions.headers,
            ...options.headers
        }
    };
    
    try {
        const response = await fetch(url, mergedOptions);
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({ error: '请求失败' }));
            throw new Error(error.error || `HTTP ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API 请求失败:', error);
        showNotification(error.message, 'error');
        throw error;
    }
}

// ========================================
// 主题切换
// ========================================

/**
 * 切换主题（墨绿/白色）
 */
function toggleTheme() {
    const body = document.body;
    const isLightTheme = body.classList.contains('theme-light');

    if (isLightTheme) {
        // 切换到墨绿主题
        body.classList.remove('theme-light');
        localStorage.setItem('theme', 'green');
        showNotification('已切换到墨绿主题', 'info');
    } else {
        // 切换到白色主题
        body.classList.add('theme-light');
        localStorage.setItem('theme', 'light');
        showNotification('已切换到白色主题', 'info');
    }
}

/**
 * 初始化主题
 */
function initTheme() {
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'light') {
        document.body.classList.add('theme-light');
    }
}

// ========================================
// 全屏切换（标题栏全局按钮：整个系统页面浏览器全屏）
// ========================================

/**
 * 切换整个系统页面的浏览器全屏
 * 供标题栏全局全屏按钮调用；电视墙单独全屏见 monitor.js 的 toggleMonitorWallFullscreen
 */
function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(function(err) {
            console.error('进入全屏失败:', err);
        });
    } else {
        document.exitFullscreen();
    }
}

/**
 * 初始化全屏状态监听
 * 根据浏览器全屏状态切换标题栏全屏按钮的进入/退出图标
 */
function initFullscreenListener() {
    document.addEventListener('fullscreenchange', function() {
        const isFullscreen = !!document.fullscreenElement;
        const enterIcon = document.querySelector('.fullscreen-enter-icon');
        const exitIcon = document.querySelector('.fullscreen-exit-icon');
        if (enterIcon) enterIcon.style.display = isFullscreen ? 'none' : '';
        if (exitIcon) exitIcon.style.display = isFullscreen ? '' : 'none';
    });
}

// ========================================
// 页面初始化
// ========================================

document.addEventListener('DOMContentLoaded', function() {
    // 初始化主题
    initTheme();

    // 初始化全屏监听
    initFullscreenListener();

    // 检查登录状态并加载用户信息
    checkSessionAndLoadUser();

    // 初始化 WebSocket
    initSocketIO();

    // 添加全局错误处理
    window.addEventListener('error', function(event) {
        console.error('全局错误:', event.error);
    });

    window.addEventListener('unhandledrejection', function(event) {
        console.error('未处理的 Promise 拒绝:', event.reason);
    });
});

// ========================================
// 认证相关
// ========================================

/**
 * 检查会话状态并加载用户信息
 */
async function checkSessionAndLoadUser() {
    try {
        const response = await fetch('/api/auth/check');
        const data = await response.json();
        
        if (data.logged_in && data.user) {
            updateUserInfo(data.user);
        } else {
            // 未登录，跳转到登录页
            window.location.href = '/login?redirect=' + encodeURIComponent(window.location.pathname);
        }
    } catch (error) {
        console.error('检查会话失败:', error);
    }
}

/**
 * 更新导航栏用户信息
 */
function updateUserInfo(user) {
    const displayName = user.display_name || user.username;
    const roleText = user.role === 'admin' ? '管理员' : '用户';
    
    const navUserName = document.getElementById('navUserName');
    const navUserRole = document.getElementById('navUserRole');
    const menuUserName = document.getElementById('menuUserName');
    const menuUserRole = document.getElementById('menuUserRole');
    
    if (navUserName) navUserName.textContent = displayName;
    if (navUserRole) navUserRole.textContent = roleText;
    if (menuUserName) menuUserName.textContent = displayName;
    if (menuUserRole) menuUserRole.textContent = `${user.username} · ${roleText}`;
}

/**
 * 切换用户下拉菜单
 */
function toggleUserMenu() {
    const menu = document.getElementById('userDropdownMenu');
    const header = document.getElementById('headerUser');
    if (menu) {
        const isVisible = menu.style.display !== 'none';
        menu.style.display = isVisible ? 'none' : 'block';
        if (header) header.classList.toggle('active', !isVisible);
    }
}

// 点击外部关闭下拉菜单
document.addEventListener('click', function(e) {
    const header = document.getElementById('headerUser');
    const menu = document.getElementById('userDropdownMenu');
    if (header && menu && !header.contains(e.target)) {
        menu.style.display = 'none';
        header.classList.remove('active');
    }
    // 关闭通知面板
    const notifyWrap = document.getElementById('headerNotify');
    const notifyPanel = document.getElementById('notifyPanel');
    if (notifyWrap && notifyPanel && !notifyWrap.contains(e.target)) {
        notifyPanel.classList.remove('show');
    }
});

/**
 * 退出登录
 */
async function logout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        window.location.href = '/login';
    } catch (error) {
        console.error('登出失败:', error);
        window.location.href = '/login';
    }
}

// 全局fetch拦截器：401时重定向到登录页
const originalFetch = window.fetch;
window.fetch = function(...args) {
    return originalFetch.apply(this, args).then(response => {
        if (response.status === 401) {
            const currentPath = window.location.pathname;
            if (currentPath !== '/login') {
                window.location.href = '/login?redirect=' + encodeURIComponent(currentPath);
            }
        }
        return response;
    });
};

// ========================================
// 导出全局函数
// ========================================

// 合并到已有的 SageSight 对象（不覆盖 head 存根中定义的 pages/registerPage/_pending）
window.SageSight = window.SageSight || {};
Object.assign(window.SageSight, {
    socket: () => socket,
    showNotification,
    toggleNotifyPanel,
    clearAllNotifications,
    resetSocket,
    deepClone,
    debounce,
    throttle,
    formatDateTime,
    getRelativeTime,
    escapeHtml,
    parseUrlParams,
    copyToClipboard,
    downloadFile,
    apiRequest,
    checkSessionAndLoadUser,
    updateUserInfo,
    toggleUserMenu,
    logout
});
