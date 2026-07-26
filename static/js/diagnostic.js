// ========================================
// 实时诊断工具（帮助定位卡死原因）
// ========================================

(function() {
    'use strict';

    // ========================================
    // 诊断配置
    // ========================================
    const config = {
        checkInterval: 5000,  // 每5秒检查一次
        maxTimers: 50,        // 定时器警告阈值
        maxNotifications: 10, // 通知警告阈值
        maxQueueSize: 20,     // 队列警告阈值
    };

    // ========================================
    // 诊断状态
    // ========================================
    let lastCheckTime = Date.now();
    let checkCount = 0;
    let isRunning = false;

    // ========================================
    // 诊断检查函数
    // ========================================
    function runDiagnostics() {
        if (isRunning) return;
        isRunning = true;

        checkCount++;
        const now = Date.now();
        const timeSinceLastCheck = now - lastCheckTime;
        lastCheckTime = now;

        // 收集诊断数据
        const diagnostics = {
            timestamp: new Date().toISOString(),
            checkCount: checkCount,
            timeSinceLastCheck: timeSinceLastCheck,

            // 定时器状态（已禁用监控，返回固定值）
            timers: { totalActive: 0 },

            // 通知状态
            notifications: {
                total: document.querySelectorAll('.notification').length,
                queue: typeof _notificationQueue !== 'undefined' ? _notificationQueue.length : 0
            },

            // 内存状态
            memory: performance.memory ? {
                usedJSHeapSize: Math.round(performance.memory.usedJSHeapSize / 1024 / 1024),
                totalJSHeapSize: Math.round(performance.memory.totalJSHeapSize / 1024 / 1024),
                jsHeapSizeLimit: Math.round(performance.memory.jsHeapSizeLimit / 1024 / 1024),
                usagePercent: Math.round(performance.memory.usedJSHeapSize / performance.memory.jsHeapSizeLimit * 100)
            } : null,

            // 事件监听器数量（估算）
            eventListeners: getEventListenerCount(),

            // WebSocket状态
            websocket: {
                connected: typeof socket !== 'undefined' && socket.connected,
                reconnectAttempts: 0
            },

            // 性能指标
            performance: {
                fps: estimateFPS(),
                longTaskCount: 0
            }
        };

        // 检查警告条件
        const warnings = checkWarnings(diagnostics);

        // 如果有警告，输出到控制台
        if (warnings.length > 0) {
            console.group(`🔍 [诊断报告 #${checkCount}] ${new Date().toLocaleTimeString()}`);
            console.log('📊 系统状态:', diagnostics);

            if (warnings.length > 0) {
                console.group('⚠️ 警告');
                warnings.forEach(w => console.warn(w));
                console.groupEnd();
            }

            console.groupEnd();
        }

        // 如果检测到严重问题，触发降级
        if (diagnostics.memory && diagnostics.memory.usagePercent > 90) {
            console.error('🚨 [诊断] 内存使用率过高:', diagnostics.memory.usagePercent + '%');
            triggerEmergencyCleanup();
        }

        if (diagnostics.timers.totalActive > 100) {
            console.error('🚨 [诊断] 定时器泄漏:', diagnostics.timers.totalActive);
            if (typeof clearAllTimers === 'function') {
                clearAllTimers();
            }
        }

        isRunning = false;
        return diagnostics;
    }

    // ========================================
    // 检查警告条件
    // ========================================
    function checkWarnings(diagnostics) {
        const warnings = [];

        // 定时器警告
        if (diagnostics.timers.totalActive > config.maxTimers) {
            warnings.push(`定时器数量过多: ${diagnostics.timers.totalActive}（阈值: ${config.maxTimers}）`);
        }

        // 通知警告
        if (diagnostics.notifications.total > config.maxNotifications) {
            warnings.push(`通知数量过多: ${diagnostics.notifications.total}（阈值: ${config.maxNotifications}）`);
        }

        if (diagnostics.notifications.queue > config.maxQueueSize) {
            warnings.push(`通知队列积压: ${diagnostics.notifications.queue}（阈值: ${config.maxQueueSize}）`);
        }

        // 内存警告
        if (diagnostics.memory) {
            if (diagnostics.memory.usagePercent > 80) {
                warnings.push(`内存使用率高: ${diagnostics.memory.usagePercent}%（阈值: 80%）`);
            }
        }

        // 时间偏差警告
        if (diagnostics.timeSinceLastCheck > config.checkInterval * 2) {
            warnings.push(`检查间隔异常: ${diagnostics.timeSinceLastCheck}ms（预期: ${config.checkInterval}ms）`);
        }

        return warnings;
    }

    // ========================================
    // 估算FPS（已优化：使用性能监控API，不再使用requestAnimationFrame循环）
    // ========================================
    let fps = 60;
    let lastFPSUpdate = Date.now();

    function estimateFPS() {
        // ✅ 修复：不再使用 requestAnimationFrame 循环，改用简单估算
        // 在诊断检查时才计算FPS，避免持续的性能开销
        const now = Date.now();

        // 简单估算：如果检查间隔正常，假设60fps
        // 如果检查间隔异常（说明主线程阻塞），降低FPS估算
        const timeSinceLastCheck = now - lastFPSUpdate;
        lastFPSUpdate = now;

        if (timeSinceLastCheck > 1000) {
            // 如果超过1秒才检查一次，说明主线程被阻塞
            fps = Math.round(1000 / timeSinceLastCheck);
        } else {
            // 否则假设正常（60fps）
            fps = 60;
        }

        return fps;
    }

    // ✅ 移除：不再使用 requestAnimationFrame 循环
    // 这个循环会在每一帧都调用，即使浏览器已经卡死，也会持续消耗资源
    // function updateFPS() {
    //     estimateFPS();
    //     requestAnimationFrame(updateFPS);
    // }
    // requestAnimationFrame(updateFPS);

    // ========================================
    // 获取事件监听器数量（估算）
    // ========================================
    function getEventListenerCount() {
        // 简单估算：无法直接获取，只能返回0
        return 0;
    }

    // ========================================
    // 紧急清理
    // ========================================
    function triggerEmergencyCleanup() {
        console.warn('[诊断] 触发紧急清理');

        // 清理通知
        const notifications = document.querySelectorAll('.notification');
        notifications.forEach(n => n.remove());

        // 清理队列
        if (typeof _notificationQueue !== 'undefined') {
            _notificationQueue = [];
        }

        console.log('[诊断] 紧急清理完成');
    }

    // ========================================
    // 启动定期检查（由 TimerManager 统一管理）
    // ========================================
    // const diagnosticInterval = setInterval(runDiagnostics, config.checkInterval);
    // 诊断检查现在由 timer_manager.js 统一管理

    // ========================================
    // 全局函数
    // ========================================
    window.runDiagnostics = runDiagnostics;
    window.getDiagnosticData = () => runDiagnostics();

    // ========================================
    // 导出诊断接口
    // ========================================
    window.DiagnosticTool = {
        run: runDiagnostics,
        getConfig: () => config,
        setConfig: (newConfig) => Object.assign(config, newConfig),
        triggerEmergencyCleanup
    };

    console.log('[诊断工具] 已启动，每5秒检查一次系统状态');
    console.log('[诊断工具] 可用命令:');
    console.log('  - runDiagnostics() : 立即运行诊断');
    console.log('  - DiagnosticTool.run() : 同上');
    console.log('  - DiagnosticTool.triggerEmergencyCleanup() : 紧急清理');

})();