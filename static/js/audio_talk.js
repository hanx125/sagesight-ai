/**
 * WebRTC语音对讲功能
 * 
 * 支持两种实现方案：
 * 1. ZLM WebRTC方案（推荐）：使用ZLMediaKit作为WebRTC中继服务器
 *    - 前端推流到ZLM（type=push）
 *    - 前端从ZLM拉流播放（type=play）
 *    - 成熟的流媒体服务器，避免ICE问题
 * 
 * 2. aiortc方案（旧方案）：使用aiortc作为WebRTC服务器
 *    - 服务器创建Offer，客户端Answer
 *    - 直接PeerConnection，但Windows ICE问题较多
 * 
 * ZLM WebRTC信令流程：
 * 1. 调用/api/audio/talk/start-zlm获取推流和拉流URL
 * 2. 使用ZLMRTCClient推流到ZLM（麦克风）
 * 3. 使用ZLMRTCClient从ZLM拉流播放（摄像机音频）
 */

// 全局变量
let audioTalkPeerConnection = null;  // WebRTC PeerConnection
let audioTalkLocalStream = null;     // 本地音频流（麦克风）
let audioTalkRemoteStream = null;    // 远程音频流（摄像机）
let audioTalkSessionId = null;       // 会话ID
let audioTalkActive = false;         // 对讲状态
let audioTalkWebSocket = null;       // WebSocket连接（可选）
let audioLevelCheckInterval = null;  // 音频级别检测定时器
let audioTalkZlmPushClient = null;   // ZLM WebRTC推流客户端（新方案）
let audioTalkZlmPlayClient = null;   // ZLM WebRTC播放客户端（新方案）

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
                
                if (data.capabilities.webrtc_supported) {
                    updateAudioTalkInfo('✓ 该摄像机支持WebRTC语音对讲');
                } else {
                    updateAudioTalkInfo('⚠ 该摄像机不支持WebRTC语音对讲');
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
 * 开始语音对讲
 */
async function startAudioTalk() {
    try {
        if (!currentSingleCamera) {
            console.error('未选择摄像机');
            updateAudioTalkInfo('请先选择摄像机');
            return;
        }
        
        console.log('开始语音对讲: camera_id=', currentSingleCamera.id);
        updateAudioTalkInfo('正在初始化...');
        
        // 步骤0: 检查设备可用性
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
        
        // 步骤1: 请求麦克风权限
        console.log('请求麦克风权限...');
        updateAudioTalkInfo('请求麦克风权限...');
        
        try {
            audioTalkLocalStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,    // 回声消除
                    noiseSuppression: true,     // 噪音抑制
                    autoGainControl: true,      // 自动增益
                    sampleRate: 8000,           // 采样率8000 Hz（ONVIF标准）
                    channelCount: 1             // 单声道
                },
                video: false  // 明确指定不需要视频
            });
            
            console.log('✓ 麦克风权限已获取');
            updateAudioTalkInfo('麦克风权限已获取');
            
        } catch (mediaError) {
            console.error('获取麦克风失败:', mediaError.name, mediaError.message);
            
            // 根据错误类型提供不同的提示
            let errorTip = '获取麦克风失败';
            
            if (mediaError.name === 'NotFoundError') {
                errorTip = '未发现麦克风设备，请检查设备是否正确连接';
            } else if (mediaError.name === 'NotAllowedError' || mediaError.name === 'PermissionDeniedError') {
                errorTip = '麦克风权限被拒绝，请在浏览器设置中允许访问麦克风';
            } else if (mediaError.name === 'NotReadableError') {
                errorTip = '麦克风设备被占用，请关闭其他使用麦克风的应用';
            } else if (mediaError.name === 'OverconstrainedError') {
                errorTip = '麦克风设备不支持所需配置，将尝试使用默认配置';
                
                // 尝试使用更简单的配置
                try {
                    audioTalkLocalStream = await navigator.mediaDevices.getUserMedia({
                        audio: true,  // 使用默认音频配置
                        video: false
                    });
                    
                    console.log('✓ 使用默认配置成功获取麦克风');
                    updateAudioTalkInfo('麦克风权限已获取（默认配置）');
                    
                } catch (fallbackError) {
                    console.error('默认配置也失败:', fallbackError);
                    updateAudioTalkInfo('✗ 无法获取麦克风');
                    showAudioTalkTip(errorTip, 'error');
                    return;
                }
            }
            
            if (!audioTalkLocalStream) {
                updateAudioTalkInfo('✗ ' + errorTip);
                showAudioTalkTip(errorTip, 'error');
                return;
            }
        }
        
        // 步骤2: 启动ZLM语音对讲（获取推流和拉流URL）
        console.log('启动ZLM语音对讲...');
        updateAudioTalkInfo('启动ZLM语音对讲...');
        
        const zlmResponse = await fetch('/api/audio/talk/start-zlm', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                camera_id: currentSingleCamera.id
            })
        });
        
        const zlmData = await zlmResponse.json();
        
        if (!zlmData.success) {
            console.error('启动ZLM语音对讲失败:', zlmData.error);
            updateAudioTalkInfo('✗ 启动失败');
            showAudioTalkTip('无法启动ZLM语音对讲', 'error');
            return;
        }
        
        console.log('✓ ZLM语音对讲已启动:', zlmData);
        console.log('  推流URL:', zlmData.push_url);
        console.log('  播放URL:', zlmData.play_url);
        
        // 步骤3: 使用ZLM WebRTC推流
        console.log('开始WebRTC推流到ZLM...');
        updateAudioTalkInfo('建立推流连接...');
        
        try {
            // 方法1: 使用ZLMRTCClient（如果可用）
            if (typeof window.ZLMRTCClient !== 'undefined') {
                console.log('使用ZLMRTCClient推流');
                
                // 创建推流客户端
                const pushClient = new ZLMRTCClient.Endpoint({
                    element: null,  // 推流不需要video元素
                    debug: true,
                    autoplay: true
                });
                
                // 监听事件
                pushClient.on(ZLMRTCClient.Events.WEBRTC_ON_CONNECTION_STATE_CHANGE, (state) => {
                    console.log('推流连接状态:', state);
                    
                    if (state === 'connected') {
                        console.log('✓ 推流连接已建立');
                        audioTalkActive = true;
                        updateAudioTalkStatus('active');
                        updateAudioTalkInfo('✓ 语音对讲已连接');
                        startAudioLevelCheck();
                        showAudioTalkTip('语音对讲已开始（ZLM推流）', 'success');
                        
                        // 启用结束按钮，禁用开始按钮
                        document.getElementById('audioTalkStartBtn').disabled = true;
                        document.getElementById('audioTalkStopBtn').disabled = false;
                    } else if (state === 'failed' || state === 'disconnected') {
                        console.error('推流连接失败');
                        audioTalkActive = false;
                        updateAudioTalkStatus('inactive');
                        updateAudioTalkInfo('✗ 推流连接失败');
                        stopAudioLevelCheck();
                        
                        // 启用开始按钮，禁用结束按钮
                        document.getElementById('audioTalkStartBtn').disabled = false;
                        document.getElementById('audioTalkStopBtn').disabled = true;
                    }
                });
                
                pushClient.on(ZLMRTCClient.Events.WEBRTC_ON_LOCAL_STREAM, (stream) => {
                    console.log('本地音频流:', stream);
                    
                    // 将麦克风音频流添加到推流客户端
                    if (audioTalkLocalStream) {
                        const audioTracks = audioTalkLocalStream.getAudioTracks();
                        if (audioTracks.length > 0) {
                            pushClient.replaceTrack(audioTracks[0]);
                            console.log('✓ 麦克风音频已添加到推流');
                        }
                    }
                });
                
                // 开始推流
                pushClient.start(zlmData.push_url);
                
                // 保存推流客户端引用
                audioTalkZlmPushClient = pushClient;
                
            } else {
                // 方法2: 使用原生WebRTC推流
                console.log('使用原生WebRTC推流（POST Offer）');
                
                // 创建PeerConnection
                audioTalkPeerConnection = new RTCPeerConnection({
                    iceServers: [
                        { urls: 'stun:stun.l.google.com:19302' }
                    ]
                });
                
                // 添加本地音频轨道
                const audioTracks = audioTalkLocalStream.getAudioTracks();
                if (audioTracks.length > 0) {
                    audioTalkPeerConnection.addTrack(audioTracks[0], audioTalkLocalStream);
                    console.log('✓ 本地音频轨道已添加');
                }
                
                // 监听ICE候选
                audioTalkPeerConnection.onicecandidate = (event) => {
                    if (event.candidate) {
                        console.log('本地ICE候选:', event.candidate);
                        // ZLM会通过POST请求交换ICE候选，这里不需要单独发送
                    }
                };
                
                // 监听连接状态
                audioTalkPeerConnection.onconnectionstatechange = () => {
                    console.log('推流连接状态:', audioTalkPeerConnection.connectionState);
                    
                    if (audioTalkPeerConnection.connectionState === 'connected') {
                        console.log('✓ 推流连接已建立');
                        audioTalkActive = true;
                        updateAudioTalkStatus('active');
                        updateAudioTalkInfo('✓ 语音对讲已连接');
                        startAudioLevelCheck();
                        showAudioTalkTip('语音对讲已开始（原生推流）', 'success');
                        
                        document.getElementById('audioTalkStartBtn').disabled = true;
                        document.getElementById('audioTalkStopBtn').disabled = false;
                    } else if (audioTalkPeerConnection.connectionState === 'failed') {
                        console.error('推流连接失败');
                        audioTalkActive = false;
                        updateAudioTalkStatus('inactive');
                        updateAudioTalkInfo('✗ 推流连接失败');
                        stopAudioLevelCheck();
                        
                        document.getElementById('audioTalkStartBtn').disabled = false;
                        document.getElementById('audioTalkStopBtn').disabled = true;
                    }
                };
                
                // 创建Offer
                const offer = await audioTalkPeerConnection.createOffer({
                    offerToReceiveAudio: false,  // 推流不需要接收音频
                    offerToReceiveVideo: false
                });
                
                await audioTalkPeerConnection.setLocalDescription(offer);
                console.log('✓ Offer已创建');
                
                // POST Offer到ZLM WebRTC推流接口
                const pushOfferResponse = await fetch(zlmData.push_url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'text/plain'  // ZLM期望text/plain格式
                    },
                    body: offer.sdp
                });
                
                const pushAnswerSdp = await pushOfferResponse.text();
                
                // 解析JSON响应（ZLM返回JSON格式的Answer）
                try {
                    const pushAnswerData = JSON.parse(pushAnswerSdp);
                    
                    if (pushAnswerData.code === 0) {
                        console.log('✓ 收到ZLM Answer:', pushAnswerData.sdp);
                        
                        // 设置远程描述（ZLM Answer）
                        const answer = new RTCSessionDescription({
                            type: 'answer',
                            sdp: pushAnswerData.sdp
                        });
                        
                        await audioTalkPeerConnection.setRemoteDescription(answer);
                        console.log('✓ 远程描述已设置');
                        
                    } else {
                        console.error('ZLM返回错误:', pushAnswerData.msg || pushAnswerData.code);
                        updateAudioTalkInfo('✗ ZLM推流失败');
                        showAudioTalkTip('ZLM推流失败: ' + (pushAnswerData.msg || '未知错误'), 'error');
                        return;
                    }
                    
                } catch (parseError) {
                    console.error('解析ZLM响应失败:', parseError);
                    console.log('ZLM响应内容:', pushAnswerSdp);
                    updateAudioTalkInfo('✗ ZLM响应解析失败');
                    return;
                }
            }
            
            // 步骤4: 从ZLM拉流播放（可选，如果需要听到摄像机音频）
            console.log('从ZLM拉流播放音频...');
            
            // 使用现有的播放器（如果已经在播放视频，音频轨道会自动包含）
            // 或者创建专门的音频播放器
            
            console.log('✓ 语音对讲初始化完成');
            
        } catch (pushError) {
            console.error('推流失败:', pushError);
            updateAudioTalkInfo('✗ 推流失败');
            showAudioTalkTip('推流失败: ' + pushError.message, 'error');
            
            // 清理资源
            if (audioTalkLocalStream) {
                audioTalkLocalStream.getTracks().forEach(track => track.stop());
                audioTalkLocalStream = null;
            }
            
            if (audioTalkPeerConnection) {
                audioTalkPeerConnection.close();
                audioTalkPeerConnection = null;
            }
        }
        
    } catch (error) {
        console.error('开始语音对讲失败:', error);
        updateAudioTalkInfo('✗ 初始化失败');
        showAudioTalkTip('语音对讲启动失败', 'error');
        
        // 清理资源
        if (audioTalkLocalStream) {
            audioTalkLocalStream.getTracks().forEach(track => track.stop());
            audioTalkLocalStream = null;
        }
        
        if (audioTalkPeerConnection) {
            audioTalkPeerConnection.close();
            audioTalkPeerConnection = null;
        }
    }
}

