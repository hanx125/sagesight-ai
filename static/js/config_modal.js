// ========================================
// 参数配置功能
// ========================================

// 当前配置的任务ID
let currentConfigTaskId = null;

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
    document.getElementById('decoderRingBufferBlockCount').value = decoderConfig.ring_buffer_block_count || 10;
    document.getElementById('decoderIndexQueueSize').value = decoderConfig.index_queue_size || 50;
    
    // ========== FFmpeg 滤镜参数（视频预处理）==========
    document.getElementById('decoderFfmpegFilters').value = (decoderConfig.ffmpeg_filters || []).join(' ');  // FFmpeg 滤镜参数

    // ✅ Zone配置已改为通道级别，不再在任务级别配置
    // 每个通道的Zone配置在通道列表中单独配置

    // 检测器配置（完整配置）
    const detectorConfig = task.detector_config || {};
    document.getElementById('useDetectionPool').value = detectorConfig.use_detection_pool ? 'true' : 'false';  // ✅ 修复：从detector_config读取
    document.getElementById('useGpuPreprocess').value = detectorConfig.use_gpu_preprocess ? 'true' : 'false';  // ✅ 新增：GPU预处理开关
    document.getElementById('detectorBackend').value = detectorConfig.backend || 'onnx_tensorrt';
    document.getElementById('detectorUseFp16').value = detectorConfig.use_fp16 ? 'true' : 'false';
    document.getElementById('detectorConfThreshold').value = detectorConfig.conf_threshold || 0.25;
    document.getElementById('detectorIouThreshold').value = detectorConfig.iou_threshold || 0.35;
    document.getElementById('detectorMinBoxArea').value = detectorConfig.min_box_area || 50;
    document.getElementById('detectorDeviceId').value = detectorConfig.device_id || 0;

    // ✅ 显存限制（转换为MB）
    const gpuMemLimitBytes = detectorConfig.gpu_mem_limit || 6442450944;  // 6GB
    const gpuMemLimitMB = Math.round(gpuMemLimitBytes / (1024 * 1024));  // 转换为MB
    document.getElementById('detectorGpuMemLimit').value = gpuMemLimitMB || 6144;

    // 预处理器配置（扁平格式，直接从 detector_config 读取）
    document.getElementById('detectorPreprocessorWidth').value = detectorConfig.width || 640;
    document.getElementById('detectorPreprocessorHeight').value = detectorConfig.height || 640;
    document.getElementById('detectorPreprocessorModelInputSize').value = detectorConfig.model_input_size || 640;
    document.getElementById('detectorPreprocessorRgbMean').value = (detectorConfig.rgb_mean || [0.0, 0.0, 0.0]).join(',');
    document.getElementById('detectorPreprocessorRgbStd').value = (detectorConfig.rgb_std || [1.0, 1.0, 1.0]).join(',');

    // 跟踪器配置
    const trackerConfig = task.tracker_config || {};
    const botsortObj = trackerConfig.botsort || trackerConfig;  // 支持两种结构

    // 注意：enabled是任务表的单独字段（tracking_enabled），不从tracker_config读取

    document.getElementById('trackerTrackThresh').value = botsortObj.track_thresh || 0.30;
    document.getElementById('trackerMatchThresh').value = botsortObj.match_thresh || 0.50;
    document.getElementById('trackerMaxAge').value = botsortObj.max_age || 45;
    document.getElementById('trackerNInit').value = botsortObj.n_init || 2;
    document.getElementById('trackerMaxHistoryLength').value = botsortObj.max_history_length || 30;

    // 运动检测配置
    const motionConfig = task.motion_config || {};

    // 注意：enabled是任务表的单独字段（motion_enabled），不从motion_config读取

    document.getElementById('motionHistory').value = motionConfig.history || 500;
    document.getElementById('motionVarThreshold').value = motionConfig.var_threshold || 16.0;
    document.getElementById('motionMinArea').value = motionConfig.min_area || 500;

    // fusion配置是扁平结构，不是子对象
    document.getElementById('motionConfidenceBoost').value = motionConfig.confidence_boost || 0.15;
    document.getElementById('motionConfidencePenalty').value = motionConfig.confidence_penalty || 0.05;

    // 新增：高级运动检测参数
    document.getElementById('motionDetectShadows').value = motionConfig.detect_shadows ? 'true' : 'false';
    document.getElementById('motionBlurSize').value = motionConfig.blur_size || 5;
    document.getElementById('motionMorphSize').value = motionConfig.morph_size || 5;
    document.getElementById('motionLearningRate').value = motionConfig.learning_rate || -1;
    document.getElementById('motionHistoryLength').value = motionConfig.motion_history_length || 10;
    document.getElementById('motionInitThreshold').value = motionConfig.init_threshold || 30;
    document.getElementById('motionForegroundThreshold').value = motionConfig.foreground_threshold || 0.3;
    document.getElementById('motionMaxMotionRegions').value = motionConfig.max_motion_regions || 20;
    document.getElementById('motionShowVisualization').value = motionConfig.show_visualization ? 'true' : 'false';
    document.getElementById('motionUseForReid').value = motionConfig.use_for_reid ? 'true' : 'false';

    // 事件输出配置
    const eventConfig = task.event_config || {};
    const imageOutputObj = eventConfig.image_output || {};
    const filterObj = eventConfig.filter || {};
    const gifObj = eventConfig.gif || {};
    const captureObj = eventConfig.capture || {};

    document.getElementById('eventEnabled').value = eventConfig.enabled !== false ? 'true' : 'false';  // 默认启用

    document.getElementById('eventImageMethod').value = imageOutputObj.method || 'both';
    document.getElementById('eventLocalBackup').value = imageOutputObj.local_backup ? 'true' : 'false';
    document.getElementById('eventImageOutputDir').value = imageOutputObj.output_dir || 'output/images';
    document.getElementById('eventImageDrawBbox').value = imageOutputObj.draw_bbox !== false ? 'true' : 'false';
    document.getElementById('eventMinConfidence').value = filterObj.min_confidence || 0.5;
    document.getElementById('eventGifEnabled').value = gifObj.enabled !== false ? 'true' : 'false';
    document.getElementById('eventGifOutputMethod').value = gifObj.output_method || 'both';
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
    const debugInfoObj = displayConfig.debug_info || {};

    document.getElementById('displayShowBoxes').value = displayObj.show_detection_boxes !== false ? 'true' : 'false';
    document.getElementById('displayShowTracks').value = displayObj.show_track_boxes !== false ? 'true' : 'false';
    document.getElementById('displayShowTrajectories').value = displayObj.show_trajectories !== false ? 'true' : 'false';
    document.getElementById('displayShowMotionRegions').value = displayObj.show_motion_regions !== false ? 'true' : 'false';
    document.getElementById('displayEnabled').value = displayObj.enabled !== false ? 'true' : 'false';
    document.getElementById('displayTargetFps').value = displayObj.target_fps || 15;
    document.getElementById('displayAIStreamEnabled').value = displayObj.ai_stream_enabled !== false ? 'true' : 'false';
    document.getElementById('displayAIStreamInterval').value = displayObj.ai_stream_interval || 10;  // ✅ 修复：默认值改为10
    document.getElementById('displayTrajectoryHistory').value = displayObj.trajectory_history_length || 30;

    document.getElementById('displayBoxColor').value = (detectionBoxObj.color || [0, 255, 0]).join(',');
    document.getElementById('displayBoxThickness').value = detectionBoxObj.thickness || 2;
    document.getElementById('displayBoxFontScale').value = detectionBoxObj.font_scale || 0.5;
    document.getElementById('displayBoxLabelColor').value = (detectionBoxObj.label_color || [0, 255, 0]).join(',');
    document.getElementById('displayTrackColor').value = (trackBoxObj.color || [255, 0, 0]).join(',');
    document.getElementById('displayTrackThickness').value = trackBoxObj.thickness || 2;
    document.getElementById('displayTrackFontScale').value = trackBoxObj.font_scale || 0.5;
    document.getElementById('displayTrackLabelColor').value = (trackBoxObj.label_color || [255, 0, 0]).join(',');
    document.getElementById('displayTrajectoryColor').value = (trajectoryObj.color || [0, 255, 255]).join(',');
    document.getElementById('displayTrajectoryThickness').value = trajectoryObj.thickness || 1;
    document.getElementById('displayTrajectoryPointSize').value = trajectoryObj.point_size || 2;
    document.getElementById('displayTrajectoryPointColor').value = (trajectoryObj.point_color || [0, 0, 255]).join(',');

    // 新增：调试信息配置
    document.getElementById('debugShowFps').value = debugInfoObj.show_fps ? 'true' : 'false';
    document.getElementById('debugShowDetectionCount').value = debugInfoObj.show_detection_count ? 'true' : 'false';
    document.getElementById('debugShowTrackCount').value = debugInfoObj.show_track_count ? 'true' : 'false';
    document.getElementById('debugShowFrameId').value = debugInfoObj.show_frame_id ? 'true' : 'false';
    document.getElementById('debugFpsPosition').value = (debugInfoObj.fps_position || [10, 30]).join(',');
    document.getElementById('debugFontScale').value = debugInfoObj.font_scale || 0.6;
    document.getElementById('debugTextColor').value = (debugInfoObj.text_color || [255, 255, 255]).join(',');
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
                ring_buffer_block_count: parseInt(document.getElementById('decoderRingBufferBlockCount').value) || 10,
                index_queue_size: parseInt(document.getElementById('decoderIndexQueueSize').value) || 50,

                // ========== FFmpeg 滤镜参数（视频预处理）==========
                ffmpeg_filters: ffmpegFilters  // FFmpeg 滤镜参数
            },

            // 检测器配置（完整配置）
            detector_config: {
                use_detection_pool: document.getElementById('useDetectionPool').value === 'true',
                use_gpu_preprocess: document.getElementById('useGpuPreprocess').value === 'true',  // ✅ 新增：GPU预处理开关
                backend: document.getElementById('detectorBackend').value || 'onnx_tensorrt',
                use_fp16: document.getElementById('detectorUseFp16').value === 'true',
                conf_threshold: parseFloat(document.getElementById('detectorConfThreshold').value) || 0.25,
                iou_threshold: parseFloat(document.getElementById('detectorIouThreshold').value) || 0.35,
                min_box_area: parseInt(document.getElementById('detectorMinBoxArea').value) || 50,
                device_id: parseInt(document.getElementById('detectorDeviceId').value) || 0,

                // ✅ 显存限制（MB转换为字节）
                gpu_mem_limit: (parseInt(document.getElementById('detectorGpuMemLimit').value) || 6144) * 1024 * 1024,

                // ✅ 预处理器配置（扁平保存，直接放在 detector_config 下）
                width: parseInt(document.getElementById('detectorPreprocessorWidth').value) || 640,
                height: parseInt(document.getElementById('detectorPreprocessorHeight').value) || 640,
                model_input_size: parseInt(document.getElementById('detectorPreprocessorModelInputSize').value) || 640,
                rgb_mean: document.getElementById('detectorPreprocessorRgbMean').value.split(',').map(Number),
                rgb_std: document.getElementById('detectorPreprocessorRgbStd').value.split(',').map(Number)

                // 注意：
                // 1. target_classes是任务表的单独字段，不放在detector_config中
                // 2. input_width/input_height等高级配置项不保存，由后端使用默认值
            },

            // 跟踪器配置
            tracker_config: {
                // 注意：enabled是任务表的单独字段（tracking_enabled），不放在tracker_config中
                tracker_type: 'botsort',  // 固定
                debug_print_interval: 0,  // 固定（禁用调试）

                botsort: {
                    track_thresh: parseFloat(document.getElementById('trackerTrackThresh').value) || 0.30,
                    match_thresh: parseFloat(document.getElementById('trackerMatchThresh').value) || 0.50,
                    max_age: parseInt(document.getElementById('trackerMaxAge').value) || 45,
                    n_init: parseInt(document.getElementById('trackerNInit').value) || 2,
                    max_history_length: parseInt(document.getElementById('trackerMaxHistoryLength').value) || 30,

                    // ✅ 固定配置项（不从前端读取）
                    low_score_thresh: 0.10,
                    use_reid: false,
                    reid_model_type: 'none',
                    reid_model_path: '',
                    reid_thresh: 0.3,
                    device: 'cpu',
                    half: false
                }
            },

            // 运动检测配置
            motion_config: {
                // 注意：enabled是任务表的单独字段（motion_enabled），不放在motion_config中

                // 用户可编辑的配置项
                history: parseInt(document.getElementById('motionHistory').value) || 500,
                var_threshold: parseFloat(document.getElementById('motionVarThreshold').value) || 16.0,
                min_area: parseInt(document.getElementById('motionMinArea').value) || 500,

                // fusion配置（扁平结构，不是子对象）
                confidence_boost: parseFloat(document.getElementById('motionConfidenceBoost').value) || 0.15,
                confidence_penalty: parseFloat(document.getElementById('motionConfidencePenalty').value) || 0.05,

                // 新增：高级运动检测参数（从前端读取）
                detect_shadows: document.getElementById('motionDetectShadows').value === 'true',
                blur_size: parseInt(document.getElementById('motionBlurSize').value) || 5,
                morph_size: parseInt(document.getElementById('motionMorphSize').value) || 5,
                learning_rate: parseFloat(document.getElementById('motionLearningRate').value) || -1,
                motion_history_length: parseInt(document.getElementById('motionHistoryLength').value) || 10,
                init_threshold: parseInt(document.getElementById('motionInitThreshold').value) || 30,
                foreground_threshold: parseFloat(document.getElementById('motionForegroundThreshold').value) || 0.3,
                max_motion_regions: parseInt(document.getElementById('motionMaxMotionRegions').value) || 20,
                show_visualization: document.getElementById('motionShowVisualization').value === 'true',
                use_for_reid: document.getElementById('motionUseForReid').value === 'true'
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

                // 图片输出配置
                image_output: {
                    method: document.getElementById('eventImageMethod').value || 'both',
                    minio_bucket: task.event_config?.image_output?.minio_bucket || 'sagesight-images',
                    ftp_remote_dir: task.event_config?.image_output?.ftp_remote_dir || '/sagesight/images',
                    local_backup: document.getElementById('eventLocalBackup').value === 'true',
                    output_dir: document.getElementById('eventImageOutputDir').value || 'output/images',
                    local_retention_days: task.event_config?.image_output?.local_retention_days || 7,
                    auto_cleanup: task.event_config?.image_output?.auto_cleanup !== false,
                    cleanup_interval_hours: task.event_config?.image_output?.cleanup_interval_hours || 24,
                    draw_bbox: document.getElementById('eventImageDrawBbox').value === 'true'
                },

                // GIF输出配置
                gif: {
                    enabled: document.getElementById('eventGifEnabled').value === 'true',
                    output_method: document.getElementById('eventGifOutputMethod').value || 'both',
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
                    trajectory_history_length: parseInt(document.getElementById('displayTrajectoryHistory').value) || 30,
                    ai_stream_enabled: document.getElementById('displayAIStreamEnabled').value === 'true',
                    ai_stream_interval: parseInt(document.getElementById('displayAIStreamInterval').value) || 10
                },
                detection_box: {
                    color: document.getElementById('displayBoxColor').value.split(',').map(Number),
                    thickness: parseInt(document.getElementById('displayBoxThickness').value) || 2,
                    font_scale: parseFloat(document.getElementById('displayBoxFontScale').value) || 0.5,
                    label_color: document.getElementById('displayBoxLabelColor').value.split(',').map(Number)
                },
                track_box: {
                    color: document.getElementById('displayTrackColor').value.split(',').map(Number),
                    thickness: parseInt(document.getElementById('displayTrackThickness').value) || 2,
                    font_scale: parseFloat(document.getElementById('displayTrackFontScale').value) || 0.5,
                    label_color: document.getElementById('displayTrackLabelColor').value.split(',').map(Number)
                },
                trajectory: {
                    color: document.getElementById('displayTrajectoryColor').value.split(',').map(Number),
                    thickness: parseInt(document.getElementById('displayTrajectoryThickness').value) || 1,
                    point_size: parseInt(document.getElementById('displayTrajectoryPointSize').value) || 2,
                    point_color: document.getElementById('displayTrajectoryPointColor').value.split(',').map(Number)
                },
                motion_region: task.display_config?.motion_region || {
                    color: [255, 255, 0],
                    thickness: 1,
                    fill_color: [255, 255, 0],
                    fill_alpha: 50
                },
                debug_info: {
                    show_fps: document.getElementById('debugShowFps').value === 'true',
                    show_detection_count: document.getElementById('debugShowDetectionCount').value === 'true',
                    show_track_count: document.getElementById('debugShowTrackCount').value === 'true',
                    show_frame_id: document.getElementById('debugShowFrameId').value === 'true',
                    fps_position: document.getElementById('debugFpsPosition').value.split(',').map(Number),
                    font_scale: parseFloat(document.getElementById('debugFontScale').value) || 0.6,
                    text_color: document.getElementById('debugTextColor').value.split(',').map(Number)
                }
            },

            // ✅ 新增：Zone区域配置
            zones_config: getZonesConfigFromForm()
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
            showNotification('配置已保存', 'success');
            closeConfigModal();
            loadTasks(); // 刷新任务列表
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

