"""
流管理API

提供摄像机流管理的HTTP API接口。
"""

import logging
import threading
from flask import Blueprint, request, jsonify

from .stream_service import get_stream_service

logger = logging.getLogger(__name__)

# 创建Blueprint
stream_bp = Blueprint('stream', __name__, url_prefix='/api/stream')

# 快照抓帧并发限制与短缓存（ffmpeg 抓 RTSP 帧较重，避免列表并发刷新压垮摄像机）
_snapshot_semaphore = threading.Semaphore(4)
_snapshot_cache = {}


# ========================================
# 流管理API
# ========================================

@stream_bp.route('/start/<camera_id>', methods=['POST'])
def start_stream(camera_id: str):
    """
    启动摄像机的流
    
    Args:
        camera_id: 摄像机ID（UUID）
    
    Query Parameters:
        server_id: 流媒体服务器ID（可选）
    
    Returns:
        启动结果
    """
    try:
        # 获取流媒体服务器ID（可选）
        server_id = request.args.get('server_id', type=int)
        
        # 获取流管理服务
        stream_service = get_stream_service()
        
        # 启动流（已包含更新后的camera信息）
        result = stream_service.start_stream(camera_id, server_id)
        
        logger.info(f"启动流成功: camera_id={camera_id}")
        
        return jsonify(result)
        
    except ValueError as e:
        logger.error(f"启动流失败: {str(e)}")
        return jsonify({'error': str(e)}), 400
        
    except Exception as e:
        logger.error(f"启动流异常: {str(e)}", exc_info=True)
        return jsonify({'error': f'启动流失败: {str(e)}'}), 500


@stream_bp.route('/stop/<camera_id>', methods=['POST'])
def stop_stream(camera_id: str):
    """
    停止摄像机的流
    
    Args:
        camera_id: 摄像机ID（UUID）
    
    Query Parameters:
        server_id: 流媒体服务器ID（可选）
    
    Returns:
        停止结果
    """
    try:
        # 获取流媒体服务器ID（可选）
        server_id = request.args.get('server_id', type=int)
        
        # 获取流管理服务
        stream_service = get_stream_service()
        
        # 停止流
        result = stream_service.stop_stream(camera_id, server_id)
        
        logger.info(f"停止流成功: camera_id={camera_id}")
        
        return jsonify(result)
        
    except ValueError as e:
        logger.error(f"停止流失败: {str(e)}")
        return jsonify({'error': str(e)}), 400
        
    except Exception as e:
        logger.error(f"停止流异常: {str(e)}", exc_info=True)
        return jsonify({'error': f'停止流失败: {str(e)}'}), 500


@stream_bp.route('/status/<camera_id>', methods=['GET'])
def check_stream_status(camera_id: str):
    """
    检查摄像机的流状态
    
    Args:
        camera_id: 摄像机ID（UUID）
    
    Query Parameters:
        server_id: 流媒体服务器ID（可选）
    
    Returns:
        流状态信息
    """
    try:
        # 获取流媒体服务器ID（可选）
        server_id = request.args.get('server_id', type=int)
        
        # 获取流管理服务
        stream_service = get_stream_service()
        
        # 检查流状态
        result = stream_service.check_stream_status(camera_id, server_id)
        
        return jsonify(result)
        
    except ValueError as e:
        logger.error(f"检查流状态失败: {str(e)}")
        return jsonify({'error': str(e)}), 400
        
    except Exception as e:
        logger.error(f"检查流状态异常: {str(e)}", exc_info=True)
        return jsonify({'error': f'检查流状态失败: {str(e)}'}), 500


@stream_bp.route('/urls/<camera_id>', methods=['GET'])
def get_stream_urls(camera_id: str):
    """
    获取摄像机的流播放地址
    
    Args:
        camera_id: 摄像机ID（UUID）
    
    Query Parameters:
        server_id: 流媒体服务器ID（可选）
    
    Returns:
        各种协议的播放地址
    """
    try:
        # 获取流媒体服务器ID（可选）
        server_id = request.args.get('server_id', type=int)
        
        # 获取流管理服务
        stream_service = get_stream_service()
        
        # 获取播放地址
        urls = stream_service.get_stream_urls(camera_id, server_id)
        
        return jsonify({
            'success': True,
            'camera_id': camera_id,
            'stream_app': 'AI',
            'stream_id': camera_id,
            'play_urls': urls
        })
        
    except ValueError as e:
        logger.error(f"获取流播放地址失败: {str(e)}")
        return jsonify({'error': str(e)}), 400
        
    except Exception as e:
        logger.error(f"获取流播放地址异常: {str(e)}", exc_info=True)
        return jsonify({'error': f'获取流播放地址失败: {str(e)}'}), 500



