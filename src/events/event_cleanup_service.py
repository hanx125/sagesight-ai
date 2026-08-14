"""
事件清理服务

负责统一清理事件相关的数据库记录和存储对象（本地/FTP/MinIO 的图片和GIF文件）。

清理流程：
1. 查询过期事件（含6个URL字段：local/minio/ftp × image/gif）
2. 逐个事件解析URL并删除对应的存储对象
3. 删除数据库记录

URL格式：
- 本地：http://host:port/output/images/{event_id}.jpg
       http://host:port/output/gifs/{event_id[:8]}.gif
- MinIO：presigned URL（http://endpoint/{bucket}/{object_name}?X-Amz-...）
- FTP：ftp://user:pass@host:port/{remote_dir}/{filename}
"""

import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse, unquote

import logging

logger = logging.getLogger(__name__)

# 分批清理参数：每批处理的事件数 / 每批之间的休眠秒数（让出 CPU 与 SQLite 锁）
_DEFAULT_BATCH_SIZE = 200
_BATCH_SLEEP_SECONDS = 0.05


class EventCleanupService:
    """事件清理服务 - 统一清理数据库记录和存储对象"""

    def __init__(self):
        """初始化清理服务，读取存储配置和数据库路径"""
        from src.system.system_config_loader import load_system_config, get_db_path
        self.config = load_system_config()
        self.db_path = get_db_path()

        # 存储配置
        storage_cfg = self.config.get('storage', {})
        self.minio_cfg = storage_cfg.get('minio', {})
        self.ftp_cfg = storage_cfg.get('ftp', {})

        # Web服务器配置（用于解析本地URL）
        from src.system.system_config_loader import get_web_server_port
        self.web_port = get_web_server_port()

        # 延迟初始化 outputter（避免在未配置时报错）
        self._minio_outputter = None
        self._ftp_outputter = None

    def cleanup_events_by_days(self, days: int, batch_size: Optional[int] = None) -> Dict:
        """
        清理超过指定天数的事件（含存储对象）

        分批处理，避免一次性大查询/大删除长时间占用 SQLite 写锁导致系统卡住：
        1. 每批最多查询 batch_size 条过期事件（从最早的开始）
        2. 逐条清理该批事件的存储对象
        3. 按 event_id 批量删除该批数据库记录
        4. 批间休眠让出锁，给 Web 请求和事件处理留出窗口
        5. 循环直到没有更多过期事件

        Args:
            days: 保留天数，清理超过此天数的事件
            batch_size: 每批处理的事件数（None 时使用默认值）

        Returns:
            清理统计字典：
            {
                'deleted_records': 删除的数据库记录数,
                'deleted_files': 成功删除的文件数,
                'failed_files': 删除失败的文件数,
                'total_files': 处理的文件总数
            }
        """
        if batch_size is None:
            batch_size = _DEFAULT_BATCH_SIZE

        logger.info(f"[EventCleanup] 开始清理超过 {days} 天的事件 (batch_size={batch_size})")

        total_deleted_records = 0
        total_deleted_files = 0
        total_failed_files = 0
        total_files = 0

        batch_no = 0
        while True:
            batch_no += 1

            # 1. 查询一批过期事件（LIMIT batch_size）
            expired_events = self._query_expired_events(days, limit=batch_size)
            if not expired_events:
                break

            logger.info(f"[EventCleanup] 批次 {batch_no}: 处理 {len(expired_events)} 条过期事件")

            # 2. 逐条清理该批事件的存储对象
            for event in expired_events:
                try:
                    result = self._cleanup_event_storage(event)
                    total_deleted_files += result['deleted']
                    total_failed_files += result['failed']
                    total_files += result['total']
                except Exception as e:
                    logger.error(f"[EventCleanup] 清理事件存储失败 event_id={event.get('event_id', '')}: {e}")

            # 3. 按 event_id 批量删除该批数据库记录
            event_ids = [event['event_id'] for event in expired_events]
            total_deleted_records += self._delete_expired_records(event_ids)

            # 4. 批间休眠，让出 SQLite 锁给其他请求
            time.sleep(_BATCH_SLEEP_SECONDS)

        logger.info(
            f"[EventCleanup] 清理完成: 删除 {total_deleted_records} 条事件记录, "
            f"删除 {total_deleted_files} 个文件, 失败 {total_failed_files} 个, "
            f"总计处理 {total_files} 个文件"
        )

        return {
            'deleted_records': total_deleted_records,
            'deleted_files': total_deleted_files,
            'failed_files': total_failed_files,
            'total_files': total_files
        }

    def _query_expired_events(self, days: int, limit: Optional[int] = None) -> List[Dict]:
        """
        查询过期事件（含6个URL字段）

        Args:
            days: 保留天数
            limit: 每批最多返回的事件数（分批清理时使用，None 表示不限制）

        Returns:
            过期事件列表（按 created_at 升序，从最早的事件开始处理）
        """
        events = []
        try:
            sql = (
                '''SELECT event_id, local_image_url, minio_image_url, ftp_image_url,
                          local_gif_url, minio_gif_url, ftp_gif_url
                   FROM events
                   WHERE created_at < datetime("now", "localtime", ?)
                   ORDER BY created_at ASC, id ASC'''
            )
            params: List = [f'-{days} days']
            if limit is not None:
                sql += ' LIMIT ?'
                params.append(limit)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                rows = cursor.fetchall()

                for row in rows:
                    events.append({
                        'event_id': row[0],
                        'local_image_url': row[1] or '',
                        'minio_image_url': row[2] or '',
                        'ftp_image_url': row[3] or '',
                        'local_gif_url': row[4] or '',
                        'minio_gif_url': row[5] or '',
                        'ftp_gif_url': row[6] or '',
                    })
        except Exception as e:
            logger.error(f"[EventCleanup] 查询过期事件失败: {e}")

        return events

    def _delete_expired_records(self, event_ids: List[str]) -> int:
        """按 event_id 批量删除数据库记录（单批 DELETE，缩短写锁占用时间）

        Args:
            event_ids: 本批待删除的事件ID列表

        Returns:
            实际删除的记录数
        """
        if not event_ids:
            return 0
        try:
            placeholders = ','.join('?' * len(event_ids))
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f'DELETE FROM events WHERE event_id IN ({placeholders})',
                    event_ids
                )
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error(f"[EventCleanup] 删除过期记录失败: {e}")
            return 0

    def _cleanup_event_storage(self, event: Dict) -> Dict:
        """
        删除单个事件的6个存储对象

        Args:
            event: 包含6个URL字段的事件字典

        Returns:
            {'deleted': 成功数, 'failed': 失败数, 'total': 总数}
        """
        deleted = 0
        failed = 0
        total = 0

        # 6个URL字段
        url_fields = [
            ('local_image_url', 'local'),
            ('minio_image_url', 'minio'),
            ('ftp_image_url', 'ftp'),
            ('local_gif_url', 'local'),
            ('minio_gif_url', 'minio'),
            ('ftp_gif_url', 'ftp'),
        ]

        for field, storage_type in url_fields:
            url = event.get(field, '')
            if not url:
                continue

            total += 1
            try:
                success = False
                if storage_type == 'local':
                    success = self._delete_local_file(url)
                elif storage_type == 'minio':
                    success = self._delete_minio_object(url)
                elif storage_type == 'ftp':
                    success = self._delete_ftp_file(url)

                if success:
                    deleted += 1
                else:
                    failed += 1
            except Exception as e:
                logger.warning(f"[EventCleanup] 删除文件失败 url={url}: {e}")
                failed += 1

        return {'deleted': deleted, 'failed': failed, 'total': total}

    def _delete_local_file(self, url: str) -> bool:
        """
        从URL提取本地文件路径并删除

        URL格式：http://host:port/output/images/{event_id}.jpg
        提取：output/images/{event_id}.jpg
        """
        try:
            # 解析URL，提取path部分
            parsed = urlparse(url)
            file_path = unquote(parsed.path).lstrip('/')

            # 如果路径为空，返回False
            if not file_path:
                return False

            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.debug(f"[EventCleanup] 本地文件不存在，跳过: {file_path}")
                return True  # 文件不存在视为成功

            # 删除文件
            os.remove(file_path)
            logger.info(f"[EventCleanup] 已删除本地文件: {file_path}")
            return True

        except Exception as e:
            logger.warning(f"[EventCleanup] 删除本地文件失败 url={url}: {e}")
            return False

    def _delete_minio_object(self, url: str) -> bool:
        """
        从URL提取object_name并删除MinIO对象

        URL格式：presigned URL（http://endpoint/{bucket}/{object_name}?X-Amz-...）
        """
        try:
            if not self.minio_cfg.get('enabled', False):
                logger.debug("[EventCleanup] MinIO未启用，跳过")
                return True

            # 延迟初始化 MinIO outputter
            if self._minio_outputter is None:
                from .minio_outputter import MinIOOutputter
                minio_config = {
                    'enabled': True,
                    'endpoint': self.minio_cfg.get('endpoint', 'localhost:9000'),
                    'access_key': self.minio_cfg.get('access_key', 'minioadmin'),
                    'secret_key': self.minio_cfg.get('secret_key', 'minioadmin'),
                    'secure': self.minio_cfg.get('secure', False),
                    'bucket': 'sagesight-images',
                }
                self._minio_outputter = MinIOOutputter(minio_config)

            # 调用outputter的delete方法（支持完整URL或object_name）
            return self._minio_outputter.delete(url)

        except Exception as e:
            logger.warning(f"[EventCleanup] 删除MinIO对象失败 url={url}: {e}")
            return False

    def _delete_ftp_file(self, url: str) -> bool:
        """
        从URL提取filename并删除FTP文件

        URL格式：ftp://user:pass@host:port/{remote_dir}/{filename}
        """
        try:
            if not self.ftp_cfg.get('enabled', False):
                logger.debug("[EventCleanup] FTP未启用，跳过")
                return True

            # 延迟初始化 FTP outputter
            if self._ftp_outputter is None:
                from .ftp_outputter import FTPOutputter
                ftp_config = {
                    'enabled': True,
                    'host': self.ftp_cfg.get('host', 'localhost'),
                    'port': self.ftp_cfg.get('port', 21),
                    'username': self.ftp_cfg.get('username', 'ftpuser'),
                    'password': self.ftp_cfg.get('password', 'ftppassword'),
                    'passive_mode': self.ftp_cfg.get('passive_mode', True),
                    'remote_dir': '/sagesight/images',
                }
                self._ftp_outputter = FTPOutputter(ftp_config)

            # 调用outputter的delete方法（支持完整URL或filename）
            return self._ftp_outputter.delete(url)

        except Exception as e:
            logger.warning(f"[EventCleanup] 删除FTP文件失败 url={url}: {e}")
            return False


