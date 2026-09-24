"""
SageSight AI - 报警语音播报服务（服务端播放）

根据任务检测类型触发报警时，通过服务器本地音频设备播放任务指定的 MP3 预置文件。
- MP3 预置文件存放目录：static/alarm_mp3/（由系统管理页上传管理）
- 播放参数（循环次数 / 时间间隙 / 音量）优先级：任务级配置 > 全局配置 > 默认值

播放后端（按可用性自动选择）：
1. pygame.mixer：跨平台，支持 MP3 / 音量
2. ffplay（FFmpeg 附带）：跨平台，通过 -volume 控制音量
3. 均不可用时仅记录告警日志，不抛出异常，不影响检测主流程
"""

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ========================================
# 常量与工具
# ========================================

_PROJECT_ROOT = Path(__file__).parent.parent.parent

# MP3 预置文件目录（相对项目根目录）
PRESET_DIR_NAME = "alarm_mp3"
PRESET_DIR = _PROJECT_ROOT / "static" / PRESET_DIR_NAME

# 全局配置键（存于 system_settings 表）
_KEYS = {
    "enabled": "voice_broadcast_enabled",  # 全局总开关（1/0）
    "mp3": "voice_mp3",                    # 默认 MP3 文件名
    "loop_count": "voice_loop_count",      # 默认循环次数
    "interval_ms": "voice_interval_ms",    # 默认循环时间间隙（毫秒）
    "volume": "voice_volume",              # 默认音量（0-1）
}

# 默认值
_DEFAULTS = {
    "enabled": False,
    "mp3": "",
    "loop_count": 1,
    "interval_ms": 1000,
    "volume": 1.0,
}

# 文件类型白名单
ALLOWED_EXTENSIONS = {".mp3"}


def ensure_preset_dir() -> Path:
    """确保 MP3 预置文件目录存在，返回目录路径"""
    os.makedirs(PRESET_DIR, exist_ok=True)
    return PRESET_DIR


def get_preset_url(filename: str) -> str:
    """生成可访问的相对 URL（前端 <audio>/<a> 引用）"""
    return f"/static/{PRESET_DIR_NAME}/{filename}"


def list_preset_files() -> list:
    """列出预置目录下的 MP3 文件"""
    ensure_preset_dir()
    files = []
    for f in sorted(os.listdir(str(PRESET_DIR))):
        if Path(f).suffix.lower() in ALLOWED_EXTENSIONS:
            fpath = Path(PRESET_DIR) / f
            files.append({
                "name": f,
                "size": fpath.stat().st_size,
                "size_display": _format_bytes(fpath.stat().st_size),
                "url": get_preset_url(f),
            })
    return files


def delete_preset_file(filename: str) -> bool:
    """删除预置 MP3 文件（仅允许 .mp3，且限定目录内，防路径穿越）"""
    name = Path(filename).name
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        return False
    ensure_preset_dir()
    fpath = Path(PRESET_DIR) / name
    if not fpath.exists() or not fpath.is_file():
        return False
    fpath.unlink()
    return True


# ========================================
# 配置解析
# ========================================

def _to_bool(value) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


def resolve_voice_config(global_cfg: dict, task: dict = None) -> dict:
    """解析生效的语音播报配置

    Args:
        global_cfg: 全局配置字典（键见 _KEYS）
        task: 任务配置字典（DetectionTask.to_dict()，可选）

    Returns:
        合并后的有效配置 dict
    """
    cfg = {
        "enabled": _to_bool(global_cfg.get(_KEYS["enabled"], _DEFAULTS["enabled"])),
        "mp3": (global_cfg.get(_KEYS["mp3"]) or _DEFAULTS["mp3"]),
        "loop_count": int(global_cfg.get(_KEYS["loop_count"], _DEFAULTS["loop_count"]) or _DEFAULTS["loop_count"]),
        "interval_ms": int(global_cfg.get(_KEYS["interval_ms"], _DEFAULTS["interval_ms"]) or _DEFAULTS["interval_ms"]),
        "volume": float(global_cfg.get(_KEYS["volume"], _DEFAULTS["volume"]) or _DEFAULTS["volume"]),
    }

    # 任务级配置覆盖全局（仅当任务启用语音播报时才可能生效）
    if task:
        task_enabled = task.get("voice_enabled", False)
        # 任务显式启用开关为优先真相源；未启用则回退到全局开关
        cfg["enabled"] = _to_bool(task_enabled) or cfg["enabled"]
        if task.get("voice_mp3"):
            cfg["mp3"] = task["voice_mp3"]
        if task.get("voice_loop_count"):
            cfg["loop_count"] = int(task["voice_loop_count"])
        if task.get("voice_interval_ms"):
            cfg["interval_ms"] = int(task["voice_interval_ms"])
        if task.get("voice_volume"):
            cfg["volume"] = float(task["voice_volume"])

    cfg["enabled"] = bool(cfg["enabled"] and cfg["mp3"])
    return cfg


