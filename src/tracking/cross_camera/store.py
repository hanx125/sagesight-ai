"""
跨相机身份与关联的存储层

设计要点：
- 身份表冗余保存"最近一次轨迹片段"（anchor_*），候选查询就是一次带索引的
  单表扫描，不用 JOIN 事件表，16 路高并发下也扛得住。
- events.cross_identity_id 三态：
      NULL  = 待融合（新事件）
      '-'   = 历史遗留，不参与（启用功能前的旧事件）
      uuid  = 已关联到某个身份
  这样融合服务不需要维护游标也能自愈：事件中途从 initial 变 final 也不会漏。
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DB_PATH = _PROJECT_ROOT / 'data' / 'tasks.db'

_INIT_LOCK = threading.Lock()
_SCHEMA_READY = False

EXCLUDED_ID = '-'   # 历史事件标记：不参与跨相机关联

# 建表语句（幂等）。集中定义，供 store.ensure_schema 与 TaskManager 初始化共用，
# 避免两处 DDL 漂移。
SCHEMA_STATEMENTS: List[str] = [
    '''
    CREATE TABLE IF NOT EXISTS cross_camera_identities (
        identity_id TEXT PRIMARY KEY,
        class_name TEXT,
        family TEXT DEFAULT 'other',
        strong_id TEXT DEFAULT '',
        first_seen REAL DEFAULT 0,
        last_seen REAL DEFAULT 0,
        event_count INTEGER DEFAULT 0,
        camera_count INTEGER DEFAULT 0,
        cameras TEXT DEFAULT '[]',
        best_image_url TEXT DEFAULT '',
        status TEXT DEFAULT 'active',
        anchor_event_id TEXT DEFAULT '',
        anchor_camera_id TEXT DEFAULT '',
        anchor_start REAL DEFAULT 0,
        anchor_end REAL DEFAULT 0,
        anchor_feature TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    ''',
    'CREATE INDEX IF NOT EXISTS ix_identity_match '
    'ON cross_camera_identities (class_name, last_seen)',
    '''
    CREATE TABLE IF NOT EXISTS cross_camera_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        camera_id TEXT NOT NULL,
        class_name TEXT,
        start_time REAL DEFAULT 0,
        end_time REAL DEFAULT 0,
        score REAL DEFAULT 0,
        reason TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    ''',
    'CREATE UNIQUE INDEX IF NOT EXISTS ux_link_event ON cross_camera_links (event_id)',
    'CREATE INDEX IF NOT EXISTS ix_link_identity ON cross_camera_links (identity_id, start_time)',
    'CREATE INDEX IF NOT EXISTS ix_events_cross_pending ON events (status, cross_identity_id, id)',
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA busy_timeout=15000')
    except Exception:
        pass
    return conn


# ========================================
# 建表 / 迁移
# ========================================

def _ensure_event_columns(conn: sqlite3.Connection) -> None:
    """补齐 events 表上的跨相机关联列

    TaskManager 启动时也会迁移这两列，但融合服务的启动顺序不一定在
    TaskManager 之后。这里自带兜底，避免"列不存在 → 索引建不了 →
    历史事件标记失败 → 把旧事件全部重新融合一遍"。
    """
    try:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(events)')}
    except sqlite3.OperationalError:
        return  # events 表还没建，交给 TaskManager
    if not columns:
        return
    if 'cross_identity_id' not in columns:
        conn.execute('ALTER TABLE events ADD COLUMN cross_identity_id TEXT')
        logger.info("[CrossCamera] events 表补齐 cross_identity_id 列")
    if 'cross_feature' not in columns:
        conn.execute('ALTER TABLE events ADD COLUMN cross_feature TEXT')
        logger.info("[CrossCamera] events 表补齐 cross_feature 列")


def ensure_schema() -> None:
    """建表（幂等，首次调用后进程内不再重复）"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _INIT_LOCK:
        if _SCHEMA_READY:
            return
        try:
            with _connect() as conn:
                _ensure_event_columns(conn)
                for statement in SCHEMA_STATEMENTS:
                    try:
                        conn.execute(statement)
                    except sqlite3.OperationalError as e:
                        # events 表还没迁移出 cross_identity_id 时，待处理索引建不了，忽略
                        logger.debug(f"[CrossCamera] 建表语句跳过: {e}")
                conn.commit()
            _SCHEMA_READY = True
            logger.info("[CrossCamera] 身份/关联表已就绪")
        except Exception as e:
            logger.error(f"[CrossCamera] 建表失败: {e}")


