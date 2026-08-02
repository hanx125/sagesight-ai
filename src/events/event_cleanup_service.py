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
from typing import Dict, List, Optional
from urllib.parse import urlparse, unquote

import logging

logger = logging.getLogger(__name__)


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
        self.web_port = self.config.get('web_server', {}).get('port', 5555)

        # 延迟初始化 outputter（避免在未配置时报错）
        self._minio_outputter = None
        self._ftp_outputter = None

    def cleanup_events_by_days(self, days: int) -> Dict:
        """
        清理超过指定天数的事件（含存储对象）

        Args:
            days: 保留天数，清理超过此天数的事件

        Returns:
            清理统计字典：
            {
                'deleted_records': 删除的数据库记录数,
                'deleted_files': 成功删除的文件数,
                'failed_files': 删除失败的文件数,
                'total_files': 处理的文件总数
            }
        """
        logger.info(f"[EventCleanup] 开始清理超过 {days} 天的事件")

        # 1. 查询过期事件（含6个URL字段）
        expired_events = self._query_expired_events(days)
        if not expired_events:
            logger.info(f"[EventCleanup] 没有超过 {days} 天的事件需要清理")
            return {
                'deleted_records': 0,
                'deleted_files': 0,
                'failed_files': 0,
                'total_files': 0
            }

        logger.info(f"[EventCleanup] 发现 {len(expired_events)} 条过期事件，开始清理存储对象")

        # 2. 逐个事件清理存储对象
        total_deleted_files = 0
        total_failed_files = 0
        total_files = 0

        for event in expired_events:
            try:
                result = self._cleanup_event_storage(event)
                total_deleted_files += result['deleted']
                total_failed_files += result['failed']
                total_files += result['total']
            except Exception as e:
                logger.error(f"[EventCleanup] 清理事件存储失败 event_id={event.get('event_id', '')}: {e}")

        # 3. 删除数据库记录
        deleted_records = self._delete_expired_records(days)

        logger.info(
            f"[EventCleanup] 清理完成: 删除 {deleted_records} 条事件记录, "
            f"删除 {total_deleted_files} 个文件, 失败 {total_failed_files} 个, "
            f"总计处理 {total_files} 个文件"
        )

        return {
            'deleted_records': deleted_records,
            'deleted_files': total_deleted_files,
            'failed_files': total_failed_files,
            'total_files': total_files
        }

    def _query_expired_events(self, days: int) -> List[Dict]:
        """
        查询过期事件（含6个URL字段）

        Args:
            days: 保留天数

        Returns:
            过期事件列表，每个事件是包含6个URL字段的字典
        """
        events = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''SELECT event_id, local_image_url, minio_image_url, ftp_image_url,
                              local_gif_url, minio_gif_url, ftp_gif_url
                       FROM events
                       WHERE created_at < datetime("now", ?)''',
                    (f'-{days} days',)
                )
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

    def _delete_expired_records(self, days: int) -> int:
        """删除过期的数据库记录"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'DELETE FROM events WHERE created_at < datetime("now", ?)',
                    (f'-{days} days',)
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

    线程行为：
    - 启动时立即执行一次清理（清理启动前积累的过期事件）
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

    # 使用外部 stop_event 或创建模块级 stop_event
    if stop_event is None:
        _cleanup_stop_event = threading.Event()
        stop_event = _cleanup_stop_event
    else:
        _cleanup_stop_event = stop_event

    def _cleanup_loop():
        logger.info(
            f"[EventCleanup] 后台清理线程启动: "
            f"retention_days={retention_days}, interval_hours={interval_hours}"
        )

        # 启动时立即执行一次清理（清理积累的过期事件）
        try:
            get_cleanup_service().cleanup_events_by_days(retention_days)
        except Exception as e:
            logger.error(f"[EventCleanup] 启动时清理失败: {e}")

        # 定期清理循环：等待 interval 后执行，可被 stop_event 唤醒提前退出
        while not stop_event.is_set():
            # stop_event.wait 返回 True 表示被 set（应退出），False 表示超时
            if stop_event.wait(interval_hours * 3600):
                break
            try:
                get_cleanup_service().cleanup_events_by_days(retention_days)
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

