"""
跨相机跟踪 HTTP 接口

- 轨迹列表 / 详情（含多相机时间轴）
- 配置读写（分组、时间窗、阈值）
- 相机列表（供分组选择）
- 重建（把最近一段时间的事件重新融合一遍）
"""

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from src.tracking.cross_camera.config import (
    get_cross_camera_config,
    save_cross_camera_config,
)
from src.tracking.cross_camera import store

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DB_PATH = _PROJECT_ROOT / 'data' / 'tasks.db'


# ========================================
# 辅助
# ========================================

def _fmt_time(epoch) -> str:
    """epoch → 'YYYY-MM-DD HH:MM:SS'"""
    try:
        value = float(epoch or 0)
    except (TypeError, ValueError):
        return ''
    if value <= 0:
        return ''
    return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')


def _camera_name_map() -> Dict[str, str]:
    """camera_id → 相机名称"""
    mapping: Dict[str, str] = {}
    try:
        with sqlite3.connect(str(_DB_PATH), timeout=10) as conn:
            for row in conn.execute('SELECT id, name FROM cameras'):
                mapping[str(row[0])] = row[1] or str(row[0])
    except Exception as e:
        logger.debug(f"[CrossCamera] 读取相机名称失败: {e}")
    return mapping


def _decorate_identity(row: Dict[str, Any], camera_names: Dict[str, str]) -> Dict[str, Any]:
    """补上相机名称与可读时间"""
    import json
    cameras: List[str] = []
    try:
        cameras = json.loads(row.get('cameras') or '[]')
    except (json.JSONDecodeError, TypeError):
        cameras = []
    return {
        'identity_id': row.get('identity_id'),
        'class_name': row.get('class_name') or '',
        'family': row.get('family') or 'other',
        'status': row.get('status') or 'active',
        'event_count': row.get('event_count') or 0,
        'camera_count': row.get('camera_count') or len(cameras),
        'cameras': cameras,
        'camera_names': [camera_names.get(c, c) for c in cameras],
        'first_seen': row.get('first_seen') or 0,
        'last_seen': row.get('last_seen') or 0,
        'first_seen_text': _fmt_time(row.get('first_seen')),
        'last_seen_text': _fmt_time(row.get('last_seen')),
        'best_image_url': row.get('best_image_url') or '',
        'strong_id': row.get('strong_id') or '',
    }


# ========================================
# 蓝图注册
# ========================================