/**
 * 发送Answer SDP到服务器
 */
async function sendAnswerToServer(answer) {
    try {
        const response = await fetch('/api/audio/talk/start-call', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                camera_id: currentSingleCamera.id,
                answer: {
                    type: answer.type,
                    sdp: answer.sdp
                }
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('✓ Answer已发送');
            return true;
        } else {
            console.error('发送Answer失败:', data.error);
            return false;
        }
        
    } catch (error) {
        console.error('发送Answer请求失败:', error);
        return false;
    }
}

/**
 * 发送ICE候选到服务器（通过WebSocket）
 */
function sendIceCandidate(candidate) {
    // 通过HTTP API发送ICE候选
    fetch('/api/audio/talk/ice-candidate', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            camera_id: currentSingleCamera.id,
            candidate: {
                candidate: candidate.candidate,
                sdpMid: candidate.sdpMid,
                sdpMLineIndex: candidate.sdpMLineIndex
            }
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log('ICE候选已发送:', candidate);
        } else {
            console.error('发送ICE候选失败:', data.error);
        }
    })
    .catch(error => {
        console.error('发送ICE候选失败:', error);
    });
}

/**
 * 获取并添加服务器ICE候选
 */
async function fetchAndAddServerIceCandidates() {
    let retryCount = 0;
    const maxRetries = 10;  // 最大尝试次数
    const retryInterval = 500;  // 重试间隔（毫秒）
    let addedCandidatesCount = 0;
    
    // 重要：去重列表，记录已添加的ICE候选
    const addedCandidatesSet = new Set();
    
    while (retryCount < maxRetries) {
        try {
            // 检查连接状态，如果已连接则停止轮询
            if (audioTalkPeerConnection && 
                (audioTalkPeerConnection.connectionState === 'connected' ||
                 audioTalkPeerConnection.connectionState === 'failed')) {
                console.log('连接已建立或失败，停止获取服务器ICE候选');
                break;
            }
            
            // 获取服务器ICE候选
            const response = await fetch(`/api/audio/talk/get-server-ice-candidates?camera_id=${currentSingleCamera.id}`);
            const data = await response.json();
            
            if (data.success && data.candidates && data.candidates.length > 0) {
                console.log(`获取到 ${data.candidates.length} 个服务器ICE候选`);
                
                // 添加ICE候选到PeerConnection（去重）
                for (const candidateData of data.candidates) {
                    try {
                        // 生成唯一标识（用于去重）
                        const candidateKey = `${candidateData.sdpMid}-${candidateData.sdpMLineIndex}-${candidateData.candidate}`;
                        
                        // 检查是否已添加过这个候选
                        if (addedCandidatesSet.has(candidateKey)) {
                            console.log(`跳过重复的ICE候选: ${candidateData.candidate}`);
                            continue;  // 跳过重复候选
                        }
                        
                        // 添加新的ICE候选
                        const candidate = new RTCIceCandidate({
                            candidate: candidateData.candidate,
                            sdpMid: candidateData.sdpMid,
                            sdpMLineIndex: candidateData.sdpMLineIndex
                        });
                        
                        await audioTalkPeerConnection.addIceCandidate(candidate);
                        addedCandidatesCount++;
                        addedCandidatesSet.add(candidateKey);  // 记录已添加
                        
                        console.log(`✓ 服务器ICE候选已添加 #${addedCandidatesCount}`);
                        
                    } catch (addError) {
                        console.error('添加服务器ICE候选失败:', addError);
                    }
                }
                
                // 如果获取到了候选，继续轮询（可能还有更多）
                retryCount++;
                await new Promise(resolve => setTimeout(resolve, retryInterval));
                
            } else {
                // 没有获取到候选，等待后重试
                console.log(`未获取到服务器ICE候选，等待重试... (${retryCount + 1}/${maxRetries})`);
                retryCount++;
                await new Promise(resolve => setTimeout(resolve, retryInterval));
            }
            
        } catch (error) {
            console.error('获取服务器ICE候选失败:', error);
            retryCount++;
            await new Promise(resolve => setTimeout(resolve, retryInterval));
        }
    }
    
    console.log(`服务器ICE候选获取完成，共添加 ${addedCandidatesCount} 个唯一候选`);
}

