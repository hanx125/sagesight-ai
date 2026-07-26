/**
 * ONVIF语音对讲功能 - 标准Audio Back Channel方案
 * 
 * 实现流程：
 * 1. 检测摄像机Audio Back Channel能力
 * 2. 启动ONVIF语音对讲会话（建立RTSP Audio Back Channel）
 * 3. 使用麦克风录音（Web Audio API）
 * 4. 实时发送PCM音频数据到后端（通过WebSocket或POST）
 * 5. 后端通过RTSP Audio Back Channel发送到摄像机扬声器
 * 
 * 核心优势：
 * - 标准ONVIF协议，跨品牌兼容
 * - 无需WebRTC复杂信令
 * - 直接音频数据传输，延迟更低
 */

// 全局变量
let audioTalkLocalStream = null;     // 本地音频流（麦克风）
let audioTalkSessionId = null;       // ONVIF会话ID
let audioTalkActive = false;         // 对讲状态
let audioContext = null;             // AudioContext
let audioScriptProcessor = null;     // ScriptProcessorNode（音频处理）
let audioWebSocket = null;           // WebSocket连接（实时音频传输）
let audioTalkInterval = null;        // 音频发送定时器（POST方式）
let audioDataBuffer = [];            // 音频数据缓存（POST方式）
let audioBufferMaxSize = 100;        // 最大缓存数量（约200ms音频）

// 音频增强控制变量
let audioGainNode = null;            // GainNode（音量控制）
let audioVolume = 1.0;               // 音量值（0-2.0）
let noiseSuppressionEnabled = true;  // 噪音抑制启用状态
let echoCancellationEnabled = true;  // 回声消除启用状态

/**
 * 检测语音对讲能力
 */
