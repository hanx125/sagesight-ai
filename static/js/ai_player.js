/**
 * AI播放器：延迟播放缓存帧
 * 
 * 功能：
 * 1. 后台捕获实时流到缓存（640×640）
 * 2. 延迟播放（等待缓存累积3秒）
 * 3. 显示缓存帧 + 检测框
 */

class AIPlayer {
    constructor(cameraId, options = {}) {
        this.cameraId = cameraId;

        // 缓存参数
        this.cacheDuration = options.cacheDuration || 10;  // 缓存时长（秒）
        this.cacheFps = options.cacheFps || 25;            // ✅ 修复：默认25fps，与后端解码帧率一致
        this.cacheWidth = options.cacheWidth || 640;       // 缓存宽度（与检测分辨率一致）
        this.cacheHeight = options.cacheHeight || 640;     // 缓存高度（与检测分辨率一致）
        this.playbackDelay = options.playbackDelay || 3000; // 延迟播放时间（毫秒）
        this.playbackFps = options.playbackFps || 15;       // 播放帧率（可以低于捕获帧率）

        // 计算缓冲区容量
        this.bufferCapacity = this.cacheDuration * this.cacheFps;
        this.minBufferForPlayback = Math.floor(this.playbackDelay / (1000 / this.cacheFps));  // 45帧（3秒）

        // 创建环形缓冲区
        this.frameBuffer = [];  // 帧缓存
        this.detectionBuffer = {};  // 检测数据缓存（✅ 修改：使用timestamp作为键）

        // ✅ 新增：时间戳映射（timestamp -> 帧数据）
        this.frameTimestampMap = {};  // {timestamp: frameData}

        // ✅ 新增：帧序号映射（frame_seq -> 帧数据）
        this.frameSeqMap = {};  // {frame_seq: frameData}

        // 定时器
        this.captureTimer = null;  // 捕获定时器
        this.playbackTimer = null;  // 播放定时器

        // 播放状态
        this.isPlaying = false;
        this.currentPlaybackTime = 0;  // 当前播放时间（毫秒时间戳）

        // ✅ 新增：帧序号计数器
        this.frameSeqCounter = 0;  // 前端帧序号（从0开始）
        this.startCaptureTime = 0;  // 开始捕获的时间（用于计算帧序号）
        this.frameSeqOffset = 0;  // ✅ 新增：帧序号偏移量（用于同步前后端帧序号）
        this.isFrameSeqSynchronized = false;  // ✅ 新增：是否已同步帧序号

        // ✅ 关键修复：使用后端返回的初始帧序号
        if (options.initialFrameSeq !== undefined && options.initialFrameSeq > 0) {
            this.frameSeqOffset = options.initialFrameSeq;
            this.isFrameSeqSynchronized = true;
            console.log(`[AIPlayer-${cameraId}] 使用初始帧序号: ${options.initialFrameSeq}`);
        }

        // Canvas元素
        this.videoElement = null;  // 视频元素（实时流，隐藏）
        this.displayCanvas = null;  // 显示Canvas（用户看到的）
        this.displayCtx = null;

        console.log(`[AIPlayer-${cameraId}] 初始化: 缓存容量=${this.bufferCapacity}帧, 延迟播放=${this.playbackDelay}ms, 初始帧序号=${this.frameSeqOffset}`);
    }

    /**
     * 启动AI播放器
     * @param {HTMLVideoElement} videoElement - 视频元素（实时流）
     * @param {HTMLCanvasElement} displayCanvas - 显示Canvas
     */
    start(videoElement, displayCanvas) {
        this.videoElement = videoElement;
        this.displayCanvas = displayCanvas;

        if (displayCanvas) {
            this.displayCtx = displayCanvas.getContext('2d');
            displayCanvas.width = this.cacheWidth;
            displayCanvas.height = this.cacheHeight;
        }

        // 启动帧捕获
        this.startCapture();

        // 启动延迟播放检查
        this.checkAndStartPlayback();
    }