/**
 * 播放远程音频（摄像机音频）
 */
function playRemoteAudio(stream) {
    try {
        // 创建音频元素
        const audioElement = document.createElement('audio');
        audioElement.srcObject = stream;
        audioElement.autoplay = true;
        audioElement.controls = false;
        
        // 添加到DOM（隐藏）
        document.body.appendChild(audioElement);
        
        console.log('✓ 远程音频已开始播放');
        
    } catch (error) {
        console.error('播放远程音频失败:', error);
    }
}

/**
 * 停止语音对讲
 */
async function stopAudioTalk() {
    try {
        console.log('停止语音对讲...');
        updateAudioTalkInfo('正在停止...');
        
        // 停止音频级别检测
        stopAudioLevelCheck();
        
        // 停止本地音频流
        if (audioTalkLocalStream) {
            audioTalkLocalStream.getTracks().forEach(track => {
                track.stop();
                console.log('音频轨道已停止:', track.kind);
            });
            audioTalkLocalStream = null;
        }
        
        // 关闭PeerConnection（aiortc方案）
        if (audioTalkPeerConnection) {
            audioTalkPeerConnection.close();
            audioTalkPeerConnection = null;
        }
        
        // 关闭ZLM推流客户端（新方案）
        if (audioTalkZlmPushClient) {
            try {
                audioTalkZlmPushClient.close();
                console.log('✓ ZLM推流客户端已关闭');
            } catch (e) {
                console.error('关闭ZLM推流客户端失败:', e);
            }
            audioTalkZlmPushClient = null;
        }
        
        // 关闭ZLM播放客户端（新方案）
        if (audioTalkZlmPlayClient) {
            try {
                audioTalkZlmPlayClient.close();
                console.log('✓ ZLM播放客户端已关闭');
            } catch (e) {
                console.error('关闭ZLM播放客户端失败:', e);
            }
            audioTalkZlmPlayClient = null;
        }
        
        // 清理远程音频元素
        const audioElements = document.querySelectorAll('audio[srcObject]');
        audioElements.forEach(element => element.remove());
        
        // 通知服务器关闭会话（支持两种方案）
        if (audioTalkSessionId || currentSingleCamera) {
            // 尝试停止ZLM会话
            try {
                const zlmStopResponse = await fetch('/api/audio/talk/stop-zlm', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        camera_id: currentSingleCamera.id
                    })
                });
                
                const zlmStopData = await zlmStopResponse.json();
                console.log('ZLM语音对讲已停止:', zlmStopData.message);
                
            } catch (zlmError) {
                console.log('ZLM停止接口调用失败（可能未使用ZLM方案）:', zlmError);
            }
            
            // 尝试关闭旧方案的会话
            try {
                await fetch('/api/audio/talk/close-session', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        camera_id: currentSingleCamera.id
                    })
                });
                
            } catch (closeError) {
                console.log('关闭会话接口调用失败:', closeError);
            }
        }
        
        audioTalkActive = false;
        audioTalkSessionId = null;
        
        // 更新UI状态
        updateAudioTalkStatus('inactive');
        updateAudioTalkInfo('语音对讲已结束');
        
        // 启用开始按钮，禁用结束按钮
        document.getElementById('audioTalkStartBtn').disabled = false;
        document.getElementById('audioTalkStopBtn').disabled = true;
        
        console.log('✓ 语音对讲已停止');
        showAudioTalkTip('语音对讲已结束', 'info');
        
    } catch (error) {
        console.error('停止语音对讲失败:', error);
        updateAudioTalkInfo('✗ 停止失败');
    }
}

