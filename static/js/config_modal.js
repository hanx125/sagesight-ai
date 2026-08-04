// ========================================
// 参数配置功能
// ========================================

// 当前配置的任务ID
let currentConfigTaskId = null;

// 辅助函数：解析颜色输入（#RRGGBB 或 R,G,B）为 RGB 数组
function _parseColorInput(value, defaultColor) {
    if (!value || typeof value !== 'string') return defaultColor;
    if (value.startsWith('#')) {
        const hex = value.slice(1);
        if (hex.length === 6) {
            const r = parseInt(hex.slice(0, 2), 16);
            const g = parseInt(hex.slice(2, 4), 16);
            const b = parseInt(hex.slice(4, 6), 16);
            if (!isNaN(r) && !isNaN(g) && !isNaN(b)) return [r, g, b];
        }
        return defaultColor;
    }
    const parts = value.trim().split(/[,\s]+/).map(s => parseInt(s.trim()));
    if (parts.length === 3 && parts.every(n => !isNaN(n) && n >= 0 && n <= 255)) return parts;
    return defaultColor;
}

// 辅助函数：RGB 数组转 #RRGGBB（用于 color picker）
function _colorToHex(colorArr, defaultHex) {
    if (Array.isArray(colorArr) && colorArr.length === 3 &&
        colorArr.every(n => typeof n === 'number' && !isNaN(n) && n >= 0 && n <= 255)) {
        const toHex = n => n.toString(16).padStart(2, '0');
        return '#' + toHex(colorArr[0]) + toHex(colorArr[1]) + toHex(colorArr[2]);
    }
    return defaultHex;
}

// 辅助函数：RGB 数组转 "R,G,B"（用于显示）
function _colorToRgbText(colorArr, defaultText) {
    if (Array.isArray(colorArr) && colorArr.length === 3 &&
        colorArr.every(n => typeof n === 'number' && !isNaN(n) && n >= 0 && n <= 255)) {
        return colorArr.join(',');
    }
    return defaultText;
}

// 辅助函数：设置颜色选择器 + 对应 RGB 文本显示
function _setColorInput(id, colorArr, defaultHex, defaultRgb) {
    const picker = document.getElementById(id);
    const display = document.getElementById(id + 'Value');
    if (picker) picker.value = _colorToHex(colorArr, defaultHex);
    if (display) display.textContent = _colorToRgbText(colorArr, defaultRgb);
}

// 辅助函数：从颜色选择器读取 RGB 数组（自动处理 #RRGGBB 格式）
function _getColorFromInput(id, defaultColor) {
    const picker = document.getElementById(id);
    if (!picker) return defaultColor;
    return _parseColorInput(picker.value, defaultColor);
}

/**
 * 存储开关归一化：将旧版 method / output_method / local_backup 字段统一转换为独立开关
 * 优先级：独立开关字段 > 旧版 method 字段 > 默认值
 *
 * @param {Object} cfg - image_output 或 gif 配置对象
 * @param {Object} defaults - 默认开关 { local, minio, ftp }
 * @returns {Object} 归一化后的开关 { local, minio, ftp }
 */
function _normalizeStorageSwitches(cfg, defaults) {
    const result = { ...defaults };

    // 1. 优先读取独立开关字段
    if (cfg.local_enabled !== undefined) result.local = cfg.local_enabled === true;
    if (cfg.minio_enabled !== undefined) result.minio = cfg.minio_enabled === true;
    if (cfg.ftp_enabled !== undefined) result.ftp = cfg.ftp_enabled === true;

    // 2. 向后兼容：若未提供独立开关，但提供了旧版 method / output_method 字段，则转换
    const legacyMethod = cfg.method || cfg.output_method;
    if (legacyMethod !== undefined && cfg.local_enabled === undefined) {
        switch (legacyMethod) {
            case 'both':  result.local = true;  result.minio = true;  result.ftp = true;  break;
            case 'minio': result.local = false; result.minio = true;  result.ftp = false; break;
            case 'ftp':   result.local = false; result.minio = false; result.ftp = true;  break;
            case 'local': result.local = true;  result.minio = false; result.ftp = false; break;
            case 'none':  result.local = false; result.minio = false; result.ftp = false; break;
        }
    }

    // 3. 向后兼容：旧版 local_backup 字段（仅图片输出使用）
    if (cfg.local_backup !== undefined && cfg.local_enabled === undefined && legacyMethod === undefined) {
        result.local = cfg.local_backup === true;
    }

    return result;
}