function checkAudioTalkCapabilities() {
    if (!currentSingleCamera) {
        console.error('未选择摄像机');
        return;
    }
    
    fetch(`/api/audio/talk/capabilities?camera_id=${currentSingleCamera.id}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                console.log('语音对讲能力:', data.capabilities);
                
                if (data.capabilities.audio_backchannel_supported) {
                    updateAudioTalkInfo('✓ 该摄像机支持ONVIF Audio Back Channel');
                    // 启用开始按钮
                    document.getElementById('audioTalkStartBtn').disabled = false;
                } else {
                    updateAudioTalkInfo('⚠ 该摄像机不支持Audio Back Channel');
                    disableAudioTalkButtons();
                }
            } else {
                console.error('检测语音对讲能力失败:', data.error);
                updateAudioTalkInfo('✗ 检测语音对讲能力失败');
            }
        })
        .catch(error => {
            console.error('检测语音对讲能力请求失败:', error);
            updateAudioTalkInfo('✗ 网络请求失败');
        });
}

/**
 * 开始语音对讲（ONVIF方案）
 */
async function startAudioTalk() {
    try {
        if (!currentSingleCamera) {
            console.error('未选择摄像机');
            updateAudioTalkInfo('请先选择摄像机');
            return;
        }
        
        console.log('开始ONVIF语音对讲: camera_id=', currentSingleCamera.id);
        updateAudioTalkInfo('正在初始化...');
        
        // 步骤1: 检查麦克风设备
        console.log('检查音频设备...');
        updateAudioTalkInfo('检查音频设备...');
        
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const audioInputDevices = devices.filter(device => device.kind === 'audioinput');
            
            console.log('发现音频输入设备:', audioInputDevices.length);
            
            if (audioInputDevices.length === 0) {
                console.error('没有发现麦克风设备');
                updateAudioTalkInfo('✗ 未发现麦克风设备，请检查设备连接');
                showAudioTalkTip('未发现麦克风设备，请检查设备是否正确连接', 'error');
                return;
            }
            
            // 显示设备信息
            audioInputDevices.forEach((device, index) => {
                console.log(`  麦克风 #${index + 1}: ${device.label || '未命名设备'} (deviceId: ${device.deviceId})`);
            });
            
        } catch (enumerateError) {
            console.error('枚举设备失败:', enumerateError);
            updateAudioTalkInfo('✗ 无法检测音频设备');
            showAudioTalkTip('无法检测音频设备，请检查浏览器权限设置', 'error');
            return;
        }
        
        // 步骤2: 请求麦克风权限
        console.log('请求麦克风权限...');
        updateAudioTalkInfo('请求麦克风权限...');
        
        try {
            audioTalkLocalStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: echoCancellationEnabled,    // 回声消除（可动态控制）
                    noiseSuppression: noiseSuppressionEnabled,    // 噪音抑制（可动态控制）
                    autoGainControl: true,      // 自动增益
                    sampleRate: 8000,           // 采样率8000 Hz（ONVIF标准）
                    channelCount: 1             // 单声道
                },
                video: false
            });
            
            console.log('✓ 麦克风权限已获取');
            updateAudioTalkInfo('麦克风权限已获取');
            
        } catch (mediaError) {
            console.error('获取麦克风失败:', mediaError.name, mediaError.message);
            
            let errorTip = '获取麦克风失败';
            
            if (mediaError.name === 'NotFoundError') {
                errorTip = '未发现麦克风设备，请检查设备是否正确连接';
            } else if (mediaError.name === 'NotAllowedError' || mediaError.name === 'PermissionDeniedError') {
                errorTip = '麦克风权限被拒绝，请在浏览器设置中允许访问麦克风';
            } else if (mediaError.name === 'NotReadableError') {
                errorTip = '麦克风设备被占用，请关闭其他使用麦克风的应用';
            }
            
            updateAudioTalkInfo('✗ ' + errorTip);
            showAudioTalkTip(errorTip, 'error');
            return;
        }
        
        // 步骤3: 启动ONVIF语音对讲会话
        console.log('启动ONVIF语音对讲会话...');
        updateAudioTalkInfo('建立Audio Back Channel...');
        
        const startResponse = await fetch('/api/audio/talk/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                camera_id: currentSingleCamera.id,
                audio_codec: 'PCMU',  // 使用PCMU编码
                sample_rate: 8000,
                volume_gain: audioVolume,  // 音量增益（从全局变量获取）
                noise_suppression: noiseSuppressionEnabled,  // 噪音抑制
                echo_cancellation: echoCancellationEnabled  // 回声消除
            })
        });
        
        const startData = await startResponse.json();
        
        if (!startData.success) {
            console.error('启动ONVIF语音对讲失败:', startData.error);
            updateAudioTalkInfo('✗ 启动失败');
            showAudioTalkTip('无法建立Audio Back Channel', 'error');
            return;
        }
        
        console.log('✓ ONVIF语音对讲会话已启动:', startData);
        console.log('  Session ID:', startData.session_id);
        console.log('  Camera IP:', startData.camera_ip);
        
        // 保存会话ID
        audioTalkSessionId = startData.session_id;
        
        // 步骤4: 创建AudioContext和音频处理器
        console.log('创建音频处理器...');
        updateAudioTalkInfo('初始化音频处理...');

        audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: 8000  // ONVIF标准采样率
        });

        const source = audioContext.createMediaStreamSource(audioTalkLocalStream);

        // 创建GainNode（音量控制）
        audioGainNode = audioContext.createGain();
        audioGainNode.gain.value = audioVolume;  // 设置初始音量

        // 创建ScriptProcessorNode（处理音频数据）
        // bufferSize: 2048（每20ms左右处理一次）
        audioScriptProcessor = audioContext.createScriptProcessor(2048, 1, 1);

        // 音频处理事件（每次有音频数据时触发）
        audioScriptProcessor.onaudioprocess = (event) => {
            // 获取音频数据（Float32格式）
            const inputBuffer = event.inputBuffer;
            const inputData = inputBuffer.getChannelData(0);

            // 计算音量等级（RMS值）- 无论是否激活都要计算，用于实时显示
            let sum = 0;
            for (let i = 0; i < inputData.length; i++) {
                sum += inputData[i] * inputData[i];
            }
            const rms = Math.sqrt(sum / inputData.length);

            // 将RMS值映射到0-5的等级（考虑音量增益）
            // Float32数据的范围是-1.0到1.0，RMS通常在0.001-0.1之间
            // 使用非线性映射 + 大幅放大系数来提高灵敏度
            const normalizedVolume = rms * audioVolume;

            // 使用平方根放大（提高低音量的灵敏度）
            const amplifiedVolume = Math.sqrt(normalizedVolume) * 80;

            // 映射到0-5的等级
            const volumeLevel = Math.floor(amplifiedVolume);

            // 更新音量指示器（5个音量条）
            updateAudioLevelIndicator(Math.min(Math.max(volumeLevel, 0), 5));

            // 只在对讲激活时才发送数据
            if (!audioTalkActive) {
                return;
            }

            // 转换为16位PCM
            const pcmData = float32ToPCM16(inputData);

            // 发送PCM数据到后端（通过WebSocket或POST）
            sendAudioDataToBackend(pcmData);
        };

        // 连接音频节点链：source → gainNode → scriptProcessor → destination
        source.connect(audioGainNode);
        audioGainNode.connect(audioScriptProcessor);
        audioScriptProcessor.connect(audioContext.destination);
        
        console.log('✓ 音频处理器已创建');
        
        // 步骤5: 启动WebSocket连接（实时音频传输）
        console.log('建立WebSocket连接...');
        updateAudioTalkInfo('建立实时传输通道...');
        
        try {
            const wsUrl = `ws://${window.location.host}/api/audio/talk/ws`;
            
            audioWebSocket = new WebSocket(wsUrl);
            
            audioWebSocket.onopen = () => {
                console.log('✓ WebSocket连接已建立');
                
                // 发送会话ID
                audioWebSocket.send(JSON.stringify({
                    type: 'session_start',
                    session_id: audioTalkSessionId
                }));
                
                // 标记为激活状态
                audioTalkActive = true;
                updateAudioTalkStatus('active');
                updateAudioTalkInfo('✓ 语音对讲已开始');
                showAudioTalkTip('语音对讲已开始（ONVIF Audio Back Channel）', 'success');
                
                // 更新按钮状态
                document.getElementById('audioTalkStartBtn').disabled = true;
                document.getElementById('audioTalkStopBtn').disabled = false;
            };
            
            audioWebSocket.onerror = (error) => {
                console.error('WebSocket错误:', error);
                updateAudioTalkInfo('✗ WebSocket连接失败');
                showAudioTalkTip('实时传输通道建立失败，将使用HTTP POST方式', 'warning');
                
                // 切换到POST方式
                startAudioPOSTMode();
            };
            
            audioWebSocket.onclose = () => {
                console.log('WebSocket连接已关闭');
                
                if (audioTalkActive) {
                    updateAudioTalkInfo('实时传输通道已断开');
                }
            };
            
        } catch (wsError) {
            console.error('创建WebSocket失败:', wsError);
            console.log('切换到HTTP POST方式');
            
            // 切换到POST方式
            startAudioPOSTMode();
        }
        
    } catch (error) {
        console.error('开始语音对讲失败:', error);
        updateAudioTalkInfo('✗ 启动失败');
        showAudioTalkTip('语音对讲启动失败: ' + error.message, 'error');
    }
}

