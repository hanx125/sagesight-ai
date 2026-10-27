"""
跨相机跟踪单元测试

覆盖：
- 外观描述子提取与相似度（同色接近、异色远离）
- 匹配器硬门控（跨类别、跨分组、时间窗、同相机重叠）
- 融合服务端到端（同一人跨两相机串成一个身份，不同人分开）

运行：python tests/test_cross_camera.py
"""

import os
import sqlite3
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tracking.cross_camera import features as feat
from src.tracking.cross_camera import store
from src.tracking.cross_camera.config import DEFAULTS, _normalize
from src.tracking.cross_camera.matcher import CrossCameraMatcher, Tracklet


# ========================================
# 测试数据构造
# ========================================

def make_person_frame(shirt, pants, bbox=(100, 100, 200, 300), size=(480, 640)):
    """造一张 480x640 的帧，在 bbox 里画"上衣服色 + 下装颜色"的人"""
    h, w = size
    frame = np.full((h, w, 3), 60, dtype=np.uint8)
    x1, y1, x2, y2 = bbox
    mid = y1 + (y2 - y1) // 2
    frame[y1:mid, x1:x2] = shirt
    frame[mid:y2, x1:x2] = pants
    return frame


CFG = _normalize({})


def make_matcher(**overrides):
    cfg = _normalize(overrides) if overrides else dict(CFG)
    return CrossCameraMatcher(cfg)


def trk(camera, cls, start, end, feature=None, event_id=None,
        strong_id=None, zones=None):
    return Tracklet(
        event_id=event_id or f"evt-{camera}-{int(start)}",
        camera_id=camera, class_name=cls,
        start_time=start, end_time=end,
        feature=feature, strong_id=strong_id, zones=zones or [],
    )


# ========================================
# 1. 特征
# ========================================

def test_descriptor_similarity():
    frame_a = make_person_frame((220, 30, 30), (30, 30, 200))   # 红上衣蓝裤
    frame_b = make_person_frame((215, 35, 35), (35, 35, 195))   # 同色系微扰
    frame_c = make_person_frame((30, 200, 30), (220, 200, 30))  # 绿上衣黄裤

    bbox = (100, 100, 200, 300)
    desc_a = feat.extract_descriptor(frame_a, bbox)
    desc_b = feat.extract_descriptor(frame_b, bbox)
    desc_c = feat.extract_descriptor(frame_c, bbox)

    assert desc_a and desc_b and desc_c, "描述子提取失败"
    same = feat.compare(desc_a, desc_b)
    diff = feat.compare(desc_a, desc_c)

    assert same['appearance'] > 0.85, f"同色相似度过低: {same}"
    assert diff['appearance'] < 0.25, f"异色相似度过高: {diff}"
    print(f"  同人外观={same['appearance']:.3f}  异人外观={diff['appearance']:.3f}")


def test_descriptor_rejects_bad_bbox():
    frame = make_person_frame((200, 200, 200), (20, 20, 20))
    assert feat.extract_descriptor(frame, (10, 10, 11, 11)) is None
    assert feat.extract_descriptor(frame, None) is None
    assert feat.extract_descriptor(None, (0, 0, 10, 10)) is None


# ========================================
# 2. 匹配器门控
# ========================================

def test_class_mismatch_rejected():
    m = make_matcher()
    a = trk('cam1', '人', 1000, 1010)
    b = trk('cam2', '汽车', 1020, 1030)
    assert m.evaluate(a, b) is None, "跨类别必须拒绝"


def test_time_window():
    m = make_matcher(max_gap_seconds=60)
    now = 10000.0
    a = trk('cam1', '人', now, now + 5)
    ok = trk('cam2', '人', now + 30, now + 35)
    far = trk('cam2', '人', now + 300, now + 305)
    assert m.evaluate(a, ok) is not None, "时间窗内应可关联"
    assert m.evaluate(a, far) is None, "超出时间窗必须拒绝"


def test_group_gating():
    m = make_matcher(groups=[
        {'name': '一层', 'cameras': ['cam1', 'cam2']},
        {'name': '二层', 'cameras': ['cam3']},
    ])
    a = trk('cam1', '人', 1000, 1010)
    same_group = trk('cam2', '人', 1020, 1030)
    other_group = trk('cam3', '人', 1020, 1030)
    assert m.evaluate(a, same_group) is not None
    assert m.evaluate(a, other_group) is None, "跨分组必须拒绝"


def test_same_camera_rejected_by_default():
    """同相机默认不关联：现场实测同机位两辆车的车窗外形相似度能到 0.94，
    一旦允许就会把不同目标串成一条越滚越大的身份"""
    m = make_matcher()
    a = trk('cam1', '人', 1000, 1020)
    overlap = trk('cam1', '人', 1010, 1030)   # 同一相机同时出现，必是两个人
    later = trk('cam1', '人', 1030, 1040)     # 离开后再进入
    assert m.evaluate(a, overlap) is None
    assert m.evaluate(a, later) is None, "默认不应允许同相机关联"
    # 换成另一台相机则正常
    assert m.evaluate(a, trk('cam2', '人', 1030, 1040)) is not None