# ========================================
# 播放后端
# ========================================

def _play_with_pygame(path: str, volume: float) -> bool:
    """使用 pygame.mixer 播放（跨平台，支持 MP3 / 音量）

    返回是否成功播放到结束（或判断可用）。
    """
    try:
        import pygame
    except Exception:
        return False

    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.set_volume(max(0.0, min(1.0, volume)))
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
        return True
    except Exception as e:
        logger.warning(f"[Voice] pygame 播放失败, 尝试其他后端: {e}")
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        return False


def _play_with_ffplay(path: str, volume: float, timeout: int = 120) -> bool:
    """使用 ffplay（FFmpeg 附带）播放 MP3"""
    volume_pct = int(round(max(0.0, min(1.0, volume)) * 100))
    cmd = [
        "ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
        "-volume", str(volume_pct), path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0
    except FileNotFoundError:
        return False
    except subprocess.TimeoutExpired:
        logger.warning(f"[Voice] ffplay 播放超时，已终止: {path}")
        return True
    except Exception as e:
        logger.warning(f"[Voice] ffplay 播放异常: {e}")
        return False


# ========================================
# 播报服务（单例）
# ========================================

class VoiceBroadcaster:
    """报警语音播报服务

    - 任意时刻仅保留一次播放任务；新报警触发会主动停止上一次播放。
    - 播放在线程中执行，不阻塞检测主流程。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()  # 当前播放任务的取消信号
        self._thread = None

    def _load_global_config(self) -> dict:
        """从 system_settings 读取全局语音配置"""
        try:
            from src.system.system_manager import get_system_manager
            mgr = get_system_manager()
            return {
                _KEYS["enabled"]: mgr.get_setting(_KEYS["enabled"], "0"),
                _KEYS["mp3"]: mgr.get_setting(_KEYS["mp3"], ""),
                _KEYS["loop_count"]: mgr.get_setting(_KEYS["loop_count"], "1"),
                _KEYS["interval_ms"]: mgr.get_setting(_KEYS["interval_ms"], "1000"),
                _KEYS["volume"]: mgr.get_setting(_KEYS["volume"], "1.0"),
            }
        except Exception as e:
            logger.warning(f"[Voice] 读取全局语音配置失败: {e}")
            return {}

    def trigger(self, task: dict = None):
        """触发一次语音播报

        Args:
            task: DetectionTask.to_dict()（含 voice_* 字段）；None 时仅按全局配置播报
        """
        try:
            cfg = resolve_voice_config(self._load_global_config(), task)
        except Exception as e:
            logger.warning(f"[Voice] 解析语音配置失败: {e}")
            return

        if not cfg["enabled"] or not cfg["mp3"]:
            return

        # 取消上一次播放
        with self._lock:
            self._cancel.set()
            thread = threading.Thread(
                target=self._play_worker,
                args=(cfg["mp3"], cfg["loop_count"], cfg["interval_ms"], cfg["volume"]),
                daemon=True,
            )
            self._cancel = threading.Event()
            self._thread = thread
            thread.start()

    def _play_worker(self, mp3_name: str, loop_count: int, interval_ms: int, volume: float):
        ensure_preset_dir()
        fpath = Path(PRESET_DIR) / Path(mp3_name).name
        if not fpath.is_file():
            logger.warning(f"[Voice] 预置 MP3 不存在，跳过播报: {fpath}")
            return

        path = str(fpath)
        cancel = self._cancel
        logger.info(
            f"[Voice] 开始报警语音播报: {mp3_name}, 循环={loop_count}, 间隙={interval_ms}ms, 音量={volume}"
        )
        for i in range(loop_count):
            if cancel.is_set():
                logger.info("[Voice] 本次语音播报被新的报警打断，停止")
                return

            played = _play_with_ffplay(path, volume)
            if not played:
                played = _play_with_pygame(path, volume)
            if not played:
                logger.warning("[Voice] 无可用的音频播放后端，报警语音播报已跳过")
                return

            # 最后一次播放后无需等待间隙
            if i < loop_count - 1 and interval_ms > 0 and not cancel.is_set():
                cancel.wait(interval_ms / 1000.0)

        logger.info(f"[Voice] 报警语音播报完成: {mp3_name}")


_voice_broadcaster = None
_lock = threading.Lock()


def get_voice_broadcaster() -> VoiceBroadcaster:
    """获取全局语音播报服务单例"""
    global _voice_broadcaster
    if _voice_broadcaster is None:
        with _lock:
            if _voice_broadcaster is None:
                _voice_broadcaster = VoiceBroadcaster()
    return _voice_broadcaster


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / (1024 ** 2):.2f} MB"
    return f"{size / (1024 ** 3):.2f} GB"