"""
人员统计 - 计数状态存储（按天复位）

- 计数状态持久化到数据库 counting_stats 表，键 = (task_id, zone_name, channel_id, count_date)
- 按天复位：所有读写都以今天的 count_date 为键，跨天自动归零（历史日期数据保留）
- SQLite 启用 WAL，进程内单连接，降低写库延迟与锁竞争
"""
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional

import logging

logger = logging.getLogger(__name__)


class CountingStatsStore:
    """人员统计计数状态存储（跨进程读取同一 SQLite 文件）"""

    def __init__(self, db_path: str = "data/tasks.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self._ensure_tables()
        self._last_date = self._today()

    def _ensure_tables(self):
        with self._lock:
            self._conn.execute('''
                CREATE TABLE IF NOT EXISTS counting_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    zone_name VARCHAR(100) NOT NULL,
                    channel_id INTEGER NOT NULL,
                    count_date VARCHAR(10) NOT NULL,
                    in_count INTEGER DEFAULT 0,
                    out_count INTEGER DEFAULT 0,
                    current_count INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self._conn.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_counting_stats_unique
                ON counting_stats (task_id, zone_name, channel_id, count_date)
            ''')
            self._conn.commit()

    def _today(self) -> str:
        return datetime.now().strftime('%Y-%m-%d')

    def today(self) -> str:
        return self._today()

    def _ensure_day(self):
        """检测跨天切换：内存态按天复位调用方负责，这里仅在日期变化时提示"""
        d = self._today()
        if d != self._last_date:
            self._last_date = d
            return True
        return False

    def increment(self, task_id, zone_name, channel_id, direction: str,
                  count_date: Optional[str] = None) -> Dict:
        """给某区域某通道计数 +1（direction: 'in' | 'out'）

        返回写入后该 (区域,通道) 当日的 {in_count, out_count, current_count}
        """
        d = count_date or self._today()
        if direction not in ('in', 'out'):
            raise ValueError(f"非法计数方向: {direction}")
        in_delta = 1 if direction == 'in' else 0
        out_delta = 1 if direction == 'out' else 0
        with self._lock:
            self._conn.execute('''
                INSERT INTO counting_stats
                    (task_id, zone_name, channel_id, count_date, in_count, out_count, current_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, zone_name, channel_id, count_date) DO UPDATE SET
                    in_count = in_count + ?,
                    out_count = out_count + ?,
                    current_count = in_count + ? - (out_count + ?),
                    updated_at = ?
            ''', (task_id, zone_name, channel_id, d,
                  in_delta, out_delta, in_delta - out_delta,
                  datetime.now().isoformat(),
                  in_delta, out_delta, in_delta, out_delta, datetime.now().isoformat()))
            self._conn.commit()
            cur = self._conn.execute('''
                SELECT in_count, out_count, current_count
                FROM counting_stats
                WHERE task_id=? AND zone_name=? AND channel_id=? AND count_date=?
            ''', (task_id, zone_name, channel_id, d))
            row = cur.fetchone()
            return {
                'in_count': int(row[0]),
                'out_count': int(row[1]),
                'current_count': int(row[2]),
            }

    def get_zone_summary(self, task_id, zone_name,
                         count_date: Optional[str] = None) -> Dict:
        """汇总某区域当日的进/出/当前人数（跨通道加总）"""
        d = count_date or self._today()
        with self._lock:
            cur = self._conn.execute('''
                SELECT COALESCE(SUM(in_count),0), COALESCE(SUM(out_count),0),
                       COALESCE(SUM(current_count),0)
                FROM counting_stats
                WHERE task_id=? AND zone_name=? AND count_date=?
            ''', (task_id, zone_name, d))
            row = cur.fetchone()
            return {
                'zone_name': zone_name,
                'in_count': int(row[0]),
                'out_count': int(row[1]),
                'current_count': int(row[2]),
                'date': d,
            }

    def get_task_total(self, task_id, count_date: Optional[str] = None) -> Dict:
        """任务级汇总：遍历该任务所有计数区域，返回各区域明细 + 总人数

        总人数 task_total = Σ区域 current_count（各区域进入-离开 之和）
        """
        d = count_date or self._today()
        with self._lock:
            cur = self._conn.execute('''
                SELECT zone_name, SUM(in_count), SUM(out_count), SUM(current_count)
                FROM counting_stats
                WHERE task_id=? AND count_date=?
                GROUP BY zone_name
            ''', (task_id, d))
            rows = cur.fetchall()
        zones = [
            {
                'zone_name': r[0],
                'in_count': int(r[1]),
                'out_count': int(r[2]),
                'current_count': int(r[3]),
            }
            for r in rows
        ]
        total = sum(z['current_count'] for z in zones)
        return {'zones': zones, 'task_total': total, 'date': d}

    def reset_task_day(self, task_id, count_date: Optional[str] = None) -> int:
        """手动复位指定任务当日计数（删除当日行），返回删除行数"""
        d = count_date or self._today()
        with self._lock:
            cur = self._conn.execute(
                'DELETE FROM counting_stats WHERE task_id=? AND count_date=?',
                (task_id, d))
            self._conn.commit()
            return cur.rowcount

    def close(self):
        try:
            with self._lock:
                self._conn.close()
        except Exception:
            pass