// ========================================
// ✅ 新增：Zone区域配置管理
// ========================================

let zoneConfigCounter = 0;  // Zone配置计数器

/**
 * 添加Zone区域配置
 */
function addZoneConfig() {
    zoneConfigCounter++;

    const container = document.getElementById('zonesConfigContainer');

    const zoneDiv = document.createElement('div');
    zoneDiv.className = 'zone-config-item';
    zoneDiv.id = `zoneConfig${zoneConfigCounter}`;
    zoneDiv.style.cssText = 'border: 1px solid #e0e0e0; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: #fafafa;';

    zoneDiv.innerHTML = `
        <div class="form-row" style="margin-bottom: 10px;">
            <div class="form-group" style="flex: 1;">
                <label>区域名称</label>
                <input type="text" id="zoneName${zoneConfigCounter}" name="zone_name_${zoneConfigCounter}" placeholder="例如：入口区域">
            </div>
            <div class="form-group" style="flex: 0 0 auto; margin-left: 20px;">
                <button type="button" class="btn btn-danger" onclick="removeZoneConfig('zoneConfig${zoneConfigCounter}')">删除</button>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>区域多边形坐标</label>
                <input type="text" id="zonePolygon${zoneConfigCounter}" name="zone_polygon_${zoneConfigCounter}" placeholder="x1,y1;x2,y2;x3,y3;x4,y4">
                <small style="color: #888; font-size: 12px;">多边形顶点坐标，用分号分隔（例如：100,100;200,100;200,200;100,200）</small>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label for="zoneRequired${zoneConfigCounter}">必需区域</label>
                <select id="zoneRequired${zoneConfigCounter}" name="zone_required_${zoneConfigCounter}">
                    <option value="false">否</option>
                    <option value="true">是</option>
                </select>
                <small style="color: #888; font-size: 12px;">必需区域：只有在此区域内才触发事件</small>
            </div>
            <div class="form-group">
                <label for="zoneLoitering${zoneConfigCounter}">徘徊检测</label>
                <select id="zoneLoitering${zoneConfigCounter}" name="zone_loitering_${zoneConfigCounter}">
                    <option value="false">禁用</option>
                    <option value="true">启用</option>
                </select>
                <small style="color: #888; font-size: 12px;">检测对象在区域内长时间停留</small>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label for="zoneLoiteringTime${zoneConfigCounter}">徘徊判定时间（帧）</label>
                <input type="number" id="zoneLoiteringTime${zoneConfigCounter}" name="zone_loitering_time_${zoneConfigCounter}" placeholder="30">
                <small style="color: #888; font-size: 12px;">超过多少帧判定为徘徊（30帧≈1秒）</small>
            </div>
            <div class="form-group">
                <label for="zoneColor${zoneConfigCounter}">区域颜色（BGR）</label>
                <input type="text" id="zoneColor${zoneConfigCounter}" name="zone_color_${zoneConfigCounter}" placeholder="255,0,0">
            </div>
        </div>
    `;

    container.appendChild(zoneDiv);
}