    /**
     * 启动帧捕获
     */
    startCapture() {
        if (this.captureTimer) {
            clearInterval(this.captureTimer);
        }

        // ✅ 关键：记录开始捕获时间，用于计算帧序号
        this.startCaptureTime = Date.now();
        this.frameSeqCounter = 0;

        const captureInterval = 1000 / this.cacheFps;
        this.captureTimer = setInterval(() => {
            this.captureFrame();
        }, captureInterval);

        console.log(`[AIPlayer-${this.cameraId}] 启动帧捕获: 帧率=${this.cacheFps}fps, startTime=${this.startCaptureTime}`);
    }

    /**
     * 捕获视频帧
     */
    captureFrame() {
        if (!this.videoElement || this.videoElement.paused || this.videoElement.ended) {
            return;
        }

        try {
            // 创建Canvas并降低分辨率到640×640
            const canvas = document.createElement('canvas');
            canvas.width = this.cacheWidth;
            canvas.height = this.cacheHeight;

            const ctx = canvas.getContext('2d');
            ctx.drawImage(this.videoElement, 0, 0, this.cacheWidth, this.cacheHeight);

            // ✅ 修改：使用视频元素的currentTime计算相对时间戳（毫秒）
            // currentTime是视频的播放时间（PTS），而不是系统时间
            const videoTimestamp = Math.floor(this.videoElement.currentTime * 1000);  // 秒转毫秒

            // ✅ 不再计算帧序号，播放时直接从后端缓存获取
            // 前端帧序号不再使用，完全依赖后端帧序号
            const frameSeq = null;  // 不再计算

            // 添加到缓存
            const frameData = {
                canvas: canvas,
                timestamp: videoTimestamp,  // ✅ 修改：使用视频时间戳（毫秒）
                frame_seq: frameSeq  // ✅ 新增：帧序号（用于匹配检测数据）
            };
            this.frameBuffer.push(frameData);

            // ✅ 添加到映射
            this.frameTimestampMap[videoTimestamp] = frameData;
            this.frameSeqMap[frameSeq] = frameData;

            // 限制缓存大小
            if (this.frameBuffer.length > this.bufferCapacity) {
                const removedFrame = this.frameBuffer.shift();
                // 从映射中删除
                if (removedFrame) {
                    // ✅ 释放Canvas对象（避免内存泄漏）
                    if (removedFrame.canvas) {
                        removedFrame.canvas.width = 0;
                        removedFrame.canvas.height = 0;
                        removedFrame.canvas = null;
                    }
                    // 清理映射表
                    if (removedFrame.timestamp !== undefined) {
                        delete this.frameTimestampMap[removedFrame.timestamp];
                    }
                    if (removedFrame.frame_seq !== undefined) {
                        delete this.frameSeqMap[removedFrame.frame_seq];
                    }
                }
            }

            // ✅ 定期清理映射表（防止累积）
            if (this.frameSeqCounter % 100 === 0) {
                const timestamps = Object.keys(this.frameTimestampMap);
                const frameSeqs = Object.keys(this.frameSeqMap);
                if (timestamps.length > this.bufferCapacity * 1.5) {
                    // 删除超出容量的映射
                    const toRemove = timestamps.slice(0, timestamps.length - this.bufferCapacity);
                    toRemove.forEach(ts => delete this.frameTimestampMap[ts]);
                }
                if (frameSeqs.length > this.bufferCapacity * 1.5) {
                    const toRemove = frameSeqs.slice(0, frameSeqs.length - this.bufferCapacity);
                    toRemove.forEach(seq => delete this.frameSeqMap[seq]);
                }
            }

            // 定期输出调试信息
            this.frameSeqCounter++;
            if (this.frameSeqCounter % 100 === 0) {
                console.log(`[AIPlayer-${this.cameraId}] 已捕获${this.frameSeqCounter}帧, 缓存大小=${this.frameBuffer.length}/${this.bufferCapacity}, timestamp=${videoTimestamp}ms, frame_seq=${frameSeq}`);
            }

        } catch (error) {
            console.error(`[AIPlayer-${this.cameraId}] 捕获帧失败:`, error);
        }
    }

