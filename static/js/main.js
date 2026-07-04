/**
 * SageSight AI - 主 JavaScript 文件
 */

// ========================================
// WebSocket 连接
// ========================================

let socket = null;

function initSocketIO() {
    if (socket) return socket;
    
    try {
        socket = io('/ws', {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionAttempts: 10
        });
        
        socket.on('connect', function() {
            console.log('WebSocket 已连接');
            showNotification('已连接到服务器', 'success');
        });
        
        socket.on('disconnect', function() {
            console.log('WebSocket 已断开');
            showNotification('连接已断开，正在重连...', 'warning');
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

const notifications = [];

function showNotification(message, type = 'info', duration = 3000) {
    const container = getNotificationContainer();
    
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.innerHTML = `
        <span class="notification-message">${message}</span>
        <button class="notification-close" onclick="dismissNotification(this.parentElement)">&times;</button>
    `;
    
    container.appendChild(notification);
    notifications.push(notification);
    
    // 自动消失
    if (duration > 0) {
        setTimeout(() => dismissNotification(notification), duration);
    }
    
    // 最多显示5条
    while (notifications.length > 5) {
        dismissNotification(notifications[0]);
    }
}

function dismissNotification(element) {
    if (!element) return;
    
    element.classList.add('notification-dismiss');
    setTimeout(() => {
        if (element.parentNode) {
            element.parentNode.removeChild(element);
        }
        const index = notifications.indexOf(element);
        if (index > -1) {
            notifications.splice(index, 1);
        }
    }, 300);
}

function getNotificationContainer() {
    let container = document.querySelector('.notification-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'notification-container';
        container.style.cssText = `
            position: fixed;
            top: 80px;
            right: 20px;
            z-index: 9999;
            display: flex;
            flex-direction: column;
            gap: 10px;
        `;
        document.body.appendChild(container);
    }
    return container;
}

// 添加通知样式
const notificationStyles = document.createElement('style');
notificationStyles.textContent = `
    .notification {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 12px 16px;
        background: var(--card-bg, #ffffff);
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        border-left: 4px solid var(--primary-color, #4a90e2);
        min-width: 280px;
        max-width: 400px;
        animation: slideIn 0.3s ease;
    }
    
    .notification-success {
        border-left-color: var(--success-color, #4caf50);
        background: #f0f9eb;
    }
    
    .notification-warning {
        border-left-color: var(--warning-color, #ff9800);
        background: #fdf6ec;
    }
    
    .notification-error {
        border-left-color: var(--danger-color, #f44336);
        background: #fef0f0;
    }
    
    .notification-info {
        border-left-color: var(--info-color, #2196f3);
        background: #f0f7ff;
    }
    
    .notification-message {
        flex: 1;
        font-size: 14px;
        color: var(--text-primary, #333333);
    }
    
    .notification-close {
        background: none;
        border: none;
        color: var(--text-secondary, #666666);
        font-size: 20px;
        cursor: pointer;
        padding: 0;
        line-height: 1;
    }
    
    .notification-close:hover {
        color: var(--text-primary, #333333);
    }
    
    .notification-dismiss {
        animation: slideOut 0.3s ease forwards;
    }
    
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }
`;
document.head.appendChild(notificationStyles);

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
// 页面初始化
// ========================================

document.addEventListener('DOMContentLoaded', function() {
    // 初始化主题
    initTheme();

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

window.SageSight = {
    socket: () => socket,
    showNotification,
    dismissNotification,
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
};