def test_same_camera_enabled():
    """显式打开 link_same_camera 后按"重入"规则关联（时间不重叠才允许）"""
    m = make_matcher(link_same_camera=True)
    a = trk('cam1', '人', 1000, 1020)
    overlap = trk('cam1', '人', 1010, 1030)
    later = trk('cam1', '人', 1030, 1040)
    assert m.evaluate(a, overlap) is None, "时间重叠必是两个人"
    assert m.evaluate(a, later) is not None


def test_strong_id():
    m = make_matcher(use_strong_id=True)
    a = trk('cam1', '人', 1000, 1010, strong_id='face:张三')
    b = trk('cam2', '人', 5000, 5010, strong_id='face:张三')   # 时间很远也认
    c = trk('cam2', '人', 1020, 1030, strong_id='face:李四')
    assert m.evaluate(a, b) is not None, "强 ID 一致应直接关联"
    assert m.evaluate(a, c) is None, "强 ID 冲突必须拒绝"


def test_margin_requires_unique_best():
    m = make_matcher()
    frame = make_person_frame((200, 40, 40), (40, 40, 200))
    desc = feat.extract_descriptor(frame, (100, 100, 200, 300))
    a = trk('cam1', '人', 1000, 1010, feature=desc)
    # 两条几乎一模一样的候选 → 无法区分，宁可不关联
    b1 = trk('cam2', '人', 1020, 1030, feature=desc, event_id='b1')
    b2 = trk('cam2', '人', 1021, 1031, feature=desc, event_id='b2')
    best, second = m.choose(a, [b1, b2])
    assert best is None, "两个候选过于接近时应放弃关联"


# ========================================
# 3. 端到端（临时库）
# ========================================

_SCHEMA = '''
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    task_id INTEGER,
    camera_id TEXT,
    algorithm TEXT,
    type TEXT,
    class_name TEXT,
    confidence REAL,
    bbox TEXT,
    local_image_url TEXT,
    metadata TEXT,
    start_time DATETIME,
    end_time DATETIME,
    status TEXT,
    entered_zones TEXT,
    zone_name TEXT,
    cross_identity_id TEXT,
    cross_feature TEXT
);
'''


def _fmt(epoch):
    from datetime import datetime
    return datetime.fromtimestamp(epoch).strftime('%Y-%m-%d %H:%M:%S.%f')


def test_fusion_end_to_end():
    tmpdir = tempfile.mkdtemp(prefix='cc_test_')
    db_path = os.path.join(tmpdir, 'tasks.db')
    original = store._DB_PATH
    store._DB_PATH = db_path
    store._SCHEMA_READY = False
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()
        store.ensure_schema()

        shirt, pants = (200, 40, 40), (40, 40, 200)
        stranger_shirt, stranger_pants = (40, 200, 40), (200, 200, 40)
        bbox = (100, 100, 200, 300)
        same_desc = feat.extract_descriptor(make_person_frame(shirt, pants), bbox)
        other_desc = feat.extract_descriptor(
            make_person_frame(stranger_shirt, stranger_pants), bbox)

        now = time.time()
        rows = [
            # (camera, class, start, end, feature)
            ('cam1', '人', now - 100, now - 90, same_desc),
            ('cam2', '人', now - 60, now - 50, same_desc),   # 同一人走到 cam2
            ('cam2', '人', now - 20, now - 10, other_desc),  # 另一个人
        ]
        conn = sqlite3.connect(db_path)
        for idx, (cam, cls, start, end, desc) in enumerate(rows):
            conn.execute(
                'INSERT INTO events (event_id, task_id, camera_id, class_name, '
                'confidence, start_time, end_time, status, metadata, cross_feature) '
                'VALUES (?, 1, ?, ?, 0.9, ?, ?, ?, ?, ?)',
                (f'e{idx}', cam, cls, _fmt(start), _fmt(end), 'final',
                 '{}', desc),
            )
        conn.commit()
        conn.close()

        from src.tracking.cross_camera.fusion_service import CrossCameraFusionService
        service = CrossCameraFusionService()
        service._matcher.update_config(CFG)
        processed = service._process_batch(10)
        assert processed == 3, f"应处理 3 条事件，实际 {processed}"

        conn = sqlite3.connect(db_path)
        identities = conn.execute(
            'SELECT identity_id, camera_count, event_count FROM cross_camera_identities'
        ).fetchall()
        pending = conn.execute(
            'SELECT COUNT(*) FROM events WHERE cross_identity_id IS NULL'
        ).fetchone()[0]
        links = conn.execute('SELECT COUNT(*) FROM cross_camera_links').fetchone()[0]
        conn.close()

        assert len(identities) == 2, f"应形成 2 个身份，实际 {len(identities)}"
        assert links == 3, f"应有 3 条关联记录，实际 {links}"
        assert pending == 0, "不应有事件遗漏在待融合状态"
        multi = [i for i in identities if i[1] > 1]
        assert len(multi) == 1, "应有 1 个身份跨了 2 台相机"
        assert multi[0][2] == 2, "跨相机身份应包含 2 条事件"
        print(f"  身份数={len(identities)} 关联={links} 跨相机身份={len(multi)}")
    finally:
        store._DB_PATH = original
        store._SCHEMA_READY = False


