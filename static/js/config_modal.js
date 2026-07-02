// ========================================
// 参数配置功能
// ========================================

// 当前配置的任务ID
let currentConfigTaskId = null;

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

        // 显示模态框
        document.getElementById('configModal').classList.add('show');

    } catch (error) {
        console.error('打开配置模态框失败:', error);
        showNotification('打开配置模态框失败', 'error');
    }
}

// 加载配置到表单
function loadConfigToForm(task) {
    // 解码器配置（完整配置）
    const decoderConfig = task.decoder_config || {};
    document.getElementById('decoderWidth').value = decoderConfig.width || 640;
    document.getElementById('decoderHeight').value = decoderConfig.height || 640;
    document.getElementById('decoderHwaccel').value = decoderConfig.hwaccel || 'dxva2';
    document.getElementById('decoderOutputFormat').value = decoderConfig.output_format || 'auto';
    document.getElementById('decoderRtspTransport').value = decoderConfig.rtsp_transport || 'tcp';
    document.getElementById('decoderReconnect').value = decoderConfig.reconnect ? 'true' : 'false';
    document.getElementById('decoderReconnectDelay').value = decoderConfig.reconnect_delay || 5;
    document.getElementById('decoderBufferSize').value = decoderConfig.buffer_size || 1048576;
    document.getElementById('decoderFrameQueueSize').value = decoderConfig.frame_queue_size || 30;
    document.getElementById('decoderUseRingBuffer').value = decoderConfig.use_ring_buffer ? 'true' : 'false';
    document.getElementById('decoderRingBufferBlockCount').value = decoderConfig.ring_buffer_block_count || 5;
    document.getElementById('decoderIndexQueueSize').value = decoderConfig.index_queue_size || 30;
    
    // 共享内存配置（新增）
    const sharedMemoryTotalSize = decoderConfig.shared_memory_total_size;
    if (sharedMemoryTotalSize === null || sharedMemoryTotalSize === undefined) {
        document.getElementById('decoderSharedMemoryTotalSize').value = 'null';
    } else {
        document.getElementById('decoderSharedMemoryTotalSize').value = sharedMemoryTotalSize;
    }
    document.getElementById('decoderSharedMemoryMaxRetries').value = decoderConfig.shared_memory_max_retries || 3;

    // 检测器配置（完整配置）
    const detectorConfig = task.detector_config || {};
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

    // 事件输出配置
    const eventConfig = task.event_config || {};
    const imageOutputObj = eventConfig.image_output || {};
    const filterObj = eventConfig.filter || {};
    const gifObj = eventConfig.gif || {};

    document.getElementById('eventEnabled').value = eventConfig.enabled !== false ? 'true' : 'false';  // 默认启用

    document.getElementById('eventImageMethod').value = imageOutputObj.method || 'both';
    document.getElementById('eventLocalBackup').value = imageOutputObj.local_backup ? 'true' : 'false';
    document.getElementById('eventImageOutputDir').value = imageOutputObj.output_dir || 'output/images';
    document.getElementById('eventMinConfidence').value = filterObj.min_confidence || 0.5;
    document.getElementById('eventGifOutputDir').value = gifObj.output_dir || 'output/gifs';

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
    document.getElementById('displayEnabled').value = displayObj.enabled !== false ? 'true' : 'false';
    document.getElementById('displayTargetFps').value = displayObj.target_fps || 15;
    document.getElementById('displayAIStreamEnabled').value = displayObj.ai_stream_enabled !== false ? 'true' : 'false';
    document.getElementById('displayAIStreamInterval').value = displayObj.ai_stream_interval || 2;
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
        // 获取共享内存大小（特殊处理null值）
        const sharedMemoryTotalSizeStr = document.getElementById('decoderSharedMemoryTotalSize').value.trim();
        let sharedMemoryTotalSize = null;
        if (sharedMemoryTotalSizeStr !== 'null' && sharedMemoryTotalSizeStr !== '') {
            sharedMemoryTotalSize = parseInt(sharedMemoryTotalSizeStr);
        }

        // 构建配置数据
        const configData = {
            // 解码器配置（完整配置）
            decoder_config: {
                width: parseInt(document.getElementById('decoderWidth').value) || 640,
                height: parseInt(document.getElementById('decoderHeight').value) || 640,
                hwaccel: document.getElementById('decoderHwaccel').value || 'dxva2',
                output_format: document.getElementById('decoderOutputFormat').value || 'auto',
                rtsp_transport: document.getElementById('decoderRtspTransport').value || 'tcp',
                reconnect: document.getElementById('decoderReconnect').value === 'true',
                reconnect_delay: parseInt(document.getElementById('decoderReconnectDelay').value) || 5,
                buffer_size: parseInt(document.getElementById('decoderBufferSize').value) || 1048576,
                frame_queue_size: parseInt(document.getElementById('decoderFrameQueueSize').value) || 30,
                use_ring_buffer: document.getElementById('decoderUseRingBuffer').value === 'true',
                ring_buffer_block_count: parseInt(document.getElementById('decoderRingBufferBlockCount').value) || 5,
                index_queue_size: parseInt(document.getElementById('decoderIndexQueueSize').value) || 30,
                shared_memory_total_size: sharedMemoryTotalSize,  // null表示自动计算
                shared_memory_max_retries: parseInt(document.getElementById('decoderSharedMemoryMaxRetries').value) || 3
            },

            // 检测器配置（完整配置）
            detector_config: {
                backend: document.getElementById('detectorBackend').value || 'onnx_tensorrt',
                use_fp16: document.getElementById('detectorUseFp16').value === 'true',
                conf_threshold: parseFloat(document.getElementById('detectorConfThreshold').value) || 0.25,
                iou_threshold: parseFloat(document.getElementById('detectorIouThreshold').value) || 0.35,
                min_box_area: parseInt(document.getElementById('detectorMinBoxArea').value) || 50,
                device_id: parseInt(document.getElementById('detectorDeviceId').value) || 0,

                // ✅ 显存限制（MB转换为字节）
                gpu_mem_limit: (parseInt(document.getElementById('detectorGpuMemLimit').value) || 6144) * 1024 * 1024,

                // ✅ 高级配置项（自动补充默认值，不从前端读取）
                input_width: 640,
                input_height: 640,
                tensorrt_engine_cache_path: './trt_cache',
                tensorrt_lib_path: 'D:\\TensorRT-10.5.0.18.Windows.win10.cuda-12.6\\TensorRT-10.5.0.18\\lib',

                // ✅ 预处理器配置（固定值，不从前端读取）
                width: 640,
                height: 640,
                model_input_size: 640,
                rgb_mean: [0.0, 0.0, 0.0],
                rgb_std: [1.0, 1.0, 1.0]

                // 注意：target_classes是任务表的单独字段，不放在detector_config中
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

                // ✅ 固定配置项（不从前端读取）
                detect_shadows: false,
                blur_size: 5,
                morph_size: 5,
                learning_rate: -1,
                motion_history_length: 10,
                init_threshold: 30,
                foreground_threshold: 0.3,
                max_motion_regions: 20,
                show_visualization: false,  // ✅ 新增：固定（禁用可视化）
                use_for_reid: true  // ✅ 新增：固定（启用ReID辅助）
            },

            // 事件输出配置
            event_config: {
                enabled: document.getElementById('eventEnabled').value === 'true',
                // 注意：storage、mqtt、websocket属于系统配置，从system_config.yaml读取，不在event_config中保存

                metadata: {},  // 固定（空）

                // 图片压缩配置
                compression: {
                    enabled: true,
                    quality: 80,
                    max_width: 640,
                    max_height: 480
                },

                // 图片输出配置
                image_output: {
                    method: document.getElementById('eventImageMethod').value || 'both',
                    minio_bucket: 'sagesight-images',
                    ftp_remote_dir: '/sagesight/images',
                    local_backup: document.getElementById('eventLocalBackup').value === 'true',
                    output_dir: document.getElementById('eventImageOutputDir').value || 'output/images',
                    local_retention_days: 7,
                    auto_cleanup: true,
                    cleanup_interval_hours: 24
                },

                // GIF输出配置
                gif: {
                    output_method: 'both',
                    output_dir: document.getElementById('eventGifOutputDir').value || 'output/gifs',
                    local_retention_days: 7,
                    auto_cleanup: true,
                    cleanup_interval_hours: 24,
                    max_frames: 30,
                    fps: 5,
                    max_width: 320,
                    max_height: 240,
                    draw_trajectory: true,
                    trajectory_color: [0, 255, 0],
                    minio_bucket: 'sagesight-gifs',
                    ftp_remote_dir: '/gifs'
                },

                trajectory: {},  // 固定（空）
                capture: {},  // 固定（空）

                // 过滤配置
                filter: {
                    target_classes: ['person', 'car'],  // 固定
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
                    ai_stream_interval: parseInt(document.getElementById('displayAIStreamInterval').value) || 2
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
                motion_region: {
                    color: [255, 255, 0],
                    thickness: 1,
                    fill_color: [255, 255, 0],
                    fill_alpha: 50
                },
                debug_info: {
                    show_fps: true,
                    show_detection_count: true,
                    show_track_count: true,
                    show_frame_id: true,
                    fps_position: [10, 30],
                    font_scale: 0.6,
                    text_color: [255, 255, 255]
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