@stream_bp.route('/snapshot/<camera_id>', methods=['GET'])
def get_camera_snapshot(camera_id: str):
    """
    获取摄像机实时快照（JPEG）

    通过 ffmpeg 从摄像机 RTSP 源直接抓取一帧（部署的 ZLMediaKit 版本无 getSnap 接口），
    供移动端列表缩略图使用。带并发限制与 2 秒短缓存，避免列表并发刷新时重复抓帧。

    Returns:
        image/jpeg 图片数据
    """
    import time
    import subprocess
    from flask import Response
    from src.cameras.camera_manager import get_camera_manager

    try:
        camera = get_camera_manager().get_camera(camera_id)
        if not camera:
            return jsonify({'error': '摄像机不存在'}), 404
        if not camera.source:
            return jsonify({'error': '摄像机未配置取流地址'}), 400

        # 2 秒短缓存：刷新周期内复用同一帧
        cached = _snapshot_cache.get(camera_id)
        if cached and time.time() - cached[0] < 2:
            return Response(cached[1], mimetype='image/jpeg',
                            headers={'Cache-Control': 'no-store'})

        with _snapshot_semaphore:
            # 等待并发额度期间其他线程可能已完成抓帧
            cached = _snapshot_cache.get(camera_id)
            if cached and time.time() - cached[0] < 2:
                return Response(cached[1], mimetype='image/jpeg',
                                headers={'Cache-Control': 'no-store'})

            # ffmpeg 从 RTSP 源抓一帧到 stdout（-loglevel error 防止日志混入图片数据）；
            # -skip_frame nokey 只解码关键帧：从 GOP 中间接入时首批 P 帧缺参考帧，
            # 解码会得到瀑布条纹残帧，只取关键帧保证画面完整
            proc = subprocess.run(
                ['ffmpeg', '-rtsp_transport', 'tcp', '-y', '-loglevel', 'error',
                 '-skip_frame', 'nokey', '-i', camera.source,
                 '-frames:v', '1', '-q:v', '5', '-f', 'image2', '-'],
                capture_output=True, timeout=15
            )
            jpeg = proc.stdout
            if proc.returncode != 0 or not jpeg:
                logger.warning(f"ffmpeg 抓帧失败: camera_id={camera_id}, returncode={proc.returncode}, "
                               f"stderr={proc.stderr[-200:] if proc.stderr else ''}")
                return jsonify({'error': '抓取快照失败'}), 502

            _snapshot_cache[camera_id] = (time.time(), jpeg)
            return Response(jpeg, mimetype='image/jpeg',
                            headers={'Cache-Control': 'no-store'})

    except subprocess.TimeoutExpired:
        logger.warning(f"抓取快照超时: camera_id={camera_id}")
        return jsonify({'error': '抓取快照超时'}), 504
    except Exception as e:
        logger.error(f"获取快照异常: camera_id={camera_id}, {str(e)}")
        return jsonify({'error': f'获取快照失败: {str(e)}'}), 500


@stream_bp.route('/batch/start', methods=['POST'])
def batch_start_streams():
    """
    批量启动多个摄像机的流
    
    Request Body:
        {
            "camera_ids": ["uuid1", "uuid2", ...],
            "server_id": 1  // 可选
        }
    
    Returns:
        批量启动结果
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        camera_ids = data.get('camera_ids', [])
        server_id = data.get('server_id')
        
        if not camera_ids:
            return jsonify({'error': '摄像机ID列表不能为空'}), 400
        
        # 获取流管理服务
        stream_service = get_stream_service()
        
        # 批量启动流
        results = []
        success_count = 0
        fail_count = 0
        
        for camera_id in camera_ids:
            try:
                result = stream_service.start_stream(camera_id, server_id)
                results.append({
                    'camera_id': camera_id,
                    'success': True,
                    'message': result.get('message', ''),
                    'play_urls': result.get('play_urls', {})
                })
                success_count += 1
            except Exception as e:
                results.append({
                    'camera_id': camera_id,
                    'success': False,
                    'error': str(e)
                })
                fail_count += 1
        
        logger.info(f"批量启动流完成: 成功={success_count}, 失败={fail_count}")
        
        return jsonify({
            'success': True,
            'message': f'批量启动流完成: 成功{success_count}个, 失败{fail_count}个',
            'total': len(camera_ids),
            'success_count': success_count,
            'fail_count': fail_count,
            'results': results
        })
        
    except Exception as e:
        logger.error(f"批量启动流异常: {str(e)}", exc_info=True)
        return jsonify({'error': f'批量启动流失败: {str(e)}'}), 500


@stream_bp.route('/batch/stop', methods=['POST'])
def batch_stop_streams():
    """
    批量停止多个摄像机的流
    
    Request Body:
        {
            "camera_ids": ["uuid1", "uuid2", ...],
            "server_id": 1  // 可选
        }
    
    Returns:
        批量停止结果
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': '请求数据无效'}), 400
        
        camera_ids = data.get('camera_ids', [])
        server_id = data.get('server_id')
        
        if not camera_ids:
            return jsonify({'error': '摄像机ID列表不能为空'}), 400
        
        # 获取流管理服务
        stream_service = get_stream_service()
        
        # 批量停止流
        results = []
        success_count = 0
        fail_count = 0
        
        for camera_id in camera_ids:
            try:
                result = stream_service.stop_stream(camera_id, server_id)
                results.append({
                    'camera_id': camera_id,
                    'success': True,
                    'message': result.get('message', '')
                })
                success_count += 1
            except Exception as e:
                results.append({
                    'camera_id': camera_id,
                    'success': False,
                    'error': str(e)
                })
                fail_count += 1
        
        logger.info(f"批量停止流完成: 成功={success_count}, 失败={fail_count}")
        
        return jsonify({
            'success': True,
            'message': f'批量停止流完成: 成功{success_count}个, 失败{fail_count}个',
            'total': len(camera_ids),
            'success_count': success_count,
            'fail_count': fail_count,
            'results': results
        })
        
    except Exception as e:
        logger.error(f"批量停止流异常: {str(e)}", exc_info=True)
        return jsonify({'error': f'批量停止流失败: {str(e)}'}), 500


# ========================================
# Blueprint注册函数
# ========================================

def register_stream_blueprint(app):
    """
    注册流管理Blueprint到Flask应用
    
    Args:
        app: Flask应用实例
    """
    app.register_blueprint(stream_bp)
    logger.info("流管理Blueprint已注册")