/**
 * 更新对讲状态UI
 */
function updateAudioTalkStatus(status) {
    const statusIcon = document.getElementById('audioTalkStatusIcon');
    const statusText = document.getElementById('audioTalkStatusText');
    
    if (status === 'active') {
        // 激活状态
        statusIcon.parentElement.classList.add('active');
        statusIcon.className = 'fa fa-microphone';
        statusText.textContent = '正在对讲';
        statusText.classList.add('active');
        
    } else {
        // 未激活状态
        statusIcon.parentElement.classList.remove('active');
        statusIcon.className = 'fa fa-microphone-slash';
        statusText.textContent = '未连接';
        statusText.classList.remove('active');
    }
}

/**
 * 更新对讲信息文本
 */
function updateAudioTalkInfo(text) {
    const infoElement = document.getElementById('audioTalkInfo');
    if (infoElement) {
        infoElement.textContent = text;
    }
}

/**
 * 禁用语音对讲按钮
 */
function disableAudioTalkButtons() {
    document.getElementById('audioTalkStartBtn').disabled = true;
    document.getElementById('audioTalkStopBtn').disabled = true;
}

/**
 * 开始音频级别检测
 */
function startAudioLevelCheck() {
    if (!audioTalkLocalStream) return;
    
    // 使用Web Audio API检测音频级别
    try {
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const analyser = audioContext.createAnalyser();
        const microphone = audioContext.createMediaStreamSource(audioTalkLocalStream);
        
        microphone.connect(analyser);
        analyser.fftSize = 256;
        
        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        
        // 定时检测音频级别
        audioLevelCheckInterval = setInterval(() => {
            analyser.getByteFrequencyData(dataArray);
            
            // 计算平均音量
            const average = dataArray.reduce((a, b) => a + b) / dataArray.length;
            const level = Math.floor(average / 255 * 5);  // 转换为1-5级别
            
            updateAudioLevelBars(level);
            
        }, 100);  // 每100ms检测一次
        
        console.log('✓ 音频级别检测已启动');
        
    } catch (error) {
        console.error('音频级别检测失败:', error);
    }
}

