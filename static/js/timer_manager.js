// ========================================
// 定时器管理器（统一管理所有定时器）
// ========================================

(function() {
    'use strict';

    // ========================================
    // 定时器注册表
    // ========================================
    const timers = {
        // ✅ 必须保留：核心功能定时器
        essential: {
            streamStatusCheck: null,      // 流状态检查
            systemMonitor: null           // 系统监控
        },

        // ⚠️ 可选保留：监控类定时器
        monitoring: {
            diagnostic: null,             // 诊断检查
            performanceMonitor: null      // 性能监控
        },

        // ❌ 可以删除：非必要定时器
        optional: {
            audioLevelCheck: null,        // 音频级别检查
            heartbeat: null               // 心跳检测
        }
    };

    // ========================================
    // 定时器配置
    // ========================================
    const config = {
        // 核心功能（必须）
        streamStatusInterval: 5000,      // 流状态检查：5秒
        systemMonitorInterval: 5000,     // 系统监控：5秒

        // 监控功能（可选）
        diagnosticInterval: 10000,       // 诊断检查：10秒（从5秒改为10秒）
        performanceMonitorInterval: 3000, // 性能监控：3秒（从1秒改为3秒）

        // 非必要（可关闭）
        audioLevelCheckEnabled: false,   // 音频级别检查：默认关闭
        heartbeatEnabled: false          // 心跳检测：默认关闭
    };

    // ========================================
    // 启动函数
    // ========================================

    // ✅ 流状态检查（必须）
    function startStreamStatusCheck() {
        if (timers.essential.streamStatusCheck) return;

        timers.essential.streamStatusCheck = setInterval(() => {
            // 流状态检查逻辑（在 monitor.js 中实现）
            if (typeof checkStreamStatus === 'function') {
                checkStreamStatus();
            }
        }, config.streamStatusInterval);

        console.log('[定时器管理] 流状态检查已启动');
    }

    // ✅ 系统监控（必须）
    function startSystemMonitor() {
        if (timers.essential.systemMonitor) return;

        timers.essential.systemMonitor = setInterval(() => {
            // 系统监控逻辑（在 system.js 命名空间内实现）
            if (window.System && typeof window.System.loadMonitorData === 'function') {
                window.System.loadMonitorData();
            }
        }, config.systemMonitorInterval);

        console.log('[定时器管理] 系统监控已启动');
    }

    // ⚠️ 诊断检查（可选）
    function startDiagnostic() {
        if (timers.monitoring.diagnostic) return;

        timers.monitoring.diagnostic = setInterval(() => {
            // 诊断逻辑（在 diagnostic.js 中实现）
            if (typeof runDiagnostics === 'function') {
                runDiagnostics();
            }
        }, config.diagnosticInterval);

        console.log('[定时器管理] 诊断检查已启动');
    }

    // ⚠️ 性能监控（可选）
    function startPerformanceMonitor() {
        if (timers.monitoring.performanceMonitor) return;

        timers.monitoring.performanceMonitor = setInterval(() => {
            // 性能监控逻辑（在 performance_monitor.js 中实现）
            if (typeof updateMonitorPanel === 'function') {
                const panel = document.getElementById('performance-monitor-panel');
                if (panel && panel.style.display !== 'none') {
                    updateMonitorPanel();
                }
            }
        }, config.performanceMonitorInterval);

        console.log('[定时器管理] 性能监控已启动');
    }

    // ❌ 音频级别检查（非必要）
    function startAudioLevelCheck() {
        if (!config.audioLevelCheckEnabled) return;
        // 音频级别检查逻辑
    }

    // ❌ 心跳检测（非必要）
    function startHeartbeat() {
        if (!config.heartbeatEnabled) return;
        // 心跳检测逻辑
    }

    // ✅ 停止系统监控（由 system 页面离开 monitor tab 时调用）
    function stopSystemMonitor() {
        if (timers.essential.systemMonitor) {
            clearInterval(timers.essential.systemMonitor);
            timers.essential.systemMonitor = null;
            console.log('[定时器管理] 系统监控已停止');
        }
    }

    // ========================================
    // 停止函数
    // ========================================

    function stopAll() {
        console.warn('[定时器管理] 停止所有定时器');

        // 停止必须的定时器
        Object.keys(timers.essential).forEach(key => {
            if (timers.essential[key]) {
                clearInterval(timers.essential[key]);
                timers.essential[key] = null;
            }
        });

        // 停止监控定时器
        Object.keys(timers.monitoring).forEach(key => {
            if (timers.monitoring[key]) {
                clearInterval(timers.monitoring[key]);
                timers.monitoring[key] = null;
            }
        });

        // 停止可选定时器
        Object.keys(timers.optional).forEach(key => {
            if (timers.optional[key]) {
                clearInterval(timers.optional[key]);
                timers.optional[key] = null;
            }
        });
    }

    function stopMonitoring() {
        console.log('[定时器管理] 停止监控定时器');

        Object.keys(timers.monitoring).forEach(key => {
            if (timers.monitoring[key]) {
                clearInterval(timers.monitoring[key]);
                timers.monitoring[key] = null;
            }
        });
    }

    // ========================================
    // 启动所有必须的定时器
    // ========================================
    function startEssential() {
        console.log('[定时器管理] 启动核心定时器');
        startStreamStatusCheck();
        // 系统监控（startSystemMonitor）仅由 system 页面按需启动，
        // 不在全局核心定时器中启动，避免 pjax 导航到非 system 页面后 loadMonitorData 操作空 DOM
    }

    function startAll() {
        console.log('[定时器管理] 启动所有定时器');
        startEssential();
        startDiagnostic();
        startPerformanceMonitor();
    }

    // ========================================
    // 获取状态
    // ========================================
    function getStatus() {
        const status = {
            essential: {},
            monitoring: {},
            optional: {}
        };

        Object.keys(timers.essential).forEach(key => {
            status.essential[key] = timers.essential[key] !== null;
        });

        Object.keys(timers.monitoring).forEach(key => {
            status.monitoring[key] = timers.monitoring[key] !== null;
        });

        Object.keys(timers.optional).forEach(key => {
            status.optional[key] = timers.optional[key] !== null;
        });

        return status;
    }

    // ========================================
    // 导出接口
    // ========================================
    window.TimerManager = {
        // 启动
        startEssential,
        startAll,
        startStreamStatusCheck,
        startSystemMonitor,
        startDiagnostic,
        startPerformanceMonitor,

        // 停止
        stopAll,
        stopMonitoring,
        stopSystemMonitor,

        // 状态
        getStatus,

        // 配置
        config
    };

    // ========================================
    // 页面加载后自动启动
    // ========================================
    document.addEventListener('DOMContentLoaded', () => {
        // 延迟启动，避免页面加载时的性能开销
        setTimeout(() => {
            startEssential();
            console.log('[定时器管理] 核心定时器已自动启动');
        }, 1000);
    });

    console.log('[定时器管理] 定时器管理器已加载');

})();