    /**
     * 检查并启动延迟播放
     */
    checkAndStartPlayback() {
        const checkInterval = setInterval(() => {
            const bufferSize = this.frameBuffer.length;
            console.log(`[AIPlayer-${this.cameraId}] 缓存检查: ${bufferSize}/${this.minBufferForPlayback}帧`);

            if (bufferSize >= this.minBufferForPlayback) {
                console.log(`[AIPlayer-${this.cameraId}] 缓存充足，开始播放`);
                clearInterval(checkInterval);
                this.startPlayback();
            }
        }, 500);

        // 最多等待10秒
        setTimeout(() => {
            clearInterval(checkInterval);
            if (!this.isPlaying) {
                console.warn(`[AIPlayer-${this.cameraId}] 强制开始播放`);
                this.startPlayback();
            }
        }, 10000);
    }

    /**
     * 开始播放缓存帧
     */
    startPlayback() {
        if (this.isPlaying) {
            return;
        }

        this.isPlaying = true;

        const playbackInterval = 1000 / this.playbackFps;
        this.playbackTimer = setInterval(() => {
            this.playNextFrame();
        }, playbackInterval);

        console.log(`[AIPlayer-${this.cameraId}] 开始播放: 帧率=${this.playbackFps}fps`);
    }

    /**
     * 播放下一帧
     */
    playNextFrame() {
        if (!this.isPlaying || !this.displayCtx) {
            return;
        }

        const bufferSize = this.frameBuffer.length;
        if (bufferSize === 0) {
            return;
        }

        // 计算延迟帧数
        const delayFrames = Math.floor(this.playbackDelay / (1000 / this.playbackFps));
        const targetIndex = Math.max(0, bufferSize - 1 - delayFrames);

        const frameData = this.frameBuffer[targetIndex];
        if (!frameData || !frameData.canvas) {
            return;
        }

        // 绘制帧
        this.displayCtx.clearRect(0, 0, this.cacheWidth, this.cacheHeight);
        this.displayCtx.drawImage(frameData.canvas, 0, 0);

        // 双重匹配：时间戳 + 帧序号
        const timestamp = frameData.timestamp || 0;
        const frameSeq = frameData.frame_seq;

        // 减少日志输出
        if (this.frameSeqCounter % 50 === 0) {
            console.log(`[AIPlayer-${this.cameraId}] 播放: ts=${timestamp}ms, seq=${frameSeq}, cache=${bufferSize}`);
        }

        // 双重匹配检测数据
        this.drawDetectionsDualMatch(timestamp, frameSeq);
    }

    /**
     * ✅ 时间戳绝对对齐匹配
     */
    drawDetectionsDualMatch(targetTimestamp, targetFrameSeq) {
        const frameSeqs = Object.keys(this.detectionBuffer).map(Number);
        if (frameSeqs.length === 0) {
            return;
        }

        // ✅ 绝对对齐：使用视频时间戳匹配
        const videoCurrentTime = this.videoElement ? this.videoElement.currentTime : 0;
        const videoTimestamp = videoCurrentTime * 1000;  // 秒转毫秒

        // 从检测缓存中找到时间戳最接近的检测数据
        let bestMatch = null;
        let bestDiff = Infinity;

        for (const seq of frameSeqs) {
            const data = this.detectionBuffer[seq];
            if (!data || data.timestamp === undefined) continue;

            // 计算校正后的时间戳（减去偏移）
            const offset = window.timestampOffset || 0;
            const adjustedTimestamp = (data.timestamp / 1000) - offset;  // 毫秒转秒，减去偏移

            // 计算差异
            const diff = Math.abs(videoCurrentTime - adjustedTimestamp);

            if (diff < bestDiff) {
                bestDiff = diff;
                bestMatch = { seq, data, diff };
            }
        }

        // 匹配成功（差异 < 100ms）
        if (bestMatch && bestDiff < 0.1) {
            this.drawDetectionData(bestMatch.data);

            if (bestMatch.data.detections && bestMatch.data.detections.length > 0) {
                console.log(`[AIPlayer-${this.cameraId}] ✓ 绝对对齐: seq=${bestMatch.seq}, diff=${(bestDiff*1000).toFixed(0)}ms, detections=${bestMatch.data.detections.length}`);
            }
        } else if (bestMatch) {
            // 差异较大，仍然绘制（近似匹配）
            this.drawDetectionData(bestMatch.data);

            if (bestMatch.data.detections && bestMatch.data.detections.length > 0) {
                console.log(`[AIPlayer-${this.cameraId}] ~ 近似匹配: seq=${bestMatch.seq}, diff=${(bestDiff*1000).toFixed(0)}ms`);
            }
        }
    }

