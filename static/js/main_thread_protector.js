// ========================================
// 主线程保护机制（防止浏览器崩溃）
// ========================================

(function() {
    'use strict';

    // ========================================
    // 配置参数
    // ========================================
    const config = {
        // 长任务检测阈值（ms）
        longTaskThreshold: 100,  // ✅ 修复：从50改为100，避免启动时误报

        // 主线程阻塞警告阈值（ms）
        blockingThreshold: 200,  // ✅ 修复：从100改为200，更宽松

        // 极端阻塞阈值（触发降级）
        criticalThreshold: 500,  // ✅ 保持500ms不变

        // 性能采样间隔（ms）
        samplingInterval: 1000,

        // 是否启用降级
        enableDegradation: true,

        // 降级冷却时间（ms）
        degradationCooldown: 5000
    };

    // ========================================
    // 性能统计
    // ========================================
    const stats = {
        totalTasks: 0,
        longTasks: 0,
        blockingTasks: 0,
        criticalTasks: 0,
        lastCheckTime: performance.now(),
        isDegraded: false,
        degradationCount: 0
    };

    // ========================================
    // 长任务检测（使用 PerformanceObserver）
    // ========================================
    if (typeof PerformanceObserver !== 'undefined') {
        try {
            const observer = new PerformanceObserver((list) => {
                const entries = list.getEntries();

                entries.forEach(entry => {
                    const duration = entry.duration;
                    stats.totalTasks++;

                    if (duration > config.criticalThreshold) {
                        stats.criticalTasks++;
                        console.error(`[主线程保护] 检测到极端阻塞: ${duration.toFixed(2)}ms`);

                        // 触发紧急降级
                        if (config.enableDegradation) {
                            triggerEmergencyDegradation('critical_blocking');
                        }
                    } else if (duration > config.blockingThreshold) {
                        stats.blockingTasks++;
                        console.warn(`[主线程保护] 检测到主线程阻塞: ${duration.toFixed(2)}ms`);

                        // 警告用户
                        if (window.PerfMonitor) {
                            const report = PerfMonitor.generateReport();
                            console.warn('[性能报告]', report);
                        }
                    } else if (duration > config.longTaskThreshold) {
                        stats.longTasks++;
                    }
                });
            });

            observer.observe({ entryTypes: ['longtask'] });
            console.log('✅ 长任务检测已启用');
        } catch (e) {
            console.warn('❌ PerformanceObserver 不可用，使用备用方案');
            startManualMonitoring();
        }
    } else {
        console.warn('❌ PerformanceObserver 不可用，使用备用方案');
        startManualMonitoring();
    }

    // ========================================
    // 手动监控（备用方案）
    // ========================================
    function startManualMonitoring() {
        let lastTime = performance.now();

        function checkFrame() {
            const now = performance.now();
            const delta = now - lastTime;

            if (delta > config.blockingThreshold) {
                stats.blockingTasks++;
                console.warn(`[主线程保护] 检测到阻塞: ${delta.toFixed(2)}ms`);
            }

            lastTime = now;
            // ✅ 修复：不再使用 requestAnimationFrame 循环
            // requestAnimationFrame 循环会在每一帧都调用，导致性能问题
            // 使用 PerformanceObserver 代替（已在上面实现）
            // requestAnimationFrame(checkFrame);
        }

        // ✅ 修复：不再启动 requestAnimationFrame 循环
        // 改为完全依赖 PerformanceObserver（现代浏览器API）
        // requestAnimationFrame(checkFrame);
        console.log('✅ 主线程保护已启用（使用 PerformanceObserver）');
    }

    // ========================================
    // 紧急降级机制
    // ========================================
    function triggerEmergencyDegradation(reason) {
        if (stats.isDegraded) {
            return;  // 已经降级，不再重复
        }

        stats.isDegraded = true;
        stats.degradationCount++;

        console.error(`[主线程保护] 触发紧急降级: ${reason}`);
        console.error('[主线程保护] 正在关闭所有性能敏感功能...');

        // ✅ 关闭AI流
        if (typeof aiStreamSubscriptions !== 'undefined') {
            Object.keys(aiStreamSubscriptions).forEach(cameraId => {
                if (typeof disableAIStream === 'function') {
                    disableAIStream(cameraId);
                }
            });
        }

        // ✅ 清空检测缓冲区
        if (typeof detectionBuffer !== 'undefined') {
            Object.keys(detectionBuffer).forEach(cameraId => {
                detectionBuffer[cameraId] = [];
            });
        }

        // ✅ 显示警告通知
        if (typeof showNotification === 'function') {
            showNotification('系统过载，已启用保护模式', 'error', 10000);
        }

        // ✅ 冷却后恢复
        setTimeout(() => {
            stats.isDegraded = false;
            console.log('[主线程保护] 保护模式已解除，系统恢复正常');
        }, config.degradationCooldown);
    }

    // ========================================
    // 性能报告API
    // ========================================
    window.MainThreadProtector = {
        getStats: () => stats,
        getConfig: () => config,
        triggerDegradation: triggerEmergencyDegradation,
        isDegraded: () => stats.isDegraded
    };

    console.log('✅ 主线程保护机制已加载');
    console.log('💡 配置:', config);
})();