/**
 * 删除Zone区域配置
 */
function removeZoneConfig(zoneConfigId) {
    const zoneDiv = document.getElementById(zoneConfigId);
    if (zoneDiv) {
        zoneDiv.remove();
    }
}

/**
 * 加载Zone配置到表单
 */
function loadZonesConfigToForm(zonesConfig) {
    // 清空现有配置
    document.getElementById('zonesConfigContainer').innerHTML = '';
    zoneConfigCounter = 0;

    if (!zonesConfig || zonesConfig.length === 0) {
        return;
    }

    // 加载每个Zone配置
    zonesConfig.forEach((zone, index) => {
        zoneConfigCounter++;
        addZoneConfig();

        // 填充数据
        document.getElementById(`zoneName${zoneConfigCounter}`).value = zone.name || '';
        document.getElementById(`zonePolygon${zoneConfigCounter}`).value = 
            (zone.polygon || []).map(p => `${p[0]},${p[1]}`).join(';');
        document.getElementById(`zoneRequired${zoneConfigCounter}`).value = zone.required ? 'true' : 'false';
        document.getElementById(`zoneLoitering${zoneConfigCounter}`).value = zone.enable_loitering_detection ? 'true' : 'false';
        document.getElementById(`zoneLoiteringTime${zoneConfigCounter}`).value = zone.loitering_time || 30;
        document.getElementById(`zoneColor${zoneConfigCounter}`).value = 
            (zone.color || [255, 0, 0]).join(',');
    });
}