/**
 * Float32转16位PCM
 */
function float32ToPCM16(float32Data) {
    const pcm16Data = new Int16Array(float32Data.length);
    
    for (let i = 0; i < float32Data.length; i++) {
        // 将Float32（-1.0到1.0）转换为Int16（-32768到32767）
        const s = Math.max(-1, Math.min(1, float32Data[i]));
        pcm16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    
    return pcm16Data;
}

/**
 * 发送音频数据到后端（WebSocket方式或缓存到buffer）
 */
function sendAudioDataToBackend(pcmData) {
    // 如果WebSocket可用，直接发送
    if (audioWebSocket && audioWebSocket.readyState === WebSocket.OPEN) {
        // 发送PCM数据（二进制）
        audioWebSocket.send(pcmData.buffer);
        console.log('✓ WebSocket发送音频数据:', pcmData.length, '采样');
    } else {
        // WebSocket不可用，缓存到buffer（用于HTTP POST）
        audioDataBuffer.push(pcmData);
        
        // 控制buffer大小，避免内存溢出
        if (audioDataBuffer.length > audioBufferMaxSize) {
            // 移除最旧的数据
            audioDataBuffer.shift();
        }
        
        console.log('音频数据已缓存:', audioDataBuffer.length, '/', audioBufferMaxSize);
    }
}

/**
 * 启动HTTP POST音频传输模式
 */
function startAudioPOSTMode() {
    console.log('启动HTTP POST音频传输模式');
    
    // 标记为激活状态（使用POST模式）
    audioTalkActive = true;
    updateAudioTalkStatus('active');
    updateAudioTalkInfo('语音对讲已开始（HTTP POST模式）');
    showAudioTalkTip('语音对讲已开始（HTTP POST模式）', 'success');
    
    // 更新按钮状态
    document.getElementById('audioTalkStartBtn').disabled = true;
    document.getElementById('audioTalkStopBtn').disabled = false;
    
    // 每100ms发送一次缓存的音频数据
    audioTalkInterval = setInterval(() => {
        if (!audioTalkActive || audioDataBuffer.length === 0) {
            return;
        }
        
        // 合并缓存的音频数据
        const mergedPCMData = mergeAudioBuffer();
        
        // 发送到后端（HTTP POST）
        sendAudioDataViaHTTP(mergedPCMData);
        
    }, 100);  // 每100ms发送一次（比WebSocket稍慢，但仍然实时）
}

/**
 * 合并缓存的音频数据
 */
function mergeAudioBuffer() {
    if (audioDataBuffer.length === 0) {
        return null;
    }
    
    // 计算总长度
    let totalLength = 0;
    for (const chunk of audioDataBuffer) {
        totalLength += chunk.length;
    }
    
    // 合并所有数据
    const mergedData = new Int16Array(totalLength);
    let offset = 0;
    
    for (const chunk of audioDataBuffer) {
        mergedData.set(chunk, offset);
        offset += chunk.length;
    }
    
    // 清空buffer
    audioDataBuffer = [];
    
    return mergedData;
}

/**
 * 通过HTTP POST发送音频数据到后端
 */
async function sendAudioDataViaHTTP(pcmData) {
    if (!pcmData || !audioTalkSessionId) {
        return;
    }
    
    try {
        // 转换为Base64编码（JSON格式发送）
        const base64Data = arrayBufferToBase64(pcmData.buffer);
        
        const response = await fetch('/api/audio/talk/send', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                session_id: audioTalkSessionId,
                audio_data: base64Data
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            console.log('✓ HTTP POST发送成功:', pcmData.length, '采样');
        } else {
            console.error('HTTP POST发送失败:', result.error);
        }
        
    } catch (error) {
        console.error('HTTP POST请求失败:', error);
    }
}

/**
 * ArrayBuffer转Base64
 */
function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    
    return window.btoa(binary);
}

