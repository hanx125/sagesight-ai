/**
 * 视频帧环形缓存和时间戳匹配系统
 * 
 * 功能：
 * 1. 缓存最近10秒的视频帧（降低分辨率）
 * 2. 定期捕获视频帧（10-15 fps）
 * 3. 根据时间戳和帧序号匹配缓存帧
 * 4. 在Canvas上绘制检测框
 */

// ========================================
// 环形缓冲区类
// ========================================

class RingBuffer {
    /**
     * 环形缓冲区（用于缓存视频帧）
     * @param {number} capacity - 缓冲区容量（帧数）
     */
    constructor(capacity) {
        this.capacity = capacity;
        this.buffer = [];
        this.head = 0;  // 写入位置
        this.size = 0;  // 当前元素数量
    }

    /**
     * 添加元素到缓冲区
     * @param {any} item - 要添加的元素
     */
    push(item) {
        if (this.size < this.capacity) {
            // 缓冲区未满，添加到末尾
            this.buffer.push(item);
            this.size++;
        } else {
            // 缓冲区已满，覆盖最旧的元素
            this.buffer[this.head] = item;
            this.head = (this.head + 1) % this.capacity;
        }
    }

    /**
     * 查找匹配的元素
     * @param {function} predicate - 匹配条件函数
     * @returns {any|null} 匹配的元素，如果未找到则返回null
     */
    find(predicate) {
        for (let i = 0; i < this.size; i++) {
            const index = (this.head + i) % this.size;
            if (predicate(this.buffer[index])) {
                return this.buffer[index];
            }
        }
        return null;
    }

    /**
     * 获取所有元素
     * @returns {array} 所有元素数组
     */
    toArray() {
        const result = [];
        for (let i = 0; i < this.size; i++) {
            const index = (this.head + i) % this.size;
            result.push(this.buffer[index]);
        }
        return result;
    }

    /**
     * 清空缓冲区
     */
    clear() {
        this.buffer = [];
        this.head = 0;
        this.size = 0;
    }

    /**
     * 获取当前元素数量
     * @returns {number} 元素数量
     */
    get length() {
        return this.size;
    }
}

// ========================================
// 视频帧缓存管理器
// ========================================

class FrameCacheManager {
    /**
     * 视频帧缓存管理器
     * @param {string} cameraId - 摄像机ID
     * @param {object} options - 配置选项
     */
    constructor(cameraId, options = {}) {
        this.cameraId = cameraId;
        
        // 配置参数
        this.cacheDuration = options.cacheDuration || 10;  // 缓存时长（秒）
        this.cacheFps = options.cacheFps || 15;            // 缓存帧率（fps）
        this.cacheWidth = options.cacheWidth || 640;       // 缓存宽度（与检测分辨率一致）
        this.cacheHeight = options.cacheHeight || 640;     // 缓存高度（与检测分辨率一致）
        this.maxTimeDiff = options.maxTimeDiff || 1000;    // 最大时间戳差异（毫秒）
        
        // 计算缓冲区容量
        this.bufferCapacity = this.cacheDuration * this.cacheFps;
        
        // 创建环形缓冲区
        this.frameCache = new RingBuffer(this.bufferCapacity);
        
        // 帧计数器
        this.frameCount = 0;
        
        // 捕获定时器
        this.captureTimer = null;
        
        // 视频元素
        this.videoElement = null;
        
        // Canvas元素（用于绘制检测框）
        this.overlayCanvas = null;
        this.overlayCtx = null;
        
        // 性能监控
        this.stats = {
            totalCaptured: 0,
            totalMatched: 0,
            totalMissed: 0,
            avgMatchTime: 0
        };

        // ✅ 新增：播放控制
        this.isPlaying = false;           // 是否正在播放
        this.playbackDelay = 3000;        // 延迟播放时间（毫秒）
        this.minBufferForPlayback = 45;   // 最小缓存帧数（3秒 × 15fps）
        this.playbackTimer = null;        // 播放定时器
        this.playbackFps = 15;            // 播放帧率
        this.currentFrameIndex = 0;       // 当前播放帧索引

        console.log(`[FrameCache-${cameraId}] 初始化: 容量=${this.bufferCapacity}帧 (${this.cacheDuration}秒 × ${this.cacheFps}fps)`);
        console.log(`[FrameCache-${cameraId}] 延迟播放: ${this.playbackDelay}ms, 最小缓存=${this.minBufferForPlayback}帧`);
    }
    