def mark_history_excluded(get_setting, set_setting) -> int:
    """首次启用时把历史事件标记为不参与（避免把 2 万条旧事件重新串一遍）

    Args:
        get_setting/set_setting: SystemManager 的设置读写函数（复用同一份配置表）

    Returns:
        被标记的历史事件数
    """
    ensure_schema()
    try:
        if get_setting('cross_camera_inited', '') == '1':
            return 0
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE events SET cross_identity_id = ? "
                "WHERE cross_identity_id IS NULL AND status = 'final'",
                (EXCLUDED_ID,),
            )
            count = cur.rowcount
            conn.commit()
        set_setting('cross_camera_inited', '1')
        if count:
            logger.info(f"[CrossCamera] 首次启用：{count} 条历史事件已标记为不参与关联")
        return count
    except Exception as e:
        logger.warning(f"[CrossCamera] 历史事件标记失败: {e}")
        return 0


# ========================================
# 待处理事件
# ========================================

def fetch_pending_events(limit: int = 100) -> List[Dict[str, Any]]:
    """取待融合的最终事件（按 id 升序，保证时序）"""
    ensure_schema()
    try:
        with _connect() as conn:
            rows = conn.execute(
                '''
                SELECT id, event_id, task_id, camera_id, class_name, confidence,
                       start_time, end_time, local_image_url, metadata,
                       cross_feature, entered_zones, zone_name, bbox,
                       image_width, image_height
                FROM events
                WHERE status = 'final' AND cross_identity_id IS NULL
                ORDER BY id ASC
                LIMIT ?
                ''',
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError as e:
        # cross_feature 列可能还没迁移完，降级只查基础列
        logger.debug(f"[CrossCamera] 待处理事件查询降级: {e}")
        try:
            with _connect() as conn:
                rows = conn.execute(
                    '''
                    SELECT id, event_id, task_id, camera_id, class_name, confidence,
                           start_time, end_time, local_image_url, metadata,
                           entered_zones, zone_name, bbox
                    FROM events
                    WHERE status = 'final' AND cross_identity_id IS NULL
                    ORDER BY id ASC
                    LIMIT ?
                    ''',
                    (int(limit),),
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.warning(f"[CrossCamera] 取待处理事件失败: {exc}")
            return []
    except Exception as e:
        logger.warning(f"[CrossCamera] 取待处理事件失败: {e}")
        return []


def set_event_identity(event_ids: List[str], identity_id: str) -> None:
    """回填事件的跨相机身份（'-' 表示不参与）"""
    if not event_ids:
        return
    try:
        with _connect() as conn:
            conn.executemany(
                'UPDATE events SET cross_identity_id = ? WHERE event_id = ?',
                [(identity_id, eid) for eid in event_ids],
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[CrossCamera] 回填事件身份失败: {e}")


def set_event_feature(event_id: str, feature: str) -> None:
    """回写外观特征（融合线程补算后落库，下次重建不必重算）"""
    if not event_id:
        return
    try:
        with _connect() as conn:
            conn.execute(
                'UPDATE events SET cross_feature = ? WHERE event_id = ?',
                (feature or '', event_id),
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"[CrossCamera] 回写外观特征失败: {e}")


def clear_features(since: float = None) -> int:
    """清空外观特征，让融合线程按当前后端重算

    切换特征后端（直方图 ↔ ReID）后必须走这一步，否则新旧特征版本混在
    一起，跨版本比较会被判定为不可比，外观维度直接失效。

    Args:
        since: 只清该时间点之后结束的事件；None 表示全清

    Returns:
        清理条数
    """
    ensure_schema()
    try:
        with _connect() as conn:
            if since:
                cur = conn.execute(
                    "UPDATE events SET cross_feature = '' "
                    "WHERE end_time >= datetime(?, 'unixepoch') "
                    "AND cross_feature IS NOT NULL AND cross_feature != ''",
                    (float(since),),
                )
            else:
                cur = conn.execute(
                    "UPDATE events SET cross_feature = '' "
                    "WHERE cross_feature IS NOT NULL AND cross_feature != ''"
                )
            count = cur.rowcount
            conn.commit()
        return count
    except Exception as e:
        logger.warning(f"[CrossCamera] 清空外观特征失败: {e}")
        return 0


# ========================================
# 身份
# ========================================

def fetch_candidate_identities(class_name: str, since: float,
                               limit: int = 60,
                               exclude_camera: str = '') -> List[Dict[str, Any]]:
    """取同类别、最近活跃的候选身份（按最近出现时间倒序）

    包含 singleton（只挂了一段轨迹、还没等到跨机同伴的身份）——这正是
    跨相机串联的起点，不能因为"只有一段"就被排除。

    exclude_camera 用于关掉同相机关联时排除"锚点正好落在本相机"的身份：
    既省得分，也避免繁忙相机把候选窗口（limit）占满，让真正该匹配的
    另一台相机候选被挤出去。
    """
    ensure_schema()
    sql = ("SELECT * FROM cross_camera_identities "
           "WHERE class_name = ? AND last_seen >= ? "
           "AND status IN ('active', 'singleton')")
    params: List[Any] = [class_name, float(since)]
    if exclude_camera:
        sql += ' AND anchor_camera_id != ?'
        params.append(str(exclude_camera))
    sql += ' ORDER BY last_seen DESC LIMIT ?'
    params.append(int(limit))
    try:
        with _connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.warning(f"[CrossCamera] 查询候选身份失败: {e}")
        return []


def get_identity(identity_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    try:
        with _connect() as conn:
            row = conn.execute(
                'SELECT * FROM cross_camera_identities WHERE identity_id = ?',
                (identity_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def create_identity(tracklet, family: str, image_url: str = '') -> str:
    """新建身份（同时写入首条关联记录）"""
    ensure_schema()
    identity_id = uuid.uuid4().hex
    cameras = [str(tracklet.camera_id)]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with _connect() as conn:
            conn.execute(
                '''
                INSERT INTO cross_camera_identities
                (identity_id, class_name, family, strong_id, first_seen, last_seen,
                 event_count, camera_count, cameras, best_image_url, status,
                 anchor_event_id, anchor_camera_id, anchor_start, anchor_end,
                 anchor_feature, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, 'singleton', ?, ?, ?, ?, ?, ?, ?)
                ''',
                (identity_id, tracklet.class_name, family, tracklet.strong_id or '',
                 tracklet.start_time, tracklet.end_time,
                 json.dumps(cameras, ensure_ascii=False),
                 image_url or tracklet.image_url or '',
                 tracklet.event_id, str(tracklet.camera_id),
                 tracklet.start_time, tracklet.end_time,
                 tracklet.feature or '', now, now),
            )
            conn.execute(
                '''
                INSERT OR IGNORE INTO cross_camera_links
                (identity_id, event_id, camera_id, class_name, start_time, end_time,
                 score, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1.0, '新建身份', ?)
                ''',
                (identity_id, tracklet.event_id, str(tracklet.camera_id),
                 tracklet.class_name, tracklet.start_time, tracklet.end_time, now),
            )
            conn.commit()
        return identity_id
    except Exception as e:
        logger.warning(f"[CrossCamera] 新建身份失败: {e}")
        return ''


def attach_to_identity(identity_id: str, tracklet, score: float,
                       reason: str, image_url: str = '') -> bool:
    """把新轨迹片段挂到已有身份上，并更新身份的锚点与统计"""
    ensure_schema()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with _connect() as conn:
            cur = conn.execute(
                '''
                INSERT OR IGNORE INTO cross_camera_links
                (identity_id, event_id, camera_id, class_name, start_time, end_time,
                 score, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (identity_id, tracklet.event_id, str(tracklet.camera_id),
                 tracklet.class_name, tracklet.start_time, tracklet.end_time,
                 float(score), reason, now),
            )
            if cur.rowcount == 0:
                return False  # 已关联过，幂等跳过

            identity = conn.execute(
                'SELECT * FROM cross_camera_identities WHERE identity_id = ?',
                (identity_id,),
            ).fetchone()
            if identity is None:
                return False

            cameras = []
            try:
                cameras = json.loads(identity['cameras'] or '[]')
            except (json.JSONDecodeError, TypeError):
                cameras = []
            if str(tracklet.camera_id) not in cameras:
                cameras.append(str(tracklet.camera_id))

            # 锚点始终指向时间上最新的那条轨迹片段，供下一轮匹配比对
            anchor_is_newer = tracklet.end_time >= (identity['anchor_end'] or 0)
            best_image = identity['best_image_url'] or ''
            if not best_image and (image_url or tracklet.image_url):
                best_image = image_url or tracklet.image_url

            if anchor_is_newer:
                conn.execute(
                    '''
                    UPDATE cross_camera_identities SET
                        last_seen = MAX(COALESCE(last_seen, 0), ?),
                        event_count = COALESCE(event_count, 0) + 1,
                        camera_count = ?,
                        cameras = ?,
                        best_image_url = ?,
                        strong_id = CASE WHEN COALESCE(strong_id, '') = '' THEN ? ELSE strong_id END,
                        anchor_event_id = ?, anchor_camera_id = ?,
                        anchor_start = ?, anchor_end = ?, anchor_feature = ?,
                        status = 'active',
                        updated_at = ?
                    WHERE identity_id = ?
                    ''',
                    (tracklet.end_time, len(cameras), json.dumps(cameras, ensure_ascii=False),
                     best_image, tracklet.strong_id or '',
                     tracklet.event_id, str(tracklet.camera_id),
                     tracklet.start_time, tracklet.end_time, tracklet.feature or '',
                     now, identity_id),
                )
            else:
                conn.execute(
                    '''
                    UPDATE cross_camera_identities SET
                        first_seen = MIN(COALESCE(first_seen, 0), ?),
                        event_count = COALESCE(event_count, 0) + 1,
                        camera_count = ?,
                        cameras = ?,
                        best_image_url = ?,
                        strong_id = CASE WHEN COALESCE(strong_id, '') = '' THEN ? ELSE strong_id END,
                        status = 'active',
                        updated_at = ?
                    WHERE identity_id = ?
                    ''',
                    (tracklet.start_time, len(cameras),
                     json.dumps(cameras, ensure_ascii=False), best_image,
                     tracklet.strong_id or '', now, identity_id),
                )
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[CrossCamera] 关联身份失败: {e}")
        return False


def close_stale_identities(ttl_minutes: float) -> int:
    """到期身份处理

    - active   → 'closed'（仍在库里，可查询、可被后续片段复活）
    - singleton→ 直接删除：只挂了一段、始终没等到跨机同伴，留着只会把轨迹页刷满噪声；
                 对应事件标为 '-' 不再重新排队
    """
    ensure_schema()
    deadline = time.time() - float(ttl_minutes) * 60.0
    try:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE cross_camera_identities SET status = 'closed' "
                "WHERE status = 'active' AND last_seen < ?",
                (deadline,),
            )
            count = cur.rowcount

            stale_singletons = [
                row[0] for row in conn.execute(
                    "SELECT identity_id FROM cross_camera_identities "
                    "WHERE status = 'singleton' AND last_seen < ?",
                    (deadline,),
                ).fetchall()
            ]
            if stale_singletons:
                marks = ','.join('?' * len(stale_singletons))
                conn.execute(
                    f'UPDATE events SET cross_identity_id = ? WHERE event_id IN '
                    f'(SELECT event_id FROM cross_camera_links '
                    f'WHERE identity_id IN ({marks}))',
                    [EXCLUDED_ID] + stale_singletons,
                )
                conn.execute(
                    f'DELETE FROM cross_camera_links WHERE identity_id IN ({marks})',
                    stale_singletons,
                )
                conn.execute(
                    f'DELETE FROM cross_camera_identities WHERE identity_id IN ({marks})',
                    stale_singletons,
                )
            conn.commit()
        if stale_singletons:
            logger.debug(f"[CrossCamera] 清理单点身份 {len(stale_singletons)} 个")
        return count
    except Exception as e:
        logger.debug(f"[CrossCamera] 关闭过期身份失败: {e}")
        return 0


# ========================================
# 查询（供 API 使用）
# ========================================

def list_identities(page: int = 1, page_size: int = 20,
                    class_name: str = '', camera_id: str = '',
                    start_time: Optional[float] = None,
                    end_time: Optional[float] = None,
                    status: str = '', min_events: int = 1) -> Tuple[List[Dict], int]:
    """身份列表（分页）"""
    ensure_schema()
    where = []
    params: List[Any] = []
    if class_name:
        where.append('class_name = ?')
        params.append(class_name)
    if status:
        where.append('status = ?')
        params.append(status)
    if min_events and int(min_events) > 1:
        where.append('event_count >= ?')
        params.append(int(min_events))
    if start_time:
        where.append('last_seen >= ?')
        params.append(float(start_time))
    if end_time:
        where.append('first_seen <= ?')
        params.append(float(end_time))
    if camera_id:
        # cameras 是 JSON 数组文本，用 LIKE 做包含判断（身份数量级不大，够用）
        where.append('cameras LIKE ?')
        params.append(f'%"{camera_id}"%')

    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    offset = (page - 1) * page_size

    try:
        with _connect() as conn:
            total = conn.execute(
                f'SELECT COUNT(*) FROM cross_camera_identities {clause}', params
            ).fetchone()[0]
            rows = conn.execute(
                f'''SELECT * FROM cross_camera_identities {clause}
                    ORDER BY last_seen DESC LIMIT ? OFFSET ?''',
                params + [page_size, offset],
            ).fetchall()
        return [dict(row) for row in rows], int(total)
    except Exception as e:
        logger.warning(f"[CrossCamera] 身份列表查询失败: {e}")
        return [], 0


def list_links(identity_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """某个身份下的全部轨迹片段（按时间正序）"""
    ensure_schema()
    try:
        with _connect() as conn:
            rows = conn.execute(
                '''
                SELECT l.*, e.local_image_url, e.confidence, e.entered_zones,
                       e.ai_description
                FROM cross_camera_links l
                LEFT JOIN events e ON e.event_id = l.event_id
                WHERE l.identity_id = ?
                ORDER BY l.start_time ASC
                LIMIT ?
                ''',
                (identity_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.warning(f"[CrossCamera] 关联明细查询失败: {e}")
        return []


def stats_summary() -> Dict[str, Any]:
    """概览数字（轨迹页顶部）

    identity_count 只统计"真的串起来了"的轨迹（≥2 段），
    单点身份只是等待跨机同伴的中间态，计入会让数字虚高。
    """
    ensure_schema()
    try:
        with _connect() as conn:
            identity_count = conn.execute(
                'SELECT COUNT(*) FROM cross_camera_identities WHERE event_count >= 2'
            ).fetchone()[0]
            singleton_count = conn.execute(
                "SELECT COUNT(*) FROM cross_camera_identities WHERE event_count < 2"
            ).fetchone()[0]
            multi_camera = conn.execute(
                'SELECT COUNT(*) FROM cross_camera_identities WHERE camera_count > 1'
            ).fetchone()[0]
            link_count = conn.execute('SELECT COUNT(*) FROM cross_camera_links').fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM events WHERE status = 'final' AND cross_identity_id IS NULL"
            ).fetchone()[0]
        return {
            'identity_count': identity_count,
            'singleton_count': singleton_count,
            'multi_camera_count': multi_camera,
            'link_count': link_count,
            'pending_events': pending,
        }
    except Exception:
        return {'identity_count': 0, 'singleton_count': 0, 'multi_camera_count': 0,
                'link_count': 0, 'pending_events': 0}