/**
 * 停止语音对讲
 */
async function stopAudioTalk() {
    try {
        console.log('停止ONVIF语音对讲');
        
        // 标记为非激活状态
        audioTalkActive = false;
        updateAudioTalkStatus('inactive');
        updateAudioTalkInfo('正在停止...');
        
        // 停止音频处理器
        if (audioScriptProcessor) {
            audioScriptProcessor.disconnect();
            audioScriptProcessor = null;
        }
        
        // 关闭AudioContext
        if (audioContext) {
            audioContext.close();
            audioContext = null;
        }
        
        // 关闭WebSocket
        if (audioWebSocket) {
            audioWebSocket.close();
            audioWebSocket = null;
        }
        
        // 停止定时器
        if (audioTalkInterval) {
            clearInterval(audioTalkInterval);
            audioTalkInterval = null;
        }
        
        // 清空音频缓存
        audioDataBuffer = [];
        
        // 停止本地音频流
        if (audioTalkLocalStream) {
            const tracks = audioTalkLocalStream.getTracks();
            tracks.forEach(track => track.stop());
            audioTalkLocalStream = null;
        }
        
        // 关闭ONVIF会话
        if (audioTalkSessionId) {
            const stopResponse = await fetch('/api/audio/talk/stop', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    session_id: audioTalkSessionId
                })
            });
            
            const stopData = await stopResponse.json();
            
            if (stopData.success) {
                console.log('✓ ONVIF会话已关闭');
            } else {
                console.error('关闭ONVIF会话失败:', stopData.error);
            }
            
            audioTalkSessionId = null;
        }
        
        // 更新状态
        updateAudioTalkInfo('语音对讲已停止');
        showAudioTalkTip('语音对讲已停止', 'info');

        // 清除音量指示器
        updateAudioLevelIndicator(0);
        
        // 更新按钮状态
        document.getElementById('audioTalkStartBtn').disabled = false;
        document.getElementById('audioTalkStopBtn').disabled = true;
        
    } catch (error) {
        console.error('停止语音对讲失败:', error);
        updateAudioTalkInfo('✗ 停止失败');
        showAudioTalkTip('停止语音对讲失败: ' + error.message, 'error');
    }
}