def register_cross_camera_blueprint(app):
    """注册跨相机跟踪接口"""
    bp = Blueprint('cross_camera', __name__)

    @bp.route('/api/cross-camera/stats', methods=['GET'])
    def cross_camera_stats():
        data = store.stats_summary()
        cfg = get_cross_camera_config()
        data['enabled'] = bool(cfg.get('enabled'))
        return jsonify(data)

    @bp.route('/api/cross-camera/identities', methods=['GET'])
    def cross_camera_identities():
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        class_name = (request.args.get('class_name') or '').strip()
        camera_id = (request.args.get('camera_id') or '').strip()
        status = (request.args.get('status') or '').strip()
        # 默认只看"真的串起来了"的轨迹（≥2 段）；min_events=1 时包含单点身份
        min_events = request.args.get('min_events', 2, type=int)
        multi_only = request.args.get('multi_camera') == '1'

        start_time = request.args.get('start_time', type=float)
        end_time = request.args.get('end_time', type=float)

        rows, total = store.list_identities(
            page=page, page_size=page_size, class_name=class_name,
            camera_id=camera_id, start_time=start_time, end_time=end_time,
            status=status, min_events=(2 if multi_only else min_events),
        )
        camera_names = _camera_name_map()
        items = [_decorate_identity(row, camera_names) for row in rows]
        return jsonify({
            'items': items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'pages': max(1, (total + page_size - 1) // page_size),
        })

    @bp.route('/api/cross-camera/identities/<identity_id>', methods=['GET'])
    def cross_camera_identity_detail(identity_id: str):
        identity = store.get_identity(identity_id)
        if not identity:
            return jsonify({'error': '轨迹不存在'}), 404
        camera_names = _camera_name_map()
        links = store.list_links(identity_id)
        timeline = []
        for link in links:
            camera_id = str(link.get('camera_id') or '')
            timeline.append({
                'event_id': link.get('event_id'),
                'camera_id': camera_id,
                'camera_name': camera_names.get(camera_id, camera_id),
                'class_name': link.get('class_name') or '',
                'start_time': link.get('start_time') or 0,
                'end_time': link.get('end_time') or 0,
                'start_text': _fmt_time(link.get('start_time')),
                'end_text': _fmt_time(link.get('end_time')),
                'duration': round(max(0.0, float(link.get('end_time') or 0)
                                      - float(link.get('start_time') or 0)), 1),
                'score': link.get('score') or 0,
                'reason': link.get('reason') or '',
                'image_url': link.get('local_image_url') or '',
                'confidence': link.get('confidence') or 0,
                'zones': link.get('entered_zones') or '',
                'ai_description': link.get('ai_description') or '',
            })
        detail = _decorate_identity(identity, camera_names)
        detail['timeline'] = timeline
        detail['duration_seconds'] = round(
            max(0.0, float(identity.get('last_seen') or 0)
                - float(identity.get('first_seen') or 0)), 1)
        return jsonify(detail)

    @bp.route('/api/cross-camera/config', methods=['GET'])
    def cross_camera_config_get():
        return jsonify(get_cross_camera_config())

    @bp.route('/api/cross-camera/config', methods=['PUT'])
    def cross_camera_config_put():
        data = request.get_json() or {}
        try:
            saved = save_cross_camera_config(data)
        except Exception as e:
            logger.error(f"[CrossCamera] 保存配置失败: {e}")
            return jsonify({'error': f'保存失败: {e}'}), 500

        # 开关从关变开时，确保历史事件已标记，避免把旧事件重新串一遍
        try:
            from src.system.system_manager import get_system_manager
            manager = get_system_manager()
            store.mark_history_excluded(manager.get_setting, manager.set_setting)
        except Exception as e:
            logger.debug(f"[CrossCamera] 历史标记失败: {e}")

        return jsonify({'message': '跨相机跟踪配置已保存', 'config': saved})

    @bp.route('/api/cross-camera/cameras', methods=['GET'])
    def cross_camera_cameras():
        """相机列表（分组配置用）：以 cameras 表为准，补充事件里出现过的相机"""
        camera_names = _camera_name_map()
        items = [{'id': cid, 'name': name} for cid, name in camera_names.items()]
        # 事件里出现但 cameras 表没有的（通道直连），也列出来
        try:
            with sqlite3.connect(str(_DB_PATH), timeout=10) as conn:
                recent = conn.execute(
                    'SELECT DISTINCT camera_id FROM events ORDER BY id DESC LIMIT 2000'
                ).fetchall()
            known = {item['id'] for item in items}
            for (cid,) in recent:
                cid = str(cid or '')
                if cid and cid not in known:
                    items.append({'id': cid, 'name': cid[:8]})
                    known.add(cid)
        except Exception as e:
            logger.debug(f"[CrossCamera] 读取事件相机失败: {e}")
        items.sort(key=lambda i: i['name'])
        return jsonify({'items': items})

    @bp.route('/api/cross-camera/rebuild', methods=['POST'])
    def cross_camera_rebuild():
        """重建最近一段时间的事件关联（默认 6 小时，最多 72 小时）

        可选 recalc_features=true：先把这批事件的外观特征清空，让融合线程
        按当前后端（直方图 / ReID）重新提取。切换特征后端、或重新校准过
        中心化均值之后必须这么做，否则新旧特征不可比，外观维度会直接失效。
        """
        data = request.get_json() or {}
        try:
            hours = float(data.get('hours', 6))
        except (TypeError, ValueError):
            hours = 6.0
        hours = max(0.5, min(72.0, hours))
        since = time.time() - hours * 3600
        recalc_features = bool(data.get('recalc_features'))

        cleared = 0
        if recalc_features:
            cleared = store.clear_features(since)

        try:
            with sqlite3.connect(str(_DB_PATH), timeout=20) as conn:
                rows = conn.execute(
                    '''
                    SELECT event_id FROM events
                    WHERE end_time >= datetime(?, 'unixepoch')
                      AND cross_identity_id IS NOT NULL
                      AND cross_identity_id != ?
                    ''',
                    (since, store.EXCLUDED_ID),
                ).fetchall()
                event_ids = [r[0] for r in rows]

                if event_ids:
                    # 1) 拆掉这些事件原有的关联记录
                    conn.executemany(
                        'DELETE FROM cross_camera_links WHERE event_id = ?',
                        [(eid,) for eid in event_ids],
                    )
                    # 2) 事件回到"待融合"状态，融合服务会按 id 顺序重新处理
                    conn.executemany(
                        'UPDATE events SET cross_identity_id = NULL WHERE event_id = ?',
                        [(eid,) for eid in event_ids],
                    )
                    # 3) 清理已经没有关联记录的空身份
                    removed = conn.execute(
                        'DELETE FROM cross_camera_identities '
                        'WHERE identity_id NOT IN '
                        '(SELECT DISTINCT identity_id FROM cross_camera_links)'
                    ).rowcount
                else:
                    removed = 0
                conn.commit()
        except Exception as e:
            logger.error(f"[CrossCamera] 重建失败: {e}")
            return jsonify({'error': f'重建失败: {e}'}), 500

        logger.info(
            f"[CrossCamera] 重建已提交：{len(event_ids)} 条事件重新排队，"
            f'清理 {removed} 个空身份，重算外观特征 {cleared} 条（最近 {hours:g} 小时）'
        )
        return jsonify({
            'message': f'{len(event_ids)} 条事件已重新排队，融合服务会按时间顺序重新关联',
            'requeued': len(event_ids),
            'removed_identities': removed,
            'features_cleared': cleared,
        })

    app.register_blueprint(bp)
    logger.info("[CrossCamera] 接口已注册: /api/cross-camera/*")
    return bp