/**
 * 停止音频级别检测
 */
function stopAudioLevelCheck() {
    if (audioLevelCheckInterval) {
        clearInterval(audioLevelCheckInterval);
        audioLevelCheckInterval = null;
        
        // 清空音频级别条
        updateAudioLevelBars(0);
        
        console.log('✓ 音频级别检测已停止');
    }
}

/**
 * 更新音频级别条显示
 */
function updateAudioLevelBars(level) {
    for (let i = 1; i <= 5; i++) {
        const bar = document.getElementById(`audioLevelBar${i}`);
        if (bar) {
            if (i <= level) {
                bar.classList.add('active');
            } else {
                bar.classList.remove('active');
            }
        }
    }
}

/**
 * 显示语音对讲提示
 */
function showAudioTalkTip(message, type = 'info') {
    // 创建提示元素
    let tipElement = document.getElementById('audioTalkTip');
    
    if (!tipElement) {
        tipElement = document.createElement('div');
        tipElement.id = 'audioTalkTip';
        tipElement.className = 'audio-talk-tip';
        tipElement.innerHTML = `
            <div class="audio-talk-tip-content">
                <i class="fa fa-info-circle"></i>
                <span id="audioTalkTipMessage"></span>
            </div>
        `;
        document.body.appendChild(tipElement);
    }
    
    const tipMessage = document.getElementById('audioTalkTipMessage');
    const tipIcon = tipElement.querySelector('i');
    
    // 设置图标和消息
    tipMessage.textContent = message;
    
    if (type === 'success') {
        tipIcon.className = 'fa fa-check-circle';
        tipIcon.style.color = '#28a745';
    } else if (type === 'error') {
        tipIcon.className = 'fa fa-times-circle';
        tipIcon.style.color = '#dc3545';
    } else {
        tipIcon.className = 'fa fa-info-circle';
        tipIcon.style.color = '#266CFB';
    }
    
    // 显示提示
    tipElement.classList.add('active');
    
    // 3秒后自动隐藏
    setTimeout(() => {
        tipElement.classList.remove('active');
    }, 3000);
}

