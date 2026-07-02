"""
ONVIF语音对讲API接口

基于ONVIF标准Audio Back Channel协议实现跨品牌双向语音对讲。

支持所有ONVIF Profile T/Q认证的摄像机：
- 海康威视（Hikvision）
- 大华（Dahua）
- 宇视（Uniview）
- 天地伟业（Tiandy）
- 其他ONVIF认证摄像机

核心优势：
- 标准ONVIF协议，跨品牌兼容
- 无需WebRTC复杂信令
- 直接通过RTSP Audio Back Channel推送音频
- 简化架构，降低部署难度

实现流程：
1. 前端麦克风 → 平台 → ONVIF Audio Back Channel → 摄像机扬声器
2. 摄像机麦克风 → RTSP音频流 → 平台 → 前端播放器

API端点：
- GET /api/audio/talk/capabilities - 检测语音对讲能力
- POST /api/audio/talk/start - 启动ONVIF语音对讲
- POST /api/audio/talk/stop - 停止语音对讲
- POST /api/audio/talk/send - 发送音频数据
- GET /api/audio/talk/sessions - 获取活动会话列表
"""

import logging
import json
import uuid
import time
import math
import struct
from flask import Blueprint, request, jsonify, current_app
from typing import Dict, Any

logger = logging.getLogger(__name__)

# 创建Blueprint
audio_talk_bp = Blueprint('audio_talk', __name__, url_prefix='/api/audio/talk')

# 导入ONVIF语音对讲服务
try:
    from .audio_talk_service_onvif import (
        ONVIFAudioTalkServiceManager,
        AudioTalkSessionConfig,
        ONVIF_AUDIO_AVAILABLE
    )
    from .onvif_audio_backchannel import test_onvif_audio_backchannel
    
    AUDIO_TALK_AVAILABLE = True
    logger.info("✓ ONVIF语音对讲服务已加载")
    
    # 创建全局服务管理器（单例）
    _audio_talk_service_manager = ONVIFAudioTalkServiceManager()
    logger.info("✓ 全局ONVIF语音对讲服务管理器已创建")
    
except ImportError as e:
    logger.warning(f"⚠ ONVIF语音对讲服务未加载: {e}")
    AUDIO_TALK_AVAILABLE = False
    ONVIF_AUDIO_AVAILABLE = False
    _audio_talk_service_manager = None


def get_audio_talk_service_manager():
    """
    获取全局ONVIF语音对讲服务管理器
    
    Returns:
        ONVIFAudioTalkServiceManager: 服务管理器实例
    """
    return _audio_talk_service_manager