/**
 * 从表单获取Zone配置
 */
function getZonesConfigFromForm() {
    const zonesConfig = [];
    const container = document.getElementById('zonesConfigContainer');
    if (!container) {
        return zonesConfig;
    }
    const zoneItems = container.querySelectorAll('.zone-config-item');

    zoneItems.forEach((item, index) => {
        const counter = parseInt(item.id.replace('zoneConfig', ''));

        const name = document.getElementById(`zoneName${counter}`).value.trim();
        const polygonStr = document.getElementById(`zonePolygon${counter}`).value.trim();

        if (!name || !polygonStr) {
            return;  // 跳过空配置
        }

        // 解析多边形坐标
        const polygon = polygonStr.split(';').map(point => {
            const [x, y] = point.split(',').map(Number);
            return [x, y];
        });

        // 解析颜色
        const colorStr = document.getElementById(`zoneColor${counter}`).value.trim();
        const color = colorStr ? colorStr.split(',').map(Number) : [255, 0, 0];

        zonesConfig.push({
            name: name,
            polygon: polygon,
            required: document.getElementById(`zoneRequired${counter}`).value === 'true',
            color: color,
            min_area: 0,
            loitering_time: parseInt(document.getElementById(`zoneLoiteringTime${counter}`).value) || 30,
            enable_loitering_detection: document.getElementById(`zoneLoitering${counter}`).value === 'true'
        });
    });

    return zonesConfig;
}