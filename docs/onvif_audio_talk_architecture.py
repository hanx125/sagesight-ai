"""
ONVIF语音对讲系统架构说明

系统架构
=========

基于ONVIF标准Audio Back Channel协议实现跨品牌双向语音对讲功能。

核心优势：
1. 跨品牌兼容（支持所有ONVIF Profile T/Q认证摄像机）
2. 无需WebRTC复杂信令（直接RTSP连接）
3. 简化架构（降低部署难度）
4. 标准化协议（不依赖厂商私有实现）

系统组成
=========

核心模块：

1. onvif_audio_backchannel.py（核心协议实现）
   - ONVIFAudioBackchannel类：实现RTSP Audio Back Channel标准流程
   - AudioEncoder类：PCM → PCMU/PCMA编码
   - RTPPacketBuilder类：构建符合RFC 3550标准的RTP包
   - test_onvif_audio_backchannel()：测试摄像机支持情况

2. audio_talk_service_onvif.py（服务层）
   - ONVIFAudioTalkSession类：管理单个摄像机的语音对讲会话
   - AudioSender类：将编码后的音频发送到摄像机
   - ONVIFAudioTalkServiceManager类：管理所有活动会话

3. audio_talk_api.py（API接口）
   - Flask Blueprint：提供RESTful API
   - 支持前端直接调用语音对讲功能
   - 无需WebSocket信令交换

音频流程
=========

平台向摄像机喊话（Audio Back Channel）：

1. 前端麦克风录音 → 获取PCM音频数据
2. 前端 → POST /api/audio/talk/send → 发送PCM数据到平台
3. 平台 → PCM编码为PCMU/PCMA → 音频压缩
4. 平台 → 构建RTP包 → 符合RFC 3550标准
5. 平台 → UDP发送到摄像机 → RTP over UDP
6. 摄像机 → 解码音频 → 扬声器输出

摄像机向平台喊话（Audio接收）：

1. 摄像机麦克风 → RTSP音频流（recvonly轨道）
2. 平台 → 从RTSP流订阅音频 → RTP接收
3. 平台 → 解码PCMU/PCMA → PCM音频
4. 平台 → WebSocket → 发送PCM到前端
5. 前端 → 播放音频 → Web Audio API

API端点
=========

1. GET /api/audio/talk/capabilities
   - 检测摄像机语音对讲能力
   - 返回Audio Back Channel支持情况

2. POST /api/audio/talk/start
   - 启动ONVIF语音对讲会话
   - 建立Audio Back Channel连接
   - 返回session_id

3. POST /api/audio/talk/send
   - 发送音频数据到摄像机扬声器
   - 支持JSON（Base64编码）和二进制PCM数据

4. POST /api/audio/talk/stop
   - 停止语音对讲会话
   - 关闭Audio Back Channel连接

5. GET /api/audio/talk/sessions
   - 获取所有活动会话列表

6. POST /api/audio/talk/test
   - 发送测试正弦波音频
   - 验证系统是否正常工作

使用示例
=========

1. 测试摄像机支持情况：

python scripts/test_onvif_audio_backchannel.py \
    --ip 192.168.5.201 \
    --port 554 \
    --user admin \
    --password tfld2023

2. 测试完整喊话流程：

python scripts/test_platform_to_camera_speak.py \
    --ip 192.168.5.201 \
    --port 554 \
    --user admin \
    --password tfld2023 \
    --duration 5

3. 测试系统集成：

python scripts/test_onvif_audio_talk_integration.py \
    --ip 192.168.5.201 \
    --port 554 \
    --user admin \
    --password tfld2023 \
    --duration 5

前端集成建议
=========

前端需要实现：

1. 麦克风录音（Web Audio API）
   - navigator.mediaDevices.getUserMedia({ audio: true })
   - AudioContext.createMediaStreamSource()
   - ScriptProcessorNode.onaudioprocess → 获取PCM数据

2. 音频数据传输
   - 方式A：WebSocket实时流（推荐）
     - 连接WebSocket → ws://host/api/audio/talk/ws
     - 每次录音事件 → 发送PCM数据包
     - 后端编码并发送到摄像机
   
   - 方式B：HTTP POST批量发送
     - 收集PCM数据 → Base64编码
     - POST /api/audio/talk/send → 发送JSON数据

3. 音频播放（接收摄像机音频）
   - WebSocket接收PCM数据
   - AudioContext.createBufferSource()
   - 播放音频流

兼容性支持
=========

支持的摄像机品牌：
- 海康威视（Hikvision） ✓
- 大华（Dahua） ✓
- 宇视（Uniview） ✓
- 天地伟业（Tiandy） ✓
- 其他ONVIF Profile T/Q认证摄像机 ✓

音频编码支持：
- PCMU（G.711 μ-law） ✓ payload type 0
- PCMA（G.711 A-law） ✓ payload type 8

采样率支持：
- 8000 Hz ✓（标准电话质量）
- 其他采样率需要协商（通过SDP）

技术标准
=========

遵循的国际标准：
1. ONVIF Profile T（双向语音对讲标准）
2. ONVIF Profile Q（服务质量标准）
3. RTSP/RTP（RFC 3550/2326）
4. G.711（ITU-T标准音频编码）
5. IETF SDP（Session Description Protocol）

文件清单
=========

核心文件：
- e:\sagesight-AI\src\cameras\onvif_audio_backchannel.py
- e:\sagesight-AI\src\cameras\audio_talk_service_onvif.py
- e:\sagesight-AI\src\cameras\audio_talk_api.py

测试文件：
- e:\sagesight-AI\scripts\test_onvif_audio_backchannel.py
- e:\sagesight-AI\scripts\test_platform_to_camera_speak.py
- e:\sagesight-AI\scripts\test_onvif_audio_talk_integration.py

辅助工具：
- e:\sagesight-AI\scripts\query_rtsp_audio_capabilities.py
- e:\sagesight-AI\scripts\find_audio_stream.py

已删除的旧方案文件：
- audio_forwarder_zlm.py（ZLM转发器，已废弃）
- audio_talk_service.py（WebRTC方案，已废弃）

下一步开发
=========

待实现功能：
1. 前端麦克风录音和实时音频传输
2. 接收摄像机音频流（recvonly轨道）
3. 双向语音实时交互（同时收发）
4. 音频质量优化（回声消除、噪音抑制）
5. WebSocket实时音频流接口

优化建议：
1. 增加音频缓冲队列（平滑传输）
2. 实现音频数据压缩（减少带宽）
3. 支持多摄像机并发对讲
4. 增加会话超时和自动清理机制
5. 实现音频质量监控和自适应编码
"""