// 切换配置选项卡
function switchConfigTab(tabName) {
    // 切换选项卡激活状态
    document.querySelectorAll('.config-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });
    // 切换面板显示
    document.querySelectorAll('.tab-panel').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.tab === tabName);
    });
}

// 打开参数配置模态框
async function openConfigModal(taskId, event) {
    event.stopPropagation();

    try {
        // 获取任务详情（包含所有配置）
        const response = await fetch(`/api/tasks/${taskId}`);
        const task = await response.json();

        if (!response.ok) {
            showNotification(task.error || '获取任务配置失败', 'error');
            return;
        }

        currentConfigTaskId = taskId;

        // 设置模态框标题
        document.getElementById('configModalTitle').textContent = `参数配置 - ${task.task_name}`;
        document.getElementById('configTaskId').value = taskId;

        // 加载当前配置到表单
        loadConfigToForm(task);

        // 重置到第一个选项卡
        switchConfigTab('decoder');

        // 显示模态框
        document.getElementById('configModal').classList.add('show');

    } catch (error) {
        console.error('打开配置模态框失败:', error);
        showNotification('打开配置模态框失败', 'error');
    }
}

// 加载配置到表单
function loadConfigToForm(task) {
    // 解码器配置（新架构）
    const decoderConfig = task.decoder_config || {};
    
    // ========== 视频参数 ==========
    document.getElementById('decoderWidth').value = decoderConfig.width || 640;
    document.getElementById('decoderHeight').value = decoderConfig.height || 640;
    document.getElementById('decoderFps').value = decoderConfig.fps || 30;  // 新增：检测帧率
    
    // ========== 硬件加速配置 ==========
    document.getElementById('decoderHwaccelPreset').value = decoderConfig.hwaccel_preset || 'preset-dxva2';  // 新版参数
    
    // ========== 输入预设配置 ==========
    document.getElementById('decoderInputPreset').value = decoderConfig.input_preset || 'preset-rtsp-generic';  // 新增：输入预设

    // ========== 输出配置 ==========
    // ✅ 已删除output_format配置，固定使用I420格式

    // ========== RTSP 配置 ==========
    document.getElementById('decoderRtspTransport').value = decoderConfig.rtsp_transport || 'tcp';
    document.getElementById('decoderReconnect').value = decoderConfig.reconnect ? 'true' : 'false';
    document.getElementById('decoderReconnectDelay').value = decoderConfig.reconnect_delay || 5;
    document.getElementById('decoderBufferSize').value = decoderConfig.buffer_size || 2097152;  // 2MB
    
    // ========== 环形缓冲区配置（新架构）==========
    document.getElementById('decoderRingBufferBlockCount').value = decoderConfig.ring_buffer_block_count ?? 40;
    document.getElementById('decoderIndexQueueSize').value = decoderConfig.index_queue_size || 50;
    
    // ========== FFmpeg 滤镜参数（视频预处理）==========
    document.getElementById('decoderFfmpegFilters').value = (decoderConfig.ffmpeg_filters || []).join(' ');  // FFmpeg 滤镜参数

    // ✅ Zone配置已改为通道级别，不再在任务级别配置
    // 每个通道的Zone配置在通道列表中单独配置

    // 检测器配置（完整配置）
    const detectorConfig = task.detector_config || {};
    document.getElementById('detectorBackend').value = detectorConfig.backend || 'onnx_tensorrt';
    document.getElementById('detectorUseFp16').value = detectorConfig.use_fp16 ? 'true' : 'false';
    document.getElementById('detectorConfThreshold').value = detectorConfig.conf_threshold || 0.25;
    document.getElementById('detectorIouThreshold').value = detectorConfig.iou_threshold || 0.35;
    document.getElementById('detectorMinBoxArea').value = detectorConfig.min_box_area || 50;
    document.getElementById('detectorDeviceId').value = detectorConfig.device_id || 0;

    // 显存限制（转换为MB）
    const gpuMemLimitBytes = detectorConfig.gpu_mem_limit || 6442450944;  // 6GB
    const gpuMemLimitMB = Math.round(gpuMemLimitBytes / (1024 * 1024));  // 转换为MB
    document.getElementById('detectorGpuMemLimit').value = gpuMemLimitMB || 6144;

    // 检测器进程数（null 表示自动计算）
    const numDetectorsEl = document.getElementById('detectorNumDetectors');
    if (numDetectorsEl) {
        numDetectorsEl.value = detectorConfig.num_detectors != null ? detectorConfig.num_detectors : '';
    }

    // 跟踪器配置（Norfair）
    const trackerConfig = task.tracker_config || {};
    // 注意：enabled是任务表的单独字段（tracking_enabled），不从tracker_config读取
    document.getElementById('trackerNInit').value = trackerConfig.n_init || 3;
    document.getElementById('trackerMaxAge').value = trackerConfig.max_age || 30;

    // 运动检测配置（帧差法）
    const motionConfig = task.motion_config || {};
    // 注意：enabled是任务表的单独字段（motion_enabled），不从motion_config读取
    document.getElementById('motionHeight').value = motionConfig.motion_height || 180;
    document.getElementById('motionThreshold').value = motionConfig.threshold || 25;
    document.getElementById('motionContourArea').value = motionConfig.contour_area || 200;
    document.getElementById('motionBlurRadius').value = motionConfig.blur_radius || 1;
    document.getElementById('motionFrameAlpha').value = motionConfig.frame_alpha || 0.01;
    document.getElementById('motionLightningThreshold').value = motionConfig.lightning_threshold || 0.8;
    document.getElementById('motionImproveContrast').value = motionConfig.improve_contrast ? 'true' : 'false';
    document.getElementById('motionContrastFrameHistory').value = motionConfig.contrast_frame_history || 50;

    // 事件输出配置
    const eventConfig = task.event_config || {};
    const imageOutputObj = eventConfig.image_output || {};
    const filterObj = eventConfig.filter || {};
    const gifObj = eventConfig.gif || {};
    const captureObj = eventConfig.capture || {};

    document.getElementById('eventEnabled').value = eventConfig.enabled !== false ? 'true' : 'false';  // 默认启用

    // 图片存储开关加载（向后兼容旧版 method / local_backup 字段）
    const imageSwitches = _normalizeStorageSwitches(imageOutputObj, { local: true, minio: false, ftp: false });
    document.getElementById('eventLocalEnabled').value = imageSwitches.local ? 'true' : 'false';
    document.getElementById('eventMinioEnabled').value = imageSwitches.minio ? 'true' : 'false';
    document.getElementById('eventFtpEnabled').value = imageSwitches.ftp ? 'true' : 'false';
    document.getElementById('eventImageOutputDir').value = imageOutputObj.output_dir || 'output/images';
    document.getElementById('eventImageDrawBbox').value = imageOutputObj.draw_bbox !== false ? 'true' : 'false';
    document.getElementById('eventMinConfidence').value = filterObj.min_confidence || 0.5;
    document.getElementById('eventGifEnabled').value = gifObj.enabled !== false ? 'true' : 'false';
    // GIF存储开关加载（向后兼容旧版 output_method 字段）
    const gifSwitches = _normalizeStorageSwitches(gifObj, { local: true, minio: false, ftp: false });
    document.getElementById('eventGifLocalEnabled').value = gifSwitches.local ? 'true' : 'false';
    document.getElementById('eventGifMinioEnabled').value = gifSwitches.minio ? 'true' : 'false';
    document.getElementById('eventGifFtpEnabled').value = gifSwitches.ftp ? 'true' : 'false';
    document.getElementById('eventGifOutputDir').value = gifObj.output_dir || 'output/gifs';
    document.getElementById('eventGifDrawBbox').value = gifObj.draw_bbox !== false ? 'true' : 'false';
    document.getElementById('eventCropMarginRatio').value = captureObj.crop_margin_ratio !== undefined ? captureObj.crop_margin_ratio : 1.0;
    document.getElementById('eventGifCropMode').value = gifObj.crop_mode || 'full';
    document.getElementById('eventGifPreSeconds').value = gifObj.pre_seconds !== undefined ? gifObj.pre_seconds : 5.0;
    document.getElementById('eventGifPostSeconds').value = gifObj.post_seconds !== undefined ? gifObj.post_seconds : 5.0;

    // 显示配置
    const displayConfig = task.display_config || {};
    const displayObj = displayConfig.display || {};
    const detectionBoxObj = displayConfig.detection_box || {};
    const trackBoxObj = displayConfig.track_box || {};
    const trajectoryObj = displayConfig.trajectory || {};

    document.getElementById('displayShowBoxes').value = displayObj.show_detection_boxes !== false ? 'true' : 'false';
    document.getElementById('displayShowTracks').value = displayObj.show_track_boxes !== false ? 'true' : 'false';
    document.getElementById('displayShowTrajectories').value = displayObj.show_trajectories !== false ? 'true' : 'false';
    document.getElementById('displayShowMotionRegions').value = displayObj.show_motion_regions !== false ? 'true' : 'false';
    document.getElementById('displayShowZones').value = displayObj.show_zones !== false ? 'true' : 'false';
    document.getElementById('displayEnabled').value = displayObj.enabled !== false ? 'true' : 'false';
    document.getElementById('displayTargetFps').value = displayObj.target_fps || 15;
    document.getElementById('displayAIStreamInterval').value = displayObj.ai_stream_interval || 10;
    document.getElementById('displayTrajectoryHistory').value = displayObj.trajectory_history_length || 30;

    // 颜色管理器配置（✅ 修复：input[type=color] 只接受 #RRGGBB 格式）
    _setColorInput('displayBoxColor', detectionBoxObj.color, '#00ff00', '0,255,0');
    document.getElementById('displayBoxThickness').value = detectionBoxObj.thickness || 2;
    document.getElementById('displayBoxFontScale').value = detectionBoxObj.font_scale || 0.5;
    _setColorInput('displayBoxLabelColor', detectionBoxObj.label_color, '#00ff00', '0,255,0');

    _setColorInput('displayTrackColor', trackBoxObj.color, '#0000ff', '0,0,255');
    document.getElementById('displayTrackThickness').value = trackBoxObj.thickness || 2;
    document.getElementById('displayTrackFontScale').value = trackBoxObj.font_scale || 0.5;
    _setColorInput('displayTrackLabelColor', trackBoxObj.label_color, '#0000ff', '0,0,255');

    _setColorInput('displayTrajectoryColor', trajectoryObj.color, '#ffff00', '255,255,0');
    document.getElementById('displayTrajectoryThickness').value = trajectoryObj.thickness || 1;
    document.getElementById('displayTrajectoryPointSize').value = trajectoryObj.point_size || 2;
    _setColorInput('displayTrajectoryPointColor', trajectoryObj.point_color, '#ff0000', '255,0,0');

    // 加载运动区域颜色配置
    const motionRegionObj = displayConfig.motion_region || {};
    _setColorInput('displayMotionRegionColor', motionRegionObj.color, '#00ffff', '0,255,255');
    document.getElementById('displayMotionRegionThickness').value = motionRegionObj.thickness || 1;
    _setColorInput('displayMotionRegionFillColor', motionRegionObj.fill_color, '#00ffff', '0,255,255');
    document.getElementById('displayMotionRegionFillAlpha').value = motionRegionObj.fill_alpha ?? 50;
}

