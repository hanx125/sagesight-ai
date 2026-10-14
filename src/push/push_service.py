"""
UniPush 推送服务

对接个推 REST API v2（UniPush 2.0 底层通道），在检测事件产生时向已绑定
的移动端设备下发通知。

配置存储于 system_settings 表：
    push_enabled            是否启用推送（1/0）
    getui_app_id            个推应用 AppId
    getui_app_key           个推应用 AppKey
    getui_app_master_secret 个推应用 MasterSecret
    push_min_confidence     推送置信度阈值（0~1，默认 0）
    push_class_filter       推送类别过滤（逗号分隔，空表示不过滤）

设备绑定关系存储于 data/tasks.db 的 push_bindings 表。
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

GETUI_API_BASE = 'https://restapi.getui.com/v2'
AUTH_REFRESH_MARGIN = 300  # Token 过期前 5 分钟刷新


class PushService:
    """个推推送服务（单例）"""

    def __init__(self, db_path: str = "data/tasks.db"):
        self.db_path = db_path
        self._auth_token: Optional[str] = None
        self._auth_expire_at: float = 0
        self._auth_lock = threading.Lock()
        self._ensure_table()

    def _ensure_table(self):
        """确保设备绑定表存在"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS push_bindings (
                    user_id INTEGER NOT NULL,
                    client_id TEXT PRIMARY KEY,
                    device_type TEXT DEFAULT 'android',
                    created_at REAL NOT NULL
                )
            ''')

    # ========================================
    # 配置读取
    # ========================================

    def _get_setting(self, key: str, default: str = '') -> str:
        from src.system.system_manager import get_system_manager
        return get_system_manager().get_setting(key, default)

    def _bool_setting(self, key: str, default: str = '0') -> bool:
        return self._get_setting(key, default) in ('1', 'true', 'True')

    def is_enabled(self) -> bool:
        return self._bool_setting('push_enabled')

    def _get_credentials(self) -> Optional[Dict[str, str]]:
        app_id = self._get_setting('getui_app_id', '').strip()
        app_key = self._get_setting('getui_app_key', '').strip()
        master_secret = self._get_setting('getui_app_master_secret', '').strip()
        if app_id and app_key and master_secret:
            return {'app_id': app_id, 'app_key': app_key, 'master_secret': master_secret}
        return None

    # ========================================
    # 个推 REST API v2
    # ========================================

    def _get_auth_token(self, credentials: Dict[str, str]) -> Optional[str]:
        """获取个推 API 鉴权 Token（带缓存）"""
        with self._auth_lock:
            if self._auth_token and time.time() < self._auth_expire_at:
                return self._auth_token

            app_id = credentials['app_id']
            timestamp = str(int(time.time() * 1000))
            sign = hashlib.sha256(
                f"{credentials['app_key']}{timestamp}{credentials['master_secret']}".encode()
            ).hexdigest()

            try:
                resp = requests.post(
                    f"{GETUI_API_BASE}/{app_id}/auth",
                    json={'sign': sign, 'timestamp': timestamp, 'appkey': credentials['app_key']},
                    timeout=10
                )
                data = resp.json()
                if data.get('code') in (10000, 0):
                    self._auth_token = data['data']['token']
                    # expire 为剩余毫秒
                    ttl_ms = int(data['data'].get('expire', 8 * 3600 * 1000))
                    self._auth_expire_at = time.time() + max(ttl_ms / 1000 - AUTH_REFRESH_MARGIN, 60)
                    return self._auth_token
                logger.error(f"[PushService] 个推鉴权失败: code={data.get('code')}, msg={data.get('msg')}")
            except Exception as e:
                logger.error(f"[PushService] 个推鉴权请求失败: {e}")
            return None

    def push_to_cid(self, client_id: str, title: str, body: str, payload: str) -> bool:
        """向单个设备推送通知"""
        credentials = self._get_credentials()
        if not credentials:
            return False

        token = self._get_auth_token(credentials)
        if not token:
            return False

        message = {
            'request_id': uuid.uuid4().hex,
            'settings': {'ttl': 3600000},
            'audience': {'cid': [client_id]},
            'push_message': {
                # Android 通知栏消息
                'notification': {
                    'title': title,
                    'body': body,
                    'click_type': {'type': 'startapp'}
                },
                # iOS 透传消息（由 UniPush 转换为 APNs 通知）
                'transmission': json.dumps({'title': title, 'body': body, 'payload': payload}, ensure_ascii=False)
            }
        }

        try:
            resp = requests.post(
                f"{GETUI_API_BASE}/{credentials['app_id']}/push/single/cid",
                json=message,
                headers={'token': token},
                timeout=10
            )
            data = resp.json()
            if data.get('code') in (10000, 0):
                return True
            logger.warning(f"[PushService] 推送失败: cid={client_id}, code={data.get('code')}, msg={data.get('msg')}")
            # Token 失效时清空缓存，下次重新鉴权
            if data.get('code') in (10001, 10002):
                self._auth_token = None
            return False
        except Exception as e:
            logger.error(f"[PushService] 推送请求异常: {e}")
            return False

    # ========================================
    # 设备绑定
    # ========================================

    def bind_client(self, user_id: int, client_id: str, device_type: str = 'android') -> bool:
        """绑定用户与设备推送 ClientId"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    'INSERT INTO push_bindings (user_id, client_id, device_type, created_at) '
                    'VALUES (?, ?, ?, ?) '
                    'ON CONFLICT(client_id) DO UPDATE SET user_id = excluded.user_id, '
                    'device_type = excluded.device_type',
                    (user_id, client_id, device_type, time.time())
                )
            return True
        except Exception as e:
            logger.error(f"[PushService] 绑定设备失败: {e}")
            return False

    def unbind_client(self, client_id: str) -> bool:
        """解绑设备"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM push_bindings WHERE client_id = ?', (client_id,))
            return True
        except Exception as e:
            logger.error(f"[PushService] 解绑设备失败: {e}")
            return False

    def is_client_bound(self, client_id: str) -> bool:
        """查询设备是否已绑定"""
        if not client_id:
            return False
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    'SELECT 1 FROM push_bindings WHERE client_id = ?', (client_id,)
                ).fetchone()
            return row is not None
        except Exception as e:
            logger.error(f"[PushService] 查询绑定状态失败: {e}")
            return False

    def get_all_clients(self) -> List[str]:
        """获取全部已绑定设备的 ClientId"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute('SELECT client_id FROM push_bindings').fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"[PushService] 查询绑定设备失败: {e}")
            return []

    # ========================================
    # 事件推送
    # ========================================

    def push_event(self, event: Dict):
        """推送检测事件（后台线程执行，不阻塞调用方）"""
        thread = threading.Thread(target=self._push_event_sync, args=(event,), daemon=True)
        thread.start()

    def _push_event_sync(self, event: Dict):
        try:
            if not self.is_enabled():
                return
            credentials = self._get_credentials()
            if not credentials:
                return

            class_name = event.get('class_name', '') or '目标'
            confidence = float(event.get('confidence') or 0)

            # 置信度阈值过滤
            try:
                min_conf = float(self._get_setting('push_min_confidence', '0') or 0)
            except ValueError:
                min_conf = 0
            if confidence < min_conf:
                return

            # 类别过滤（逗号分隔白名单，空表示不过滤）
            class_filter = self._get_setting('push_class_filter', '').strip()
            if class_filter:
                allow_list = [c.strip() for c in class_filter.split(',') if c.strip()]
                if class_name not in allow_list:
                    return

            title = f"AI 检测报警: {class_name}"
            body = (f"置信度 {confidence:.0%}，"
                    f"时间 {event.get('created_at') or ''}")
            payload = json.dumps({
                'event_id': event.get('event_id', ''),
                'task_id': event.get('task_id'),
                'camera_id': event.get('camera_id', '')
            }, ensure_ascii=False)

            client_ids = self.get_all_clients()
            if not client_ids:
                return

            success = sum(1 for cid in client_ids if self.push_to_cid(cid, title, body, payload))
            logger.info(f"[PushService] 事件推送完成: 成功 {success}/{len(client_ids)}")
        except Exception as e:
            logger.error(f"[PushService] 事件推送异常: {e}")


_push_service: Optional[PushService] = None


def get_push_service() -> PushService:
    """获取 PushService 单例"""
    global _push_service
    if _push_service is None:
        _push_service = PushService()
    return _push_service
