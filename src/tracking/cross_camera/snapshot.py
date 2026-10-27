"""事件快照读取

为什么需要它：
    外观特征提取从"相机进程"搬到了"融合线程"。相机进程手里有内存图像，
    融合线程只有数据库里的一条记录，必须自己把图找回来。

取图优先级（先便宜后昂贵）：
    1. output/images/{event_id}.jpg   本地文件，~5ms，绝大多数情况走这里
    2. local_image_url 是本地路径      同上，兼容旧数据
    3. local_image_url 是 HTTP 地址     网络拉取，2s 超时

HTTP 那一步必须熔断：容器里图片可能在另一台机器上，也可能已经不可用，
如果每条事件都等 2 秒超时，融合线程会被彻底拖死。连续失败 3 次就禁用
5 分钟，期间只用本地文件。
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_IMAGE_DIR = _PROJECT_ROOT / 'output' / 'images'

_HTTP_TIMEOUT = 2.0
_HTTP_FAIL_LIMIT = 3
_HTTP_COOLDOWN = 300.0

_http_fail_count = 0
_http_disabled_until = 0.0

_EXT_CANDIDATES = ('.jpg', '.jpeg', '.png', '.bmp')


def _imread(path: str) -> Optional[np.ndarray]:
    """读图（用 imdecode 而不是 imread，避免中文/特殊字符路径读不到）"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            return None
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    except Exception as e:
        logger.debug(f"[CrossCamera] 读取快照失败 {path}: {e}")
        return None


def _normalize_url(url: str) -> str:
    """把 Windows 风格的反斜杠路径修成可用的 URL

    实测数据里出现过 http://host:5556/output/images\\xxx.jpg（反斜杠），
    浏览器能忍，requests 不能。
    """
    text = (url or '').strip()
    if not text:
        return ''
    if text.startswith(('http://', 'https://')):
        head, _, tail = text.partition('://')
        host, _, path = tail.partition('/')
        return f"{head}://{host}/" + path.replace('\\', '/')
    return text


def _fetch_http(url: str) -> Optional[np.ndarray]:
    global _http_fail_count, _http_disabled_until

    if time.time() < _http_disabled_until:
        return None
    try:
        import requests
        resp = requests.get(url, timeout=_HTTP_TIMEOUT)
        if resp.status_code != 200 or not resp.content:
            raise ValueError(f'HTTP {resp.status_code}')
        buf = np.frombuffer(resp.content, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError('解码失败')
        _http_fail_count = 0
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    except Exception as e:
        _http_fail_count += 1
        if _http_fail_count >= _HTTP_FAIL_LIMIT:
            _http_disabled_until = time.time() + _HTTP_COOLDOWN
            logger.warning(
                f"[CrossCamera] 快照 HTTP 拉取连续失败 {_http_fail_count} 次"
                f"（{e}），暂停 {int(_HTTP_COOLDOWN / 60)} 分钟，期间只用本地文件"
            )
            _http_fail_count = 0
        else:
            logger.debug(f"[CrossCamera] 快照 HTTP 拉取失败 {url}: {e}")
        return None


def load_snapshot(event_id: str, local_image_url: str = '',
                  image_dir: str = '') -> Optional[np.ndarray]:
    """取事件快照（RGB numpy 数组）；取不到返回 None"""
    if not event_id:
        return None

    base = Path(image_dir) if image_dir else _DEFAULT_IMAGE_DIR

    # 1) 按 event_id 拼本地文件名（事件图的命名规则就是 event_id）
    for ext in _EXT_CANDIDATES:
        path = base / f'{event_id}{ext}'
        if path.exists():
            img = _imread(str(path))
            if img is not None:
                return img

    url = _normalize_url(local_image_url)
    if not url:
        return None

    # 2) 本来就是本地路径
    if not url.startswith(('http://', 'https://')):
        if os.path.exists(url):
            return _imread(url)
        return None

    # 3) 网络拉取（带熔断）
    return _fetch_http(url)


def reset_http_state() -> None:
    """测试用：清空熔断状态"""
    global _http_fail_count, _http_disabled_until
    _http_fail_count = 0
    _http_disabled_until = 0.0
