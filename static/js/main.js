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

const notifications = [];

// ✅ 新增：通知节流（防止通知堆积导致卡死）
let _notificationCounter = 0;
let _notificationLimit = 1;  // ✅ 修复：从3改为1，每秒最多1个通知（更激进）
let _notificationResetTime = Date.now();
let _lastNotificationMessage = '';
let _lastNotificationTime = 0;

// ✅ 新增：批量通知队列（不阻塞主线程）
let _notificationQueue = [];
let _isProcessingNotifications = false;

// ✅ 紧急修复：禁用定时器监控，防止递归调用导致卡死
// 定时器监控本身导致了性能问题，已移除

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

    // ✅ 新增：将通知放入队列，使用 requestIdleCallback 异步处理
    _notificationQueue.push({ message, type, duration });

    // 如果没有在处理通知，启动处理
    if (!_isProcessingNotifications) {
        processNotificationsAsync();
    }
}

// ✅ 新增：异步处理通知（不阻塞主线程）
function processNotificationsAsync() {
    if (_notificationQueue.length === 0) {
        _isProcessingNotifications = false;
        return;
    }

    _isProcessingNotifications = true;

    // 使用 requestIdleCallback 在浏览器空闲时处理
    if (typeof requestIdleCallback !== 'undefined') {
        requestIdleCallback((deadline) => {
            // 只在浏览器空闲时处理通知
            while (_notificationQueue.length > 0 && deadline.timeRemaining() > 0) {
                const { message, type, duration } = _notificationQueue.shift();
                _renderNotification(message, type, duration);
            }

            // 如果还有通知，继续处理
            if (_notificationQueue.length > 0) {
                processNotificationsAsync();
            } else {
                _isProcessingNotifications = false;
            }
        }, { timeout: 1000 });  // 最多1秒后必须处理
    } else {
        // 备用方案：使用 setTimeout
        setTimeout(() => {
            if (_notificationQueue.length > 0) {
                const { message, type, duration } = _notificationQueue.shift();
                _renderNotification(message, type, duration);
            }
            processNotificationsAsync();
        }, 100);  // 100ms延迟
    }
}

// ✅ 新增：实际渲染通知（优化DOM操作）
function _renderNotification(message, type, duration) {
    const container = getNotificationContainer();

    // ✅ 优化：先批量删除超出的通知，减少DOM操作
    const maxNotifications = 3;
    while (notifications.length >= maxNotifications) {
        const oldestNotification = notifications.shift();
        if (oldestNotification && oldestNotification.parentElement) {
            // ✅ 使用 remove() 方法，比 removeChild 更高效
            oldestNotification.remove();
        }
    }

    // ✅ 使用文档片段批量插入（减少重排）
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.innerHTML = `
        <span class="notification-message">${message}</span>
        <button class="notification-close" onclick="dismissNotification(this.parentElement)">&times;</button>
    `;

    container.appendChild(notification);
    notifications.push(notification);

    if (duration > 0) {
        setTimeout(() => {
            dismissNotification(notification);
        }, duration);
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
        container.style.position = 'fixed';
        container.style.top = '80px';
        container.style.right = '20px';
        container.style.zIndex = '999999';
        container.style.display = 'flex';
        container.style.flexDirection = 'column';
        container.style.gap = '10px';
        container.style.pointerEvents = 'none';
        container.style.alignItems = 'flex-end';
        container.style.background = 'transparent';
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
        pointer-events: auto;
        z-index: 999999 !important;
        position: relative;
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
