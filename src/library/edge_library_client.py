"""边缘库比对客户端

内网调用边缘集成平台（sagesight-edge）已有人脸库/车牌库接口：
- 人脸：multipart 上传人脸图，边缘抽特征 + 余弦相似度比对
- 车牌：先调边缘 OCR 接口读出车牌号，再按车牌号在库内匹配

本平台只做检测，不挂人脸特征模型和车牌 OCR 模型，比对全部在边缘完成。
任务配置里填的是库名称（与边缘平台库管理界面一致），首次比对时
调用边缘库列表接口把名称解析为库 ID 并缓存。任何异常/超时都吞掉并
返回 None，绝不阻断事件上报主流程。
"""

import logging
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# 库名称解析结果的缓存时长（秒）：避免每次比对都请求库列表接口
_LIBRARY_RESOLVE_TTL = 60.0


class EdgeLibraryClient:
    """边缘库比对客户端（一个任务一份配置）"""

    def __init__(self, config: Dict):
        """
        Args:
            config: library_match 配置段，含 enabled/edge_api_url/agent_token/
                    timeout/face_library_name/plate_library_name/
                    face_classes/plate_classes
        """
        self.enabled = bool(config.get('enabled', False))
        self.base_url = (config.get('edge_api_url') or '').rstrip('/')
        self.agent_token = config.get('agent_token') or ''
        self.timeout = float(config.get('timeout', 5) or 5)
        self.face_library_name = (config.get('face_library_name') or '').strip()
        self.plate_library_name = (config.get('plate_library_name') or '').strip()
        self.face_classes = {str(c).lower() for c in (config.get('face_classes') or [])}
        self.plate_classes = {str(c).lower() for c in (config.get('plate_classes') or [])}
        # 内置中英文类别别名：标签文件无论用中文（人脸/车牌）还是英文（face/plate）
        # 都能正确路由到比对，避免类别名语言不一致导致比对被静默跳过
        self.face_classes |= {'face', '人脸'}
        self.plate_classes |= {'plate', 'license_plate', 'licence_plate', '车牌'}
        # 库名称 -> ID 的解析缓存：{id, expire_at}
        self._resolved_face_id: Optional[Dict] = None
        self._resolved_plate_id: Optional[Dict] = None

    @property
    def usable(self) -> bool:
        """是否具备发起比对的条件（开关开启且已配置边缘地址）"""
        return self.enabled and bool(self.base_url)

    def match_class(self, class_name: str) -> Optional[str]:
        """按检测类别名判断应做哪类比对：'face' / 'plate' / None"""
        name = (class_name or '').lower()
        if name in self.face_classes and self.face_library_name:
            return 'face'
        if name in self.plate_classes and self.plate_library_name:
            return 'plate'
        return None

    def resolve_face_library(self) -> Optional[int]:
        """按库名称解析人脸库 ID（带缓存），解析失败返回 None"""
        return self._resolve_library('face', self.face_library_name)

    def resolve_plate_library(self) -> Optional[int]:
        """按库名称解析车牌库 ID（带缓存），解析失败返回 None"""
        return self._resolve_library('plate', self.plate_library_name)

    def match_face(self, image_bytes: bytes) -> Optional[Dict[str, Any]]:
        """人脸库比对（图像由边缘抽特征并比对），返回边缘 data 段或 None"""
        library_id = self.resolve_face_library()
        if library_id is None:
            return None
        url = f"{self.base_url}/video/face/library/{library_id}/match"
        return self._post(url, files={'file': ('face.jpg', image_bytes, 'image/jpeg')})

    def recognize_plate(self, image_bytes: bytes) -> Optional[str]:
        """车牌 OCR：上传图片，返回识别到的车牌号（取置信度最高的一张）"""
        if not self.usable:
            return None
        url = f"{self.base_url}/video/plate/recognize/image"
        data = self._post(url, files={'file': ('plate.jpg', image_bytes, 'image/jpeg')})
        return self._pick_plate_no(data)

    def match_plate(self, plate_no: str) -> Optional[Dict[str, Any]]:
        """按车牌号在库内匹配（精确 + 前缀），返回边缘 data 段或 None"""
        library_id = self.resolve_plate_library()
        if library_id is None or not plate_no:
            return None
        url = f"{self.base_url}/video/plate/library/{library_id}/match"
        return self._post(url, json={'plate_no': plate_no})

    def _resolve_library(self, kind: str, library_name: str) -> Optional[int]:
        """按库名称在边缘库列表中查找库 ID（结果缓存一段时间）"""
        if not self.usable or not library_name:
            return None
        cache = self._resolved_face_id if kind == 'face' else self._resolved_plate_id
        if cache and cache['name'] == library_name and cache['expire_at'] > time.time():
            return cache['id']
        url = f"{self.base_url}/video/{'face' if kind == 'face' else 'plate'}/libraries"
        data = self._get(url)
        libraries = data if isinstance(data, list) else []
        library_id = None
        for lib in libraries:
            if isinstance(lib, dict) and str(lib.get('library_name', '')).strip() == library_name:
                library_id = lib.get('id')
                break
        if library_id is None:
            logger.warning(f"[EdgeLibrary] 库名称未匹配到库: kind={kind}, name={library_name}")
            # 未命中也缓存片刻，避免高频比对时反复请求列表接口
            cache = {'name': library_name, 'id': None, 'expire_at': time.time() + _LIBRARY_RESOLVE_TTL}
        else:
            cache = {'name': library_name, 'id': int(library_id), 'expire_at': time.time() + _LIBRARY_RESOLVE_TTL}
        if kind == 'face':
            self._resolved_face_id = cache
        else:
            self._resolved_plate_id = cache
        return cache['id']

    def _get(self, url: str) -> Optional[Any]:
        """统一 GET 请求：解析 {code, msg, data} 信封，异常/非 0 码返回 None"""
        headers = {'X-Agent-Token': self.agent_token} if self.agent_token else {}
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            body = resp.json()
        except Exception as e:
            logger.warning(f"[EdgeLibrary] 请求失败 {url}: {e}")
            return None
        if not isinstance(body, dict) or body.get('code', 0) != 0:
            msg = body.get('msg') if isinstance(body, dict) else body
            logger.warning(f"[EdgeLibrary] 接口返回错误 {url}: {msg}")
            return None
        return body.get('data')

    def _post(self, url: str, **kwargs) -> Optional[Any]:
        """统一 POST 请求：解析 {code, msg, data} 信封，异常/非 0 码返回 None"""
        headers = {'X-Agent-Token': self.agent_token} if self.agent_token else {}
        try:
            resp = requests.post(url, headers=headers, timeout=self.timeout, **kwargs)
            body = resp.json()
        except Exception as e:
            logger.warning(f"[EdgeLibrary] 请求失败 {url}: {e}")
            return None
        if not isinstance(body, dict) or body.get('code', 0) != 0:
            msg = body.get('msg') if isinstance(body, dict) else body
            logger.warning(f"[EdgeLibrary] 接口返回错误 {url}: {msg}")
            return None
        return body.get('data')

    @staticmethod
    def _pick_plate_no(data: Any) -> Optional[str]:
        """从 OCR 结果中取出车牌号（结构为 dict 列表，取置信度最高者）"""
        if not isinstance(data, list) or not data:
            return None
        plates: List[Dict[str, Any]] = [d for d in data if isinstance(d, dict) and d.get('plate_no')]
        if not plates:
            return None
        plates.sort(key=lambda d: d.get('detect_conf', 0.0), reverse=True)
        return str(plates[0]['plate_no'])