# ========================================
# 全局实例
# ========================================
_cleanup_service = None


def get_cleanup_service() -> EventCleanupService:
    """获取事件清理服务实例"""
    global _cleanup_service
    if _cleanup_service is None:
        _cleanup_service = EventCleanupService()
    return _cleanup_service


# ========================================
# 后台清理调度器
# ========================================
# 由 run.py 在应用启动时调用 start_event_cleanup_scheduler()，
# 按 system_config.yaml 的 events_cleanup 配置定期清理过期事件及其存储文件。

# 模块级停止事件（供 stop_event_cleanup_scheduler() 优雅退出）
_cleanup_stop_event: Optional[threading.Event] = None
_cleanup_thread: Optional[threading.Thread] = None


def start_event_cleanup_scheduler(stop_event: Optional[threading.Event] = None) -> Optional[threading.Thread]:
    """启动事件清理后台调度线程

    从 system_config.yaml 的 events_cleanup 段读取配置（单一真相源）：
    - enabled: false 时不启动后台线程（直接返回 None），仍可通过 API 手动清理
    - retention_days: 事件保留天数
    - cleanup_interval_hours: 清理间隔（小时）
    - startup_delay_seconds: 启动延迟秒数，避开系统启动高峰，防止抢锁卡住
    - batch_size: 每批清理的事件数，批间休眠让出 SQLite 锁

    线程行为：
    - 启动后延迟 startup_delay_seconds 秒执行首次清理（清理启动前积累的过期事件）
    - 之后每 cleanup_interval_hours 小时执行一次 cleanup_events_by_days()
    - 使用 stop_event 实现优雅退出（未提供时使用模块级 _cleanup_stop_event）

    Args:
        stop_event: 外部停止事件（可选）。应用退出时 set 该事件可让清理线程优雅退出。

    Returns:
        启动的线程对象（enabled=false 时返回 None）
    """
    global _cleanup_stop_event, _cleanup_thread

    # 延迟导入避免循环依赖
    from src.system.system_config_loader import get_events_cleanup_config

    cfg = get_events_cleanup_config()
    if not cfg['enabled']:
        logger.info(
            "[EventCleanup] 自动清理未启用（events_cleanup.enabled=false），"
            "跳过后台线程启动（仍可通过 POST /api/events/clear 手动清理）"
        )
        return None

    retention_days = cfg['retention_days']
    interval_hours = cfg['cleanup_interval_hours']
    startup_delay_seconds = cfg['startup_delay_seconds']
    batch_size = cfg['batch_size']

    # 使用外部 stop_event 或创建模块级 stop_event
    if stop_event is None:
        _cleanup_stop_event = threading.Event()
        stop_event = _cleanup_stop_event
    else:
        _cleanup_stop_event = stop_event

    def _cleanup_loop():
        logger.info(
            f"[EventCleanup] 后台清理线程启动: "
            f"retention_days={retention_days}, interval_hours={interval_hours}, "
            f"batch_size={batch_size}, startup_delay_seconds={startup_delay_seconds}"
        )

        # 启动延迟：避开系统启动高峰（Web 初始化、事件处理进程启动等），
        # 防止清理线程一启动就占用 SQLite 写锁导致系统卡住
        if startup_delay_seconds > 0:
            logger.info(f"[EventCleanup] 启动延迟 {startup_delay_seconds} 秒后执行首次清理")
            if stop_event.wait(startup_delay_seconds):
                logger.info("[EventCleanup] 启动延迟期间收到停止通知，清理线程退出")
                return

        # 首次清理（分批执行，批间让出锁）
        try:
            get_cleanup_service().cleanup_events_by_days(retention_days, batch_size=batch_size)
        except Exception as e:
            logger.error(f"[EventCleanup] 启动时清理失败: {e}")

        # 定期清理循环：等待 interval 后执行，可被 stop_event 唤醒提前退出
        while not stop_event.is_set():
            # stop_event.wait 返回 True 表示被 set（应退出），False 表示超时
            if stop_event.wait(interval_hours * 3600):
                break
            try:
                get_cleanup_service().cleanup_events_by_days(retention_days, batch_size=batch_size)
            except Exception as e:
                logger.error(f"[EventCleanup] 定期清理失败: {e}")

        logger.info("[EventCleanup] 后台清理线程已退出")

    _cleanup_thread = threading.Thread(
        target=_cleanup_loop, daemon=True, name="EventCleanup"
    )
    _cleanup_thread.start()
    logger.info("[EventCleanup] 后台清理调度线程已启动")
    return _cleanup_thread


def stop_event_cleanup_scheduler():
    """停止事件清理后台调度线程（优雅退出）"""
    global _cleanup_stop_event
    if _cleanup_stop_event is not None:
        _cleanup_stop_event.set()
        logger.info("[EventCleanup] 已通知后台清理线程退出")

