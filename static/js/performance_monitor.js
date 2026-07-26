// ========================================
// 前端性能诊断工具
// ========================================

(function() {
    'use strict';

    // ========================================
    // 性能监控数据
    // ========================================
    const perfMonitor = {
        // WebSocket消息统计
        websocket: {
            detection_frame: { count: 0, lastTime: 0, avgInterval: 0, intervals: [] },
            ai_stream_frame: { count: 0, lastTime: 0, avgInterval: 0, intervals: [], frameSizes: [] },
            new_event: { count: 0, lastTime: 0, avgInterval: 0, intervals: [] },
            total: 0
        },

        // Canvas渲染统计
        canvas: {
            renders: 0,
            totalTime: 0,
            avgTime: 0,
            lastRenderTime: 0,
            fps: 0,
            frameCount: 0
        },

        // 通知统计
        notification: {
            count: 0,
            lastTime: 0,
            avgInterval: 0,
            intervals: []
        },

        // 内存统计
        memory: {
            usedJSHeapSize: 0,
            totalJSHeapSize: 0,
            jsHeapSizeLimit: 0
        },

        // 性能瓶颈检测
        bottlenecks: [],

        // 开始时间
        startTime: Date.now(),

        // 是否启用
        enabled: true
    };

    // ========================================
    // WebSocket消息监控
    // ========================================
    function monitorWebSocket(eventName, data) {
        if (!perfMonitor.enabled) return;

        const now = Date.now();
        const stats = perfMonitor.websocket[eventName];

        if (stats) {
            stats.count++;
            perfMonitor.websocket.total++;

            // 记录间隔
            if (stats.lastTime > 0) {
                const interval = now - stats.lastTime;
                stats.intervals.push(interval);

                // 保留最近100个间隔
                if (stats.intervals.length > 100) {
                    stats.intervals.shift();
                }

                // 计算平均间隔
                stats.avgInterval = stats.intervals.reduce((a, b) => a + b, 0) / stats.intervals.length;
            }

            stats.lastTime = now;

            // AI流帧大小统计
            if (eventName === 'ai_stream_frame' && data.frame) {
                stats.frameSizes.push(data.frame.length);
                if (stats.frameSizes.length > 100) {
                    stats.frameSizes.shift();
                }
            }
        }

        // 检测瓶颈
        detectBottlenecks();
    }

    // ========================================
    // Canvas渲染监控
    // ========================================
    function monitorCanvasRender(renderTime) {
        if (!perfMonitor.enabled) return;

        const canvas = perfMonitor.canvas;
        canvas.renders++;
        canvas.totalTime += renderTime;
        canvas.avgTime = canvas.totalTime / canvas.renders;

        // 计算FPS
        const now = Date.now();
        if (canvas.lastRenderTime > 0) {
            const frameInterval = now - canvas.lastRenderTime;
            if (frameInterval > 0) {
                canvas.fps = 1000 / frameInterval;
            }
        }
        canvas.lastRenderTime = now;
        canvas.frameCount++;

        // 检测瓶颈
        detectBottlenecks();
    }

    // ========================================
    // 通知监控
    // ========================================
    function monitorNotification() {
        if (!perfMonitor.enabled) return;

        const notification = perfMonitor.notification;
        const now = Date.now();

        notification.count++;

        if (notification.lastTime > 0) {
            const interval = now - notification.lastTime;
            notification.intervals.push(interval);

            if (notification.intervals.length > 100) {
                notification.intervals.shift();
            }

            notification.avgInterval = notification.intervals.reduce((a, b) => a + b, 0) / notification.intervals.length;
        }

        notification.lastTime = now;

        // 检测瓶颈
        detectBottlenecks();
    }

    // ========================================
    // 性能瓶颈检测
    // ========================================
    function detectBottlenecks() {
        const bottlenecks = perfMonitor.bottlenecks;
        bottlenecks.length = 0;

        const ws = perfMonitor.websocket;

        // 1. WebSocket消息速率瓶颈
        if (ws.ai_stream_frame.avgInterval > 0 && ws.ai_stream_frame.avgInterval < 50) {
            bottlenecks.push({
                type: 'websocket_rate',
                severity: 'high',
                message: `AI流消息速率过高: ${ws.ai_stream_frame.avgInterval.toFixed(1)}ms间隔`,
                recommendation: '增加 ai_stream_interval 配置值'
            });
        }

        // 2. 通知频率瓶颈
        if (perfMonitor.notification.avgInterval > 0 && perfMonitor.notification.avgInterval < 100) {
            bottlenecks.push({
                type: 'notification_rate',
                severity: 'high',
                message: `通知频率过高: ${perfMonitor.notification.avgInterval.toFixed(1)}ms间隔`,
                recommendation: '检查事件触发源，降低通知频率'
            });
        }

        // 3. Canvas渲染瓶颈
        if (perfMonitor.canvas.avgTime > 50) {
            bottlenecks.push({
                type: 'canvas_render',
                severity: 'high',
                message: `Canvas渲染耗时过长: ${perfMonitor.canvas.avgTime.toFixed(1)}ms`,
                recommendation: '降低渲染频率或分辨率'
            });
        }

        // 4. 帧大小瓶颈
        if (ws.ai_stream_frame.frameSizes.length > 0) {
            const avgSize = ws.ai_stream_frame.frameSizes.reduce((a, b) => a + b, 0) / ws.ai_stream_frame.frameSizes.length;
            const avgSizeKB = (avgSize / 1024).toFixed(1);

            if (avgSize > 200 * 1024) {  // > 200KB
                bottlenecks.push({
                    type: 'frame_size',
                    severity: 'medium',
                    message: `AI流帧过大: ${avgSizeKB}KB`,
                    recommendation: '降低JPEG质量或分辨率'
                });
            }
        }

        // 5. 内存瓶颈
        if (performance.memory) {
            const mem = performance.memory;
            perfMonitor.memory.usedJSHeapSize = mem.usedJSHeapSize;
            perfMonitor.memory.totalJSHeapSize = mem.totalJSHeapSize;
            perfMonitor.memory.jsHeapSizeLimit = mem.jsHeapSizeLimit;

            const usedMB = (mem.usedJSHeapSize / 1024 / 1024).toFixed(1);
            const limitMB = (mem.jsHeapSizeLimit / 1024 / 1024).toFixed(1);

            if (mem.usedJSHeapSize > mem.jsHeapSizeLimit * 0.8) {
                bottlenecks.push({
                    type: 'memory',
                    severity: 'high',
                    message: `内存使用过高: ${usedMB}MB / ${limitMB}MB`,
                    recommendation: '检查内存泄漏，减少缓存'
                });
            }
        }
    }

    // ========================================
    // 性能报告生成
    // ========================================
    function generateReport() {
        const report = {
            timestamp: new Date().toISOString(),
            uptime: ((Date.now() - perfMonitor.startTime) / 1000).toFixed(1) + 's',

            websocket: {
                detection_frame: {
                    count: perfMonitor.websocket.detection_frame.count,
                    avgInterval: perfMonitor.websocket.detection_frame.avgInterval.toFixed(1) + 'ms'
                },
                ai_stream_frame: {
                    count: perfMonitor.websocket.ai_stream_frame.count,
                    avgInterval: perfMonitor.websocket.ai_stream_frame.avgInterval.toFixed(1) + 'ms',
                    avgFrameSize: perfMonitor.websocket.ai_stream_frame.frameSizes.length > 0
                        ? (perfMonitor.websocket.ai_stream_frame.frameSizes.reduce((a, b) => a + b, 0) / perfMonitor.websocket.ai_stream_frame.frameSizes.length / 1024).toFixed(1) + 'KB'
                        : 'N/A'
                },
                new_event: {
                    count: perfMonitor.websocket.new_event.count,
                    avgInterval: perfMonitor.websocket.new_event.avgInterval.toFixed(1) + 'ms'
                },
                total: perfMonitor.websocket.total
            },

            canvas: {
                renders: perfMonitor.canvas.renders,
                avgTime: perfMonitor.canvas.avgTime.toFixed(1) + 'ms',
                fps: perfMonitor.canvas.fps.toFixed(1)
            },

            notification: {
                count: perfMonitor.notification.count,
                avgInterval: perfMonitor.notification.avgInterval.toFixed(1) + 'ms'
            },

            memory: perfMonitor.memory.usedJSHeapSize > 0
                ? {
                    used: (perfMonitor.memory.usedJSHeapSize / 1024 / 1024).toFixed(1) + 'MB',
                    total: (perfMonitor.memory.totalJSHeapSize / 1024 / 1024).toFixed(1) + 'MB',
                    limit: (perfMonitor.memory.jsHeapSizeLimit / 1024 / 1024).toFixed(1) + 'MB'
                }
                : 'N/A',

            bottlenecks: perfMonitor.bottlenecks
        };

        return report;
    }

    // ========================================
    // 创建性能监控面板
    // ========================================
    function createMonitorPanel() {
        const panel = document.createElement('div');
        panel.id = 'performance-monitor-panel';
        panel.style.cssText = `
            position: fixed;
            top: 10px;
            right: 10px;
            width: 400px;
            max-height: 600px;
            background: rgba(0, 0, 0, 0.9);
            color: #00ff00;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            padding: 15px;
            border-radius: 8px;
            z-index: 10000;
            overflow-y: auto;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
        `;

        const titleBar = document.createElement('div');
        titleBar.style.cssText = `
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid #00ff00;
        `;
        titleBar.innerHTML = `
            <span style="font-size: 14px; font-weight: bold;">🔍 性能诊断工具</span>
            <button id="close-monitor" style="background: #ff0000; color: white; border: none; padding: 5px 10px; cursor: pointer; border-radius: 3px;">关闭</button>
        `;

        const content = document.createElement('div');
        content.id = 'monitor-content';

        panel.appendChild(titleBar);
        panel.appendChild(content);
        document.body.appendChild(panel);

        // 关闭按钮事件
        document.getElementById('close-monitor').addEventListener('click', () => {
            panel.style.display = 'none';
        });

        return content;
    }

    // ========================================
    // 更新监控面板
    // ========================================
    function updateMonitorPanel() {
        const content = document.getElementById('monitor-content');
        if (!content) return;

        const report = generateReport();

        let html = `
            <div style="margin-bottom: 10px;">
                <strong>运行时间:</strong> ${report.uptime}
            </div>

            <div style="margin-bottom: 15px; padding: 10px; background: rgba(0, 255, 0, 0.1); border-radius: 4px;">
                <div style="font-weight: bold; margin-bottom: 8px;">📊 WebSocket消息统计</div>
                <div style="margin-left: 15px; line-height: 1.6;">
                    <div>✅ detection_frame: ${report.websocket.detection_frame.count} 条 (${report.websocket.detection_frame.avgInterval})</div>
                    <div>✅ ai_stream_frame: ${report.websocket.ai_stream_frame.count} 条 (${report.websocket.ai_stream_frame.avgInterval}) - ${report.websocket.ai_stream_frame.avgFrameSize}</div>
                    <div>✅ new_event: ${report.websocket.new_event.count} 条 (${report.websocket.new_event.avgInterval})</div>
                    <div style="margin-top: 5px; color: #ffff00;">总计: ${report.websocket.total} 条</div>
                </div>
            </div>

            <div style="margin-bottom: 15px; padding: 10px; background: rgba(0, 255, 0, 0.1); border-radius: 4px;">
                <div style="font-weight: bold; margin-bottom: 8px;">🎨 Canvas渲染统计</div>
                <div style="margin-left: 15px; line-height: 1.6;">
                    <div>渲染次数: ${report.canvas.renders}</div>
                    <div>平均耗时: ${report.canvas.avgTime}</div>
                    <div>当前FPS: ${report.canvas.fps}</div>
                </div>
            </div>

            <div style="margin-bottom: 15px; padding: 10px; background: rgba(0, 255, 0, 0.1); border-radius: 4px;">
                <div style="font-weight: bold; margin-bottom: 8px;">🔔 通知统计</div>
                <div style="margin-left: 15px; line-height: 1.6;">
                    <div>通知次数: ${report.notification.count}</div>
                    <div>平均间隔: ${report.notification.avgInterval}</div>
                </div>
            </div>

            <div style="margin-bottom: 15px; padding: 10px; background: rgba(0, 255, 0, 0.1); border-radius: 4px;">
                <div style="font-weight: bold; margin-bottom: 8px;">💾 内存使用</div>
                <div style="margin-left: 15px; line-height: 1.6;">
                    <div>${typeof report.memory === 'string' ? report.memory : `已用: ${report.memory.used} / 限制: ${report.memory.limit}`}</div>
                </div>
            </div>

            ${report.bottlenecks.length > 0 ? `
            <div style="margin-bottom: 15px; padding: 10px; background: rgba(255, 0, 0, 0.2); border-radius: 4px; border: 1px solid #ff0000;">
                <div style="font-weight: bold; margin-bottom: 8px; color: #ff0000;">⚠️ 性能瓶颈检测 (${report.bottlenecks.length}个)</div>
                <div style="margin-left: 15px; line-height: 1.8;">
                    ${report.bottlenecks.map(b => `
                        <div style="margin-bottom: 8px;">
                            <div style="color: ${b.severity === 'high' ? '#ff0000' : '#ffff00'};">• ${b.message}</div>
                            <div style="color: #00ffff; font-size: 11px;">  建议: ${b.recommendation}</div>
                        </div>
                    `).join('')}
                </div>
            </div>
            ` : ''}
        `;

        content.innerHTML = html;
    }

    // ========================================
    // 导出监控函数
    // ========================================
    window.PerfMonitor = {
        monitorWebSocket,
        monitorCanvasRender,
        monitorNotification,
        generateReport,
        createMonitorPanel,
        updateMonitorPanel,

        // 控制方法
        enable: () => { perfMonitor.enabled = true; },
        disable: () => { perfMonitor.enabled = false; },

        // 获取数据
        getData: () => perfMonitor
    };

    console.log('✅ 性能诊断工具已加载');
    console.log('💡 使用方法:');
    console.log('   - 按 Ctrl+Shift+P 打开监控面板');
    console.log('   - PerfMonitor.createMonitorPanel() : 打开监控面板');
    console.log('   - PerfMonitor.updateMonitorPanel() : 更新面板数据');
    console.log('   - PerfMonitor.generateReport() : 生成性能报告');

    // ✅ 添加快捷键：Ctrl+Shift+P 打开监控面板
    document.addEventListener('keydown', function(e) {
        if (e.ctrlKey && e.shiftKey && e.key === 'P') {
            const panel = document.getElementById('performance-monitor-panel');
            if (panel) {
                if (panel.style.display === 'none') {
                    panel.style.display = 'block';
                } else {
                    panel.style.display = 'none';
                }
            } else {
                createMonitorPanel();
            }

            // 更新面板
            if (panel && panel.style.display !== 'none') {
                updateMonitorPanel();
            }
        }
    });

    // ✅ 修复：移除性能监控定时器，改用 TimerManager 统一管理
    // 性能监控和心跳检测现在由 timer_manager.js 统一管理
    // let _monitorUpdateInterval = setInterval(() => {
    //     const panel = document.getElementById('performance-monitor-panel');
    //     if (panel && panel.style.display !== 'none') {
    //         // ✅ 使用 requestAnimationFrame 避免阻塞
    //         requestAnimationFrame(() => {
    //             updateMonitorPanel();
    //         });
    //     }
    // }, 2000);

    // let _heartbeatInterval = setInterval(() => {
    //     const now = Date.now();
    //     const blocked = now - _lastHeartbeat;
    //
    //     // 如果主线程阻塞超过5秒，触发熔断
    //     if (blocked > 5000) {
    //         console.error('🔥 紧急熔断：主线程阻塞超过5秒');
    //         console.error('🔥 正在停止所有监控...');
    //
    //         // 停止所有定时器
    //         clearInterval(_heartbeatInterval);
    //         clearInterval(_monitorUpdateInterval);
    //
    //         // 显示警告
    //         const warning = document.createElement('div');
    //         warning.style.cssText = `
    //             position: fixed;
    //             top: 50%;
    //             left: 50%;
    //             transform: translate(-50%, -50%);
    //             background: rgba(255, 0, 0, 0.9);
    //             color: white;
    //             padding: 20px;
    //             border-radius: 10px;
    //             z-index: 999999;
    //             font-size: 16px;
    //             font-family: Arial, sans-serif;
    //             text-align: center;
    //         `;
    //         warning.innerHTML = `
    //             <div style="font-weight: bold; margin-bottom: 10px;">🔥 系统过载保护</div>
    //             <div>主线程阻塞超过5秒</div>
    //             <div style="margin-top: 10px; font-size: 14px;">已停止所有监控</div>
    //             <button onclick="this.parentElement.remove()" style="margin-top: 15px; padding: 5px 15px; cursor: pointer;">关闭</button>
    //         `;
    //         document.body.appendChild(warning);
    //
    //         // 禁用性能监控
    //         perfMonitor.enabled = false;
    //     }
    //
    //     _lastHeartbeat = now;
    // }, 1000);  // 每秒检测一次
})();