/**
 * 更新语音对讲状态显示
 */
function updateAudioTalkStatus(status) {
    const statusIconElement = document.getElementById('audioTalkStatusIcon');
    const statusTextElement = document.getElementById('audioTalkStatusText');
    
    if (statusIconElement) {
        if (status === 'active') {
            statusIconElement.className = 'fa fa-microphone';
            statusIconElement.style.color = '#00ff00';
        } else if (status === 'inactive') {
            statusIconElement.className = 'fa fa-microphone-slash';
            statusIconElement.style.color = '#888888';
        }
    }
    
    if (statusTextElement) {
        if (status === 'active') {
            statusTextElement.textContent = '正在对讲';
            statusTextElement.style.color = '#00ff00';
        } else if (status === 'inactive') {
            statusTextElement.textContent = '未连接';
            statusTextElement.style.color = '#888888';
        }
    }
}

/**
 * 更新语音对讲信息显示
 */
function updateAudioTalkInfo(message) {
    const infoElement = document.getElementById('audioTalkInfo');

    if (infoElement) {
        infoElement.textContent = message;
    }
}

/**
 * 更新音量指示器（实时显示麦克风输入音量）
 * @param {number} level - 音量等级（0-5）
 */
function updateAudioLevelIndicator(level) {
    // 获取5个音量条元素
    const bars = [
        document.getElementById('audioLevelBar1'),
        document.getElementById('audioLevelBar2'),
        document.getElementById('audioLevelBar3'),
        document.getElementById('audioLevelBar4'),
        document.getElementById('audioLevelBar5')
    ];

    // 更新每个音量条的激活状态
    bars.forEach((bar, index) => {
        if (bar) {
            if (index < level) {
                // 激活：添加active类
                bar.classList.add('active');

                // 根据位置设置颜色（渐变效果）
                const colors = [
                    '#4CAF50', // 第1条：绿色（低音量）
                    '#8BC34A', // 第2条：浅绿
                    '#FFC107', // 第3条：黄色（中等音量）
                    '#FF9800', // 第4条：橙色
                    '#F44336'  // 第5条：红色（高音量）
                ];
                bar.style.background = colors[index];

                // 根据激活的条数设置高度（前面的条更短）
                const heights = [8, 12, 16, 20, 24];
                bar.style.height = heights[index] + 'px';
            } else {
                // 未激活：移除active类，恢复默认状态
                bar.classList.remove('active');
                bar.style.background = 'rgba(220, 53, 69, 0.2)';
                bar.style.height = '24px'; // 恢复默认高度
            }
        }
    });
}

/**
 * 显示语音对讲提示
 */