// 关闭参数配置模态框
function closeConfigModal() {
    document.getElementById('configModal').classList.remove('show');
    currentConfigTaskId = null;
}

// 保存配置
async function saveConfig() {
    if (!currentConfigTaskId) {
        showNotification('任务ID无效', 'error');
        return;
    }

    try {
        // ✅ 修复：获取任务详情（用于读取现有配置）
        const taskResponse = await fetch(`/api/tasks/${currentConfigTaskId}`);
        const task = await taskResponse.json();
        
        if (!taskResponse.ok) {
            showNotification(task.error || '获取任务配置失败', 'error');
            return;
        }
        
        // 解析 FFmpeg 滤镜参数（字符串转数组）
        const ffmpegFiltersStr = document.getElementById('decoderFfmpegFilters').value.trim();
        const ffmpegFilters = ffmpegFiltersStr ? ffmpegFiltersStr.split(/\s+/) : [];

        // 构建配置数据
        const configData = {
            // 解码器配置（新架构）
            decoder_config: {
                // ========== 视频参数 ==========
                width: parseInt(document.getElementById('decoderWidth').value) || 640,
                height: parseInt(document.getElementById('decoderHeight').value) || 640,
                fps: parseInt(document.getElementById('decoderFps').value) || 30,  // 新增：检测帧率

                // ========== 硬件加速配置 ==========
                hwaccel_preset: document.getElementById('decoderHwaccelPreset').value || 'preset-dxva2',  // 新版参数

                // ========== 输入预设配置 ==========
                input_preset: document.getElementById('decoderInputPreset').value || 'preset-rtsp-generic',  // 新增：输入预设

                // ========== 输出配置 ==========
                // ✅ 已删除output_format配置，固定使用I420格式

                // ========== RTSP 配置 ==========
                rtsp_transport: document.getElementById('decoderRtspTransport').value || 'tcp',
                reconnect: document.getElementById('decoderReconnect').value === 'true',
                reconnect_delay: parseInt(document.getElementById('decoderReconnectDelay').value) || 5,
                buffer_size: parseInt(document.getElementById('decoderBufferSize').value) || 2097152,  // 2MB

                // ========== 环形缓冲区配置（新架构）==========
                ring_buffer_block_count: parseInt(document.getElementById('decoderRingBufferBlockCount').value) || 40,
                index_queue_size: parseInt(document.getElementById('decoderIndexQueueSize').value) || 50,

                // ========== FFmpeg 滤镜参数（视频预处理）==========
                ffmpeg_filters: ffmpegFilters  // FFmpeg 滤镜参数
            },

            // 检测器配置（完整配置）
            detector_config: {
                backend: document.getElementById('detectorBackend').value || 'onnx_tensorrt',
                use_fp16: document.getElementById('detectorUseFp16').value === 'true',
                conf_threshold: parseFloat(document.getElementById('detectorConfThreshold').value) || 0.25,
                iou_threshold: parseFloat(document.getElementById('detectorIouThreshold').value) || 0.35,
                min_box_area: parseInt(document.getElementById('detectorMinBoxArea').value) || 50,
                device_id: parseInt(document.getElementById('detectorDeviceId').value) || 0,

                // 显存限制（MB转换为字节）
                gpu_mem_limit: (parseInt(document.getElementById('detectorGpuMemLimit').value) || 6144) * 1024 * 1024,

                // 检测器进程数（空值表示自动计算）
                num_detectors: document.getElementById('detectorNumDetectors')?.value
                    ? parseInt(document.getElementById('detectorNumDetectors').value)
                    : null,

                // 注意：
                // 1. target_classes是任务表的单独字段，不放在detector_config中
                // 2. input_width/input_height等高级配置项不保存，由后端使用默认值
            },

            // 跟踪器配置（Norfair）
            tracker_config: {
                // 注意：enabled是任务表的单独字段（tracking_enabled），不放在tracker_config中
                tracker_type: 'norfair',
                debug_print_interval: 0,  // 固定（禁用调试）
                n_init: parseInt(document.getElementById('trackerNInit').value) || 3,
                max_age: parseInt(document.getElementById('trackerMaxAge').value) || 30,
            },

            // 运动检测配置（帧差法）
            motion_config: {
                // 注意：enabled是任务表的单独字段（motion_enabled），不放在motion_config中
                motion_height: parseInt(document.getElementById('motionHeight').value) || 180,
                threshold: parseInt(document.getElementById('motionThreshold').value) || 25,
                contour_area: parseInt(document.getElementById('motionContourArea').value) || 200,
                blur_radius: parseInt(document.getElementById('motionBlurRadius').value) || 1,
                frame_alpha: parseFloat(document.getElementById('motionFrameAlpha').value) || 0.01,
                lightning_threshold: parseFloat(document.getElementById('motionLightningThreshold').value) || 0.8,
                improve_contrast: document.getElementById('motionImproveContrast').value === 'true',
                contrast_frame_history: parseInt(document.getElementById('motionContrastFrameHistory').value) || 50,
            },

            // 事件输出配置
            event_config: {
                enabled: document.getElementById('eventEnabled').value === 'true',
                // 注意：storage、mqtt、websocket属于系统配置，从system_config.yaml读取，不在event_config中保存

                metadata: task.event_config?.metadata || {},  // 保留现有值

                // 图片压缩配置
                compression: task.event_config?.compression || {
                    enabled: true,
                    quality: 80,
                    max_width: 640,
                    max_height: 480
                },

                // 图片输出配置（独立开关控制本地/MinIO/FTP）
                image_output: {
                    local_enabled: document.getElementById('eventLocalEnabled').value === 'true',
                    minio_enabled: document.getElementById('eventMinioEnabled').value === 'true',
                    ftp_enabled: document.getElementById('eventFtpEnabled').value === 'true',
                    minio_bucket: task.event_config?.image_output?.minio_bucket || 'sagesight-images',
                    ftp_remote_dir: task.event_config?.image_output?.ftp_remote_dir || '/sagesight/images',
                    output_dir: document.getElementById('eventImageOutputDir').value || 'output/images',
                    local_retention_days: task.event_config?.image_output?.local_retention_days || 7,
                    auto_cleanup: task.event_config?.image_output?.auto_cleanup !== false,
                    cleanup_interval_hours: task.event_config?.image_output?.cleanup_interval_hours || 24,
                    draw_bbox: document.getElementById('eventImageDrawBbox').value === 'true'
                },

                // GIF输出配置（独立开关控制本地/MinIO/FTP）
                gif: {
                    enabled: document.getElementById('eventGifEnabled').value === 'true',
                    local_enabled: document.getElementById('eventGifLocalEnabled').value === 'true',
                    minio_enabled: document.getElementById('eventGifMinioEnabled').value === 'true',
                    ftp_enabled: document.getElementById('eventGifFtpEnabled').value === 'true',
                    output_dir: document.getElementById('eventGifOutputDir').value || 'output/gifs',
                    local_retention_days: task.event_config?.gif?.local_retention_days || 7,
                    auto_cleanup: task.event_config?.gif?.auto_cleanup !== false,
                    cleanup_interval_hours: task.event_config?.gif?.cleanup_interval_hours || 24,
                    max_frames: task.event_config?.gif?.max_frames || 30,
                    fps: task.event_config?.gif?.fps || 5,
                    max_width: task.event_config?.gif?.max_width || 320,
                    max_height: task.event_config?.gif?.max_height || 240,
                    draw_trajectory: task.event_config?.gif?.draw_trajectory !== false,
                    trajectory_color: task.event_config?.gif?.trajectory_color || [0, 255, 0],
                    crop_mode: document.getElementById('eventGifCropMode').value || 'full',
                    pre_seconds: parseFloat(document.getElementById('eventGifPreSeconds').value) || 5.0,
                    post_seconds: parseFloat(document.getElementById('eventGifPostSeconds').value) || 5.0,
                    cache_fps: task.event_config?.gif?.cache_fps || 10,
                    cache_retention: task.event_config?.gif?.cache_retention || 20.0,
                    draw_bbox: document.getElementById('eventGifDrawBbox').value === 'true',
                    minio_bucket: task.event_config?.gif?.minio_bucket || 'sagesight-gifs',
                    ftp_remote_dir: task.event_config?.gif?.ftp_remote_dir || '/gifs'
                },

                trajectory: task.event_config?.trajectory || {},  // 保留现有值
                // 关键帧采集配置
                capture: {
                    strategy: task.event_config?.capture?.strategy || 'periodic',
                    interval: task.event_config?.capture?.interval || 5,
                    change_threshold: task.event_config?.capture?.change_threshold || 50,
                    save_full_on_start: task.event_config?.capture?.save_full_on_start !== false,
                    save_full_on_end: task.event_config?.capture?.save_full_on_end !== false,
                    crop_margin_ratio: parseFloat(document.getElementById('eventCropMarginRatio').value) || 1.0
                },

                // 过滤配置（✅ 修复：使用任务表的target_classes字段）
                filter: {
                    target_classes: task.target_classes || ['person', 'car'],
                    min_confidence: parseFloat(document.getElementById('eventMinConfidence').value) || 0.5
                }
            },

            // 显示配置
            display_config: {
                display: {
                    enabled: document.getElementById('displayEnabled').value === 'true',
                    window_title: "SageSight AI - Detection Monitor",
                    target_fps: parseInt(document.getElementById('displayTargetFps').value) || 15,
                    show_detection_boxes: document.getElementById('displayShowBoxes').value === 'true',
                    show_track_boxes: document.getElementById('displayShowTracks').value === 'true',
                    show_trajectories: document.getElementById('displayShowTrajectories').value === 'true',
                    show_motion_regions: document.getElementById('displayShowMotionRegions').value === 'true',
                    show_zones: document.getElementById('displayShowZones').value === 'true',
                    trajectory_history_length: parseInt(document.getElementById('displayTrajectoryHistory').value) || 30,
                    ai_stream_interval: parseInt(document.getElementById('displayAIStreamInterval').value) || 10
                },
                detection_box: {
                    color: _getColorFromInput('displayBoxColor', [0, 255, 0]),
                    thickness: parseInt(document.getElementById('displayBoxThickness').value) || 2,
                    font_scale: parseFloat(document.getElementById('displayBoxFontScale').value) || 0.5,
                    label_color: _getColorFromInput('displayBoxLabelColor', [0, 255, 0])
                },
                track_box: {
                    color: _getColorFromInput('displayTrackColor', [0, 0, 255]),
                    thickness: parseInt(document.getElementById('displayTrackThickness').value) || 2,
                    font_scale: parseFloat(document.getElementById('displayTrackFontScale').value) || 0.5,
                    label_color: _getColorFromInput('displayTrackLabelColor', [0, 0, 255])
                },
                trajectory: {
                    color: _getColorFromInput('displayTrajectoryColor', [255, 255, 0]),
                    thickness: parseInt(document.getElementById('displayTrajectoryThickness').value) || 1,
                    point_size: parseInt(document.getElementById('displayTrajectoryPointSize').value) || 2,
                    point_color: _getColorFromInput('displayTrajectoryPointColor', [255, 0, 0])
                },
                motion_region: {
                    color: _getColorFromInput('displayMotionRegionColor', [0, 255, 255]),
                    thickness: parseInt(document.getElementById('displayMotionRegionThickness').value) || 1,
                    fill_color: _getColorFromInput('displayMotionRegionFillColor', [0, 255, 255]),
                    fill_alpha: parseInt(document.getElementById('displayMotionRegionFillAlpha').value) ?? 50
                }
            }
        };

        // 发送更新请求
        const response = await fetch(`/api/tasks/${currentConfigTaskId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(configData)
        });

        const result = await response.json();

        if (response.ok) {
            // 根据任务状态给出针对性提示
            const taskStatus = task.status || 'stopped';
            const isRunning = taskStatus === 'running';

            if (isRunning) {
                // 任务运行中：提示需要重启才能生效的配置
                showNotification('配置已保存。显示配置、存储设置等变更需重启任务后生效', 'warning');
            } else {
                showNotification('配置已保存', 'success');
            }
            closeConfigModal();
            // 刷新任务列表（tasks.js 已改造为 IIFE 命名空间）
            if (window.Tasks && typeof window.Tasks.loadTasks === 'function') {
                window.Tasks.loadTasks();
            }
        } else {
            showNotification(result.error || '保存配置失败', 'error');
        }

    } catch (error) {
        console.error('保存配置失败:', error);
        showNotification('保存配置失败', 'error');
    }
}

// 点击模态框外部关闭
document.addEventListener('click', function(event) {
    if (event.target.id === 'configModal') {
        closeConfigModal();
    }
});