@audio_talk_bp.route('/capabilities', methods=['GET'])
def get_audio_talk_capabilities():
    """
    检测摄像机语音对讲能力
    
    Query Parameters:
        camera_id: 摄像机ID
    
    Returns:
        {
            "success": true,
            "capabilities": {
                "supported": true,
                "audio_backchannel_supported": true,
                "codecs": ["PCMU", "PCMA"],
                "sample_rate": 8000,
                "channels": 1,
                "onvif_supported": true
            }
        }
    """
    try:
        camera_id = request.args.get('camera_id')
        
        if not camera_id:
            return jsonify({'error': '缺少摄像机ID'}), 400
        
        logger.info(f"检测语音对讲能力: camera_id={camera_id}")
        
        if not AUDIO_TALK_AVAILABLE:
            return jsonify({
                'success': False,
                'error': 'ONVIF语音对讲服务未安装',
                'hint': '请确保ONVIF Audio Back Channel模块已正确安装'
            }), 500
        
        # 从数据库获取摄像机信息
        from .camera_manager import CameraManager
        
        manager = CameraManager()
        camera = manager.get_camera(camera_id)
        
        if not camera:
            logger.error(f"摄像机不存在: camera_id={camera_id}")
            return jsonify({'error': '摄像机不存在'}), 404
        
        # 获取摄像机配置
        camera_ip = camera.ip
        rtsp_port = camera.rtsp_port if hasattr(camera, 'rtsp_port') else 554
        username = camera.username if hasattr(camera, 'username') else 'admin'
        password = camera.password if hasattr(camera, 'password') else ''
        
        logger.info(f"检测Audio Back Channel支持: {camera_ip}:{rtsp_port}")
        
        # 测试ONVIF Audio Back Channel
        result = test_onvif_audio_backchannel(
            camera_ip=camera_ip,
            camera_port=rtsp_port,
            username=username,
            password=password
        )
        
        # 构建能力响应
        capabilities = {
            'supported': result.get('audio_backchannel_supported', False),
            'audio_backchannel_supported': result.get('audio_backchannel_supported', False),
            'codecs': ['PCMU', 'PCMA'],  # ONVIF标准编码
            'sample_rate': 8000,
            'channels': 1,
            'onvif_supported': ONVIF_AUDIO_AVAILABLE,
            'audio_tracks': result.get('audio_tracks', []),
            'success': result.get('success', False)
        }
        
        logger.info(f"语音对讲能力: supported={capabilities['supported']}, "
                   f"audio_backchannel={capabilities['audio_backchannel_supported']}")
        
        return jsonify({
            'success': True,
            'capabilities': capabilities
        })
        
    except Exception as e:
        logger.error(f"检测语音对讲能力失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@audio_talk_bp.route('/start', methods=['POST'])
def start_audio_talk():
    """
    启动ONVIF语音对讲会话
    
    Request Body:
        {
            "camera_id": "camera_001",
            "audio_codec": "PCMU",  # 可选，默认PCMU
            "sample_rate": 8000  # 可选，默认8000
        }
    
    Returns:
        {
            "success": true,
            "session_id": "session_001",
            "camera_ip": "192.168.5.201",
            "message": "ONVIF语音对讲会话已启动"
        }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '缺少请求数据'}), 400
        
        camera_id = data.get('camera_id')
        
        if not camera_id:
            return jsonify({'error': '缺少摄像机ID'}), 400
        
        logger.info(f"启动ONVIF语音对讲: camera_id={camera_id}")
        
        if not AUDIO_TALK_AVAILABLE:
            return jsonify({
                'success': False,
                'error': 'ONVIF语音对讲服务未安装'
            }), 500
        
        # 从数据库获取摄像机信息
        from .camera_manager import CameraManager
        
        manager = CameraManager()
        camera = manager.get_camera(camera_id)
        
        if not camera:
            logger.error(f"摄像机不存在: camera_id={camera_id}")
            return jsonify({'error': '摄像机不存在'}), 404
        
        # 获取摄像机配置
        camera_ip = camera.ip
        rtsp_port = camera.rtsp_port if hasattr(camera, 'rtsp_port') else 554
        username = camera.username if hasattr(camera, 'username') else 'admin'
        password = camera.password if hasattr(camera, 'password') else ''
        
        # 音频参数
        audio_codec = data.get('audio_codec', 'PCMU')
        sample_rate = data.get('sample_rate', 8000)

        # 音频增强参数
        volume_gain = data.get('volume_gain', 1.0)  # 音量增益（0-2.0）
        noise_suppression = data.get('noise_suppression', True)  # 噪音抑制
        echo_cancellation = data.get('echo_cancellation', True)  # 回声消除
        
        # 获取全局服务管理器
        service_manager = get_audio_talk_service_manager()
        
        if not service_manager:
            return jsonify({
                'success': False,
                'error': '服务管理器未初始化'
            }), 500
        
        # 生成会话ID
        session_id = f"audio_talk_{camera_id}_{uuid.uuid4().hex[:8]}"
        
        # 创建会话
        session = service_manager.create_session(
            session_id=session_id,
            camera_id=camera_id,
            camera_ip=camera_ip,
            camera_rtsp_port=rtsp_port,
            username=username,
            password=password,
            audio_codec=audio_codec,
            sample_rate=sample_rate,
            volume_gain=volume_gain,
            noise_suppression=noise_suppression,
            echo_cancellation=echo_cancellation
        )
        
        # 启动会话
        if service_manager.start_session(session_id):
            logger.info(f"✓ ONVIF语音对讲会话已启动: session_id={session_id}")
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'camera_id': camera_id,
                'camera_ip': camera_ip,
                'audio_codec': audio_codec,
                'sample_rate': sample_rate,
                'volume_gain': volume_gain,
                'noise_suppression': noise_suppression,
                'echo_cancellation': echo_cancellation,
                'message': 'ONVIF语音对讲会话已启动，可以发送音频数据'
            })
        else:
            logger.error(f"启动会话失败: session_id={session_id}")
            
            return jsonify({
                'success': False,
                'error': 'ONVIF Audio Back Channel建立失败，摄像机可能不支持'
            }), 500
        
    except Exception as e:
        logger.error(f"启动ONVIF语音对讲失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@audio_talk_bp.route('/stop', methods=['POST'])
def stop_audio_talk():
    """
    停止ONVIF语音对讲会话
    
    Request Body:
        {
            "session_id": "session_001"
        }
    
    Returns:
        {
            "success": true,
            "message": "ONVIF语音对讲会话已停止"
        }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '缺少请求数据'}), 400
        
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'error': '缺少会话ID'}), 400
        
        logger.info(f"停止ONVIF语音对讲: session_id={session_id}")
        
        # 获取全局服务管理器
        service_manager = get_audio_talk_service_manager()
        
        if not service_manager:
            return jsonify({
                'success': False,
                'error': '服务管理器未初始化'
            }), 500
        
        # 停止会话
        service_manager.stop_session(session_id)
        
        logger.info(f"✓ ONVIF语音对讲会话已停止: session_id={session_id}")
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'message': 'ONVIF语音对讲会话已停止'
        })
        
    except Exception as e:
        logger.error(f"停止ONVIF语音对讲失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@audio_talk_bp.route('/send', methods=['POST'])
def send_audio_data():
    """
    发送音频数据到摄像机扬声器
    
    Request Body (JSON):
        {
            "session_id": "session_001",
            "audio_data": "base64编码的PCM音频数据"
        }
    
    或直接发送二进制PCM数据（Content-Type: application/octet-stream）
    
    Returns:
        {
            "success": true,
            "message": "音频数据已发送"
        }
    """
    try:
        # 检查Content-Type
        content_type = request.content_type
        
        session_id = None
        pcm_data = None
        
        if content_type == 'application/json':
            # JSON格式
            data = request.get_json()
            
            if not data:
                return jsonify({'error': '缺少请求数据'}), 400
            
            session_id = data.get('session_id')
            
            if not session_id:
                return jsonify({'error': '缺少会话ID'}), 400
            
            # 解码Base64音频数据
            audio_data_base64 = data.get('audio_data')
            
            if not audio_data_base64:
                return jsonify({'error': '缺少音频数据'}), 400
            
            import base64
            pcm_data = base64.b64decode(audio_data_base64)
            
        elif content_type == 'application/octet-stream':
            # 二进制PCM数据
            session_id = request.headers.get('X-Session-ID')
            
            if not session_id:
                return jsonify({'error': '缺少会话ID（X-Session-ID header）'}), 400
            
            pcm_data = request.data
        
        else:
            return jsonify({'error': '不支持的Content-Type'}), 400
        
        logger.info(f"发送音频数据: session_id={session_id}, size={len(pcm_data)}字节")
        
        # 获取全局服务管理器
        service_manager = get_audio_talk_service_manager()
        
        if not service_manager:
            return jsonify({
                'success': False,
                'error': '服务管理器未初始化'
            }), 500
        
        # 发送音频数据
        if service_manager.send_audio_to_session(session_id, pcm_data):
            logger.info(f"✓ 音频数据已发送: {len(pcm_data)}字节")
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'message': '音频数据已发送到摄像机扬声器'
            })
        else:
            logger.error(f"发送音频数据失败: session_id={session_id}")
            
            return jsonify({
                'success': False,
                'error': '发送音频数据失败'
            }), 500
        
    except Exception as e:
        logger.error(f"发送音频数据失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@audio_talk_bp.route('/sessions', methods=['GET'])
def get_active_sessions():
    """
    获取所有活动的语音对讲会话
    
    Returns:
        {
            "success": true,
            "sessions": {
                "session_001": {
                    "session_id": "session_001",
                    "camera_id": "camera_001",
                    "camera_ip": "192.168.5.201",
                    "is_active": true,
                    "audio_codec": "PCMU",
                    "sample_rate": 8000
                }
            }
        }
    """
    try:
        logger.info("获取所有活动的语音对讲会话")
        
        # 获取全局服务管理器
        service_manager = get_audio_talk_service_manager()
        
        if not service_manager:
            return jsonify({
                'success': False,
                'error': '服务管理器未初始化'
            }), 500
        
        # 获取活动会话
        active_sessions = service_manager.get_active_sessions()
        
        logger.info(f"活动会话数量: {len(active_sessions)}")
        
        return jsonify({
            'success': True,
            'sessions': active_sessions,
            'count': len(active_sessions)
        })
        
    except Exception as e:
        logger.error(f"获取活动会话失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@audio_talk_bp.route('/test', methods=['POST'])
def test_audio_send():
    """
    测试音频发送（发送模拟正弦波）
    
    Request Body:
        {
            "session_id": "session_001",
            "duration": 5,  # 发送持续时间（秒）
            "frequency": 1000  # 正弦波频率（Hz）
        }
    
    Returns:
        {
            "success": true,
            "message": "测试音频已发送"
        }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '缺少请求数据'}), 400
        
        session_id = data.get('session_id')
        duration = data.get('duration', 5)
        frequency = data.get('frequency', 1000)
        
        if not session_id:
            return jsonify({'error': '缺少会话ID'}), 400
        
        logger.info(f"测试音频发送: session_id={session_id}, duration={duration}s, frequency={frequency}Hz")
        
        # 获取全局服务管理器
        service_manager = get_audio_talk_service_manager()
        
        if not service_manager:
            return jsonify({
                'success': False,
                'error': '服务管理器未初始化'
            }), 500
        
        # 生成正弦波PCM数据
        sample_rate = 8000
        total_samples = sample_rate * duration
        chunk_size = 160  # 每次发送160个采样（20ms）
        
        logger.info(f"生成正弦波: total_samples={total_samples}, chunk_size={chunk_size}")
        
        packets_sent = 0
        
        # 分块发送
        for i in range(0, total_samples, chunk_size):
            # 生成正弦波PCM数据
            samples = []
            
            for j in range(chunk_size):
                t = (i + j) / sample_rate
                value = int(32767 * 0.3 * math.sin(2 * math.pi * frequency * t))
                samples.append(struct.pack('<h', value))
            
            pcm_data = b''.join(samples)
            
            # 发送音频数据
            if service_manager.send_audio_to_session(session_id, pcm_data):
                packets_sent += 1
            
            # 控制发送速率
            time.sleep(0.02)  # 20ms
        
        logger.info(f"✓ 测试音频发送完成: packets_sent={packets_sent}")
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'duration': duration,
            'frequency': frequency,
            'packets_sent': packets_sent,
            'message': f'测试音频已发送（{packets_sent}个RTP包）'
        })
        
    except Exception as e:
        logger.error(f"测试音频发送失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@audio_talk_bp.route('/health', methods=['GET'])
def health_check():
    """
    健康检查
    
    Returns:
        {
            "status": "ok",
            "service": "ONVIF Audio Talk Service",
            "onvif_available": true
        }
    """
    return jsonify({
        'status': 'ok',
        'service': 'ONVIF Audio Talk Service',
        'onvif_available': ONVIF_AUDIO_AVAILABLE,
        'audio_talk_available': AUDIO_TALK_AVAILABLE
    })


# Blueprint注册函数
def register_audio_talk_blueprint(app):
    """
    注册语音对讲Blueprint到Flask应用
    
    Args:
        app: Flask应用实例
    """
    try:
        app.register_blueprint(audio_talk_bp)
        logger.info("✓ ONVIF语音对讲Blueprint已注册")
        
    except Exception as e:
        logger.error(f"注册语音对讲Blueprint失败: {e}")


def init_audio_talk_api(app, socketio=None):
    """
    初始化语音对讲API（兼容旧接口）
    
    注意：ONVIF方案不需要WebSocket信令，
    参数socketio保留是为了兼容旧代码
    
    Args:
        app: Flask应用实例
        socketio: SocketIO实例（可选，ONVIF方案不使用）
    """
    try:
        # 注册Blueprint
        register_audio_talk_blueprint(app)
        
        if socketio:
            logger.info("✓ SocketIO参数已接收（ONVIF方案不使用WebSocket信令）")
        
        logger.info("✓ ONVIF语音对讲API初始化完成")
        
    except Exception as e:
        logger.error(f"初始化语音对讲API失败: {e}")