function showAudioTalkTip(message, type = 'info') {
    const infoElement = document.getElementById('audioTalkInfo');
    
    if (infoElement) {
        infoElement.textContent = message;
        
        // 设置提示类型样式
        if (type === 'success') {
            infoElement.style.color = '#00ff00';
        } else if (type === 'error') {
            infoElement.style.color = '#ff0000';
        } else if (type === 'warning') {
            infoElement.style.color = '#ffff00';
        } else {
            infoElement.style.color = '#ffffff';
        }
        
        // 5秒后恢复默认信息
        setTimeout(() => {
            if (audioTalkActive) {
                updateAudioTalkInfo('语音对讲正在进行...');
            } else {
                updateAudioTalkInfo('点击"开始对讲"建立连接');
            }
        }, 5000);
    }
    
    // 同时在控制台输出
    console.log(`[语音对讲提示] ${type}: ${message}`);
}

/**
 * 禁用语音对讲按钮
 */
function disableAudioTalkButtons() {
    const startBtn = document.getElementById('audioTalkStartBtn');
    const stopBtn = document.getElementById('audioTalkStopBtn');
    
    if (startBtn) {
        startBtn.disabled = true;
    }
    
    if (stopBtn) {
        stopBtn.disabled = true;
    }
}

/**
 * 更新音频音量
 * @param {number} volumeValue - 音量值（0-200）
 */
function updateAudioVolume(volumeValue) {
    // 转换为0-2.0范围
    audioVolume = volumeValue / 100.0;

    // 更新UI显示
    const volumeDisplay = document.getElementById('audioVolumeValue');
    if (volumeDisplay) {
        volumeDisplay.textContent = volumeValue + '%';
    }

    // 更新GainNode音量（如果已创建）
    if (audioGainNode) {
        audioGainNode.gain.value = audioVolume;
        console.log('✓ 音量已更新:', audioVolume.toFixed(2));
    }
}

/**
 * 切换噪音抑制
 * @param {boolean} enabled - 是否启用噪音抑制
 */
function toggleNoiseSuppression(enabled) {
    noiseSuppressionEnabled = enabled;

    // 更新UI状态
    const statusElement = document.getElementById('noiseSuppressionStatus');
    if (statusElement) {
        statusElement.textContent = enabled ? '已启用' : '已禁用';
    }

    console.log('✓ 噪音抑制已' + (enabled ? '启用' : '禁用'));

    // 如果语音对讲正在进行，需要重新启动才能生效
    if (audioTalkActive) {
        showAudioTalkTip('噪音抑制设置将在下次开始对讲时生效', 'warning');
    }
}

/**
 * 切换回声消除
 * @param {boolean} enabled - 是否启用回声消除
 */
function toggleEchoCancellation(enabled) {
    echoCancellationEnabled = enabled;

    // 更新UI状态
    const statusElement = document.getElementById('echoCancellationStatus');
    if (statusElement) {
        statusElement.textContent = enabled ? '已启用' : '已禁用';
    }

    console.log('✓ 回声消除已' + (enabled ? '启用' : '禁用'));

    // 如果语音对讲正在进行，需要重新启动才能生效
    if (audioTalkActive) {
        showAudioTalkTip('回声消除设置将在下次开始对讲时生效', 'warning');
    }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    console.log('ONVIF语音对讲模块已加载');

    // ✅ 修复：检查变量是否已定义
    if (typeof currentSingleCamera !== 'undefined' && currentSingleCamera) {
        checkAudioTalkCapabilities();
    }
});

/**
 * 初始化语音对讲功能（供monitor.js调用）
 */
function initAudioTalk() {
    console.log('初始化ONVIF语音对讲功能...');

    // 检测语音对讲能力
    // ✅ 修复：检查变量是否已定义
    if (typeof currentSingleCamera !== 'undefined' && currentSingleCamera) {
        checkAudioTalkCapabilities();
    }

    // 初始化UI状态
    updateAudioTalkStatus('inactive');
    updateAudioTalkInfo('点击"开始对讲"建立连接');

    // 初始化按钮状态
    const startBtn = document.getElementById('audioTalkStartBtn');
    const stopBtn = document.getElementById('audioTalkStopBtn');

    if (startBtn) {
        startBtn.disabled = false;
    }

    if (stopBtn) {
        stopBtn.disabled = true;
    }
}