/**
 * 初始化语音对讲功能（在播放器打开时调用）
 */
function initAudioTalk() {
    console.log('初始化语音对讲功能...');
    
    // 检测语音对讲能力
    if (currentSingleCamera) {
        checkAudioTalkCapabilities();
    }
    
    // 初始化WebSocket（如果需要）
    // initAudioTalkWebSocket();
}

/**
 * 初始化WebSocket信令通道（可选）
 */
function initAudioTalkWebSocket() {
    try {
        const wsUrl = `ws://${window.location.host}/audio/talk`;
        audioTalkWebSocket = new WebSocket(wsUrl);
        
        audioTalkWebSocket.onopen = () => {
            console.log('✓ WebSocket已连接');
        };
        
        audioTalkWebSocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log('WebSocket消息:', data);
            
            // 处理WebSocket消息
            if (data.type === 'webrtc_answer_response') {
                console.log('Answer响应:', data);
            } else if (data.type === 'webrtc_ice_response') {
                console.log('ICE响应:', data);
            }
        };
        
        audioTalkWebSocket.onerror = (error) => {
            console.error('WebSocket错误:', error);
        };
        
        audioTalkWebSocket.onclose = () => {
            console.log('WebSocket已关闭');
            audioTalkWebSocket = null;
        };
        
    } catch (error) {
        console.error('WebSocket初始化失败:', error);
    }
}

// 导出函数（供外部调用）
window.startAudioTalk = startAudioTalk;
window.stopAudioTalk = stopAudioTalk;
window.checkAudioTalkCapabilities = checkAudioTalkCapabilities;
window.initAudioTalk = initAudioTalk;