    /**
     * 启动帧捕获
     * @param {HTMLVideoElement} videoElement - 视频元素
     * @param {HTMLCanvasElement} overlayCanvas - 叠加层Canvas
     */
    startCapture(videoElement, overlayCanvas) {
        this.videoElement = videoElement;  // ✅ 保存视频元素引用
        this.overlayCanvas = overlayCanvas;
        
        if (overlayCanvas) {
            this.overlayCtx = overlayCanvas.getContext('2d');
            // 设置Canvas尺寸与视频一致
            overlayCanvas.width = videoElement.videoWidth || 854;
            overlayCanvas.height = videoElement.videoHeight || 480;
        }
        
        // 定期捕获帧
        const captureInterval = 1000 / this.cacheFps;  // 毫秒
        
        this.captureTimer = setInterval(() => {
            this.captureFrame();
        }, captureInterval);
        
        console.log(`[FrameCache-${this.cameraId}] 启动帧捕获: 间隔=${captureInterval}ms, 帧率=${this.cacheFps}fps`);
    }
    
    /**
     * 停止帧捕获
     */
    stopCapture() {
        if (this.captureTimer) {
            clearInterval(this.captureTimer);
            this.captureTimer = null;
        }
        
        // 清空缓存
        this.frameCache.clear();
        
        // 清空叠加层
        if (this.overlayCtx) {
            this.overlayCtx.clearRect(0, 0, this.overlayCanvas.width, this.overlayCanvas.height);
        }
        
        console.log(`[FrameCache-${this.cameraId}] 停止帧捕获，清空缓存`);
    }
    
    /**
     * 捕获视频帧
     */
    captureFrame() {
        if (!this.videoElement || this.videoElement.paused || this.videoElement.ended) {
            return;
        }
        
        try {
            // 创建Canvas并降低分辨率
            const canvas = document.createElement('canvas');
            canvas.width = this.cacheWidth;
            canvas.height = this.cacheHeight;
            
            const ctx = canvas.getContext('2d');
            ctx.drawImage(this.videoElement, 0, 0, this.cacheWidth, this.cacheHeight);
            
            // 记录时间戳和帧序号
            const timestamp = Date.now();  // 毫秒时间戳
            const frameSeq = this.frameCount++;
            
            // 添加到缓存
            this.frameCache.push({
                canvas: canvas,
                timestamp: timestamp,
                frameSeq: frameSeq,
                cameraId: this.cameraId
            });
            
            // 更新统计
            this.stats.totalCaptured++;
            
            // 每100帧输出一次状态
            if (this.frameCount % 100 === 0) {
                console.log(`[FrameCache-${this.cameraId}] 已捕获${this.frameCount}帧, 缓存大小=${this.frameCache.length}/${this.bufferCapacity}`);
            }
            
        } catch (error) {
            console.error(`[FrameCache-${this.cameraId}] 捕获帧失败:`, error);
        }
    }
    
    /**
     * 匹配检测数据对应的缓存帧
     * @param {object} detectionData - 检测数据（包含timestamp和frameSeq）
     * @returns {object|null} 匹配的缓存帧，如果未找到则返回null
     */
    matchFrame(detectionData) {
        const { timestamp, frameSeq } = detectionData;
        
        const startTime = performance.now();
        
        // 方案1：先尝试精确帧序号匹配
        let matchedFrame = this.frameCache.find(frame => frame.frameSeq === frameSeq);
        
        if (matchedFrame) {
            const matchTime = performance.now() - startTime;
            this.stats.totalMatched++;
            this.stats.avgMatchTime = (this.stats.avgMatchTime * 0.9) + (matchTime * 0.1);
            
            console.log(`[FrameCache-${this.cameraId}] 帧序号匹配成功: frameSeq=${frameSeq}, 耗时=${matchTime.toFixed(2)}ms`);
            return matchedFrame;
        }
        
        // 方案2：时间戳容差匹配（±maxTimeDiff）
        matchedFrame = this.frameCache.find(frame => {
            const timeDiff = Math.abs(frame.timestamp - timestamp);
            return timeDiff <= this.maxTimeDiff;
        });
        
        if (matchedFrame) {
            const timeDiff = Math.abs(matchedFrame.timestamp - timestamp);
            const matchTime = performance.now() - startTime;
            this.stats.totalMatched++;
            this.stats.avgMatchTime = (this.stats.avgMatchTime * 0.9) + (matchTime * 0.1);
            
            console.log(`[FrameCache-${this.cameraId}] 时间戳匹配成功: 时间差=${timeDiff}ms, 耗时=${matchTime.toFixed(2)}ms`);
            return matchedFrame;
        }
        
        // 未匹配到
        this.stats.totalMissed++;
        console.warn(`[FrameCache-${this.cameraId}] 帧匹配失败: timestamp=${timestamp}, frameSeq=${frameSeq}, 缓存大小=${this.frameCache.length}`);
        
        return null;
    }
    