def test_pending_scan_self_heals():
    """initial 事件后转 final 时，即使 id 更小也能被后续批次捡到"""
    tmpdir = tempfile.mkdtemp(prefix='cc_test2_')
    db_path = os.path.join(tmpdir, 'tasks.db')
    original = store._DB_PATH
    store._DB_PATH = db_path
    store._SCHEMA_READY = False
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()
        store.ensure_schema()

        now = time.time()
        conn = sqlite3.connect(db_path)
        conn.execute(
            'INSERT INTO events (event_id, camera_id, class_name, start_time, '
            'end_time, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('e1', 'cam1', '人', _fmt(now - 30), _fmt(now - 20), 'initial', '{}'),
        )
        conn.execute(
            'INSERT INTO events (event_id, camera_id, class_name, start_time, '
            'end_time, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('e2', 'cam1', '人', _fmt(now - 10), _fmt(now), 'final', '{}'),
        )
        conn.commit()
        # e2 先被处理，e1 之后才变 final
        store.set_event_identity(['e2'], 'x')
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE events SET status='final' WHERE event_id='e1'")
        conn.commit()
        conn.close()

        pending = store.fetch_pending_events(10)
        assert [p['event_id'] for p in pending] == ['e1'], \
            f"迟到的 final 事件应被捡到，实际 {[p['event_id'] for p in pending]}"
    finally:
        store._DB_PATH = original
        store._SCHEMA_READY = False


def test_singleton_purged_after_ttl():
    """只挂了一段、始终没等到跨机同伴的身份到期应被回收，
    否则轨迹页会被"单点身份"刷满噪声"""
    tmpdir = tempfile.mkdtemp(prefix='cc_test3_')
    db_path = os.path.join(tmpdir, 'tasks.db')
    original = store._DB_PATH
    store._DB_PATH = db_path
    store._SCHEMA_READY = False
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()
        store.ensure_schema()

        now = time.time()
        conn = sqlite3.connect(db_path)
        conn.execute(
            'INSERT INTO events (event_id, camera_id, class_name, start_time, '
            'end_time, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('lonely', 'cam1', '人', _fmt(now - 30), _fmt(now - 20), 'final', '{}'),
        )
        conn.commit()
        conn.close()

        from src.tracking.cross_camera.fusion_service import CrossCameraFusionService
        service = CrossCameraFusionService()
        service._matcher.update_config(CFG)
        service._process_batch(10)

        conn = sqlite3.connect(db_path)
        status = conn.execute(
            'SELECT status FROM cross_camera_identities'
        ).fetchone()
        conn.close()
        assert status and status[0] == 'singleton', f"单段身份应为 singleton，实际 {status}"

        store.close_stale_identities(ttl_minutes=0.01)   # 立刻过期
        conn = sqlite3.connect(db_path)
        left = conn.execute('SELECT COUNT(*) FROM cross_camera_identities').fetchone()[0]
        links = conn.execute('SELECT COUNT(*) FROM cross_camera_links').fetchone()[0]
        mark = conn.execute(
            "SELECT cross_identity_id FROM events WHERE event_id='lonely'"
        ).fetchone()[0]
        conn.close()
        assert left == 0 and links == 0, "过期单点身份应被删除"
        assert mark == store.EXCLUDED_ID, f"事件应标记为不参与，实际 {mark}"
        print(f"  单点身份已回收，事件标记={mark}")
    finally:
        store._DB_PATH = original
        store._SCHEMA_READY = False


# ========================================
# 运行
# ========================================

def main():
    tests = [
        ('外观特征相似度', test_descriptor_similarity),
        ('非法 bbox 容错', test_descriptor_rejects_bad_bbox),
        ('跨类别拒绝', test_class_mismatch_rejected),
        ('通行时间窗', test_time_window),
        ('区域分组门控', test_group_gating),
        ('同相机默认不关联', test_same_camera_rejected_by_default),
        ('同相机关联开关', test_same_camera_enabled),
        ('强 ID 关联', test_strong_id),
        ('候选接近时放弃', test_margin_requires_unique_best),
        ('端到端融合', test_fusion_end_to_end),
        ('迟到事件自愈', test_pending_scan_self_heals),
        ('单点身份过期回收', test_singleton_purged_after_ttl),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERR ] {name}: {type(e).__name__}: {e}")
    print(f"\n{'全部通过' if failed == 0 else f'{failed} 项失败'}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