    /**
     * 绘制检测数据
     */
    drawDetectionData(detectionData) {
        const { detections, tracks } = detectionData;

        // 绘制检测框（直接使用640×640坐标）
        if (detections && detections.length > 0) {
            detections.forEach(det => {
                const { bbox, class_name, confidence } = det;
                const [x1, y1, x2, y2] = bbox;

                this.displayCtx.strokeStyle = '#00FF00';
                this.displayCtx.lineWidth = 2;
                this.displayCtx.strokeRect(x1, y1, x2 - x1, y2 - y1);

                this.displayCtx.fillStyle = '#00FF00';
                this.displayCtx.font = '14px Arial';
                this.displayCtx.fillText(`${class_name}: ${confidence.toFixed(2)}`, x1, y1 - 5);
            });
        }

        // 绘制跟踪框
        if (tracks && tracks.length > 0) {
            tracks.forEach(track => {
                const { bbox, track_id } = track;
                const [x1, y1, x2, y2] = bbox;

                this.displayCtx.strokeStyle = '#0066FF';
                this.displayCtx.lineWidth = 2;
                this.displayCtx.strokeRect(x1, y1, x2 - x1, y2 - y1);

                this.displayCtx.fillStyle = '#0066FF';
                this.displayCtx.font = '14px Arial';
                this.displayCtx.fillText(`ID: ${track_id}`, x1, y1 - 5);
            });
        }
    }

    /**
     * 添加检测数据到缓存
     */
    addDetectionData(data) {
        const { frame_seq, timestamp, detections, tracks } = data;

        // ✅ 修复：使用frame_seq作为键（帧序号匹配）
        if (frame_seq === undefined || frame_seq === null) {
            console.warn(`[AIPlayer-${this.cameraId}] 检测数据缺少frame_seq，跳过`);
            return;
        }

        this.detectionBuffer[frame_seq] = {
            frame_seq: frame_seq,
            timestamp: timestamp,
            detections: detections,
            tracks: tracks
        };

        console.log(`[AIPlayer-${this.cameraId}] 缓存检测数据: frame_seq=${frame_seq}, timestamp=${timestamp}ms, detections=${detections ? detections.length : 0}, 缓存大小=${Object.keys(this.detectionBuffer).length}`);

        // 限制缓存大小（保留最近15秒，约225帧）
        const frameSeqs = Object.keys(this.detectionBuffer).map(Number).sort((a, b) => b - a);
        if (frameSeqs.length > 225) {
            // 删除最旧的帧
            const toRemove = frameSeqs.slice(225);
            toRemove.forEach(seq => {
                delete this.detectionBuffer[seq];
            });
            console.log(`[AIPlayer-${this.cameraId}] 清理检测数据缓存，删除了${toRemove.length}个旧数据`);
        }
    }

    /**
     * 停止播放
     */
    stop() {
        if (this.captureTimer) {
            clearInterval(this.captureTimer);
            this.captureTimer = null;
        }

        if (this.playbackTimer) {
            clearInterval(this.playbackTimer);
            this.playbackTimer = null;
        }

        this.isPlaying = false;
        this.frameBuffer = [];
        this.detectionBuffer = {};

        console.log(`[AIPlayer-${this.cameraId}] 已停止`);
    }
}

// 导出
window.AIPlayer = AIPlayer;