    /**
     * 绘制检测框到叠加层
     * @param {object} detectionData - 检测数据
     */
    drawDetections(detectionData) {
        if (!this.overlayCtx || !this.overlayCanvas) {
            console.warn(`[FrameCache-${this.cameraId}] 叠加层Canvas未初始化`);
            return;
        }
        
        const { detections, tracks, trajectories } = detectionData;
        
        // 清空叠加层
        this.overlayCtx.clearRect(0, 0, this.overlayCanvas.width, this.overlayCanvas.height);
        
        // ✅ 优化：后端直接推送640×640坐标，前端直接使用（无需映射）
        console.log(`[FrameCache-${this.cameraId}] 直接使用640×640坐标绘制（无需映射）`);
        
        // 绘制检测框
        if (detections && detections.length > 0) {
            detections.forEach(det => {
                const { bbox, class_name, confidence } = det;
                const [x1, y1, x2, y2] = bbox;
                
                // ✅ 优化：直接使用640×640坐标
                this.overlayCtx.strokeStyle = '#00FF00';
                this.overlayCtx.lineWidth = 2;
                this.overlayCtx.strokeRect(x1, y1, x2 - x1, y2 - y1);
                
                // 绘制标签
                this.overlayCtx.fillStyle = '#00FF00';
                this.overlayCtx.font = '14px Arial';
                this.overlayCtx.fillText(`${class_name}: ${confidence.toFixed(2)}`, x1, y1 - 5);
            });
        }
        
        // 绘制跟踪框
        if (tracks && tracks.length > 0) {
            tracks.forEach(track => {
                const { bbox, track_id, confidence, class_id } = track;
                const [x1, y1, x2, y2] = bbox;
                
                // ✅ 优化：直接使用640×640坐标
                this.overlayCtx.strokeStyle = '#0066FF';
                this.overlayCtx.lineWidth = 2;
                this.overlayCtx.strokeRect(x1, y1, x2 - x1, y2 - y1);
                
                // 绘制ID
                this.overlayCtx.fillStyle = '#0066FF';
                this.overlayCtx.font = '14px Arial';
                this.overlayCtx.fillText(`ID: ${track_id}`, x1, y1 - 5);
            });
        }
        
        // 绘制轨迹（可选）
        if (trajectories && Object.keys(trajectories).length > 0) {
            Object.values(trajectories).forEach(trajectory => {
                if (trajectory.points && trajectory.points.length > 1) {
                    this.overlayCtx.strokeStyle = '#FF6600';
                    this.overlayCtx.lineWidth = 1;
                    this.overlayCtx.beginPath();
                    
                    trajectory.points.forEach((point, index) => {
                        const x = point.center_x;
                        const y = point.center_y;
                        
                        if (index === 0) {
                            this.overlayCtx.moveTo(x, y);
                        } else {
                            this.overlayCtx.lineTo(x, y);
                        }
                    });
                    
                    this.overlayCtx.stroke();
                }
            });
        }
    }
    
    /**
     * 获取性能统计
     * @returns {object} 性能统计数据
     */
    getStats() {
        return {
            ...this.stats,
            cacheSize: this.frameCache.length,
            cacheCapacity: this.bufferCapacity,
            matchRate: this.stats.totalMatched / (this.stats.totalMatched + this.stats.totalMissed) || 0
        };
    }
}

// 导出类（供其他脚本使用）
window.RingBuffer = RingBuffer;
window.FrameCacheManager = FrameCacheManager;