"""
SageSight AI - 报警语音播报服务（服务端播放）

根据任务检测类型触发报警时，通过服务器本地音频设备播放任务指定的 MP3 预置文件。
- MP3 预置文件存放目录：static/alarm_mp3/（由系统管理页上传管理）
- 播放参数（循环次数 / 时间间隙 / 音量）优先级：任务级配置 > 全局配置 > 默认值

播放后端（按可用性自动选择）：
1. ffplay（FFmpeg 附带）：通过 -volume 控制音量；输出设备由 config/system_config.yaml
   的 voice.audio_device / voice.audio_driver 指定（留空则用系统默认设备）
2. pygame.mixer：跨平台，支持 MP3 / 音量
3. 均不可用时仅记录告警日志，不抛出异常，不影响检测主流程
"""

import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from src.system.system_config_loader import get_voice_config

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
    "enabled": True,
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

    # 任务级开关语义：全局总开关关闭时一律不播（真总开关）；
    # 全局开启时由任务开关决定（任务开=播，任务关=不播）
    if task is not None:
        cfg["enabled"] = cfg["enabled"] and _to_bool(task.get("voice_enabled", False))
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

def _play_with_pygame(path: str, volume: float, cancel: threading.Event = None) -> bool:
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
            if cancel is not None and cancel.is_set():
                pygame.mixer.music.stop()
                return False
            time.sleep(0.05)
        return True
    except Exception as e:
        logger.warning(f"[Voice] pygame 播放失败, 尝试其他后端: {e}")
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        return False


def _terminate_proc(proc):
    """终止播放子进程并等待其退出（避免新旧两路声音短暂重叠）"""
    try:
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        proc.wait(timeout=1.0)
    except Exception:
        pass


def _run_pre_play_command():
    """播放前执行配置的 shell 命令（如打开功放开关）

    部分板卡的功放使能开关会在一次播放结束后被驱动复位，因此每轮播放前都要执行。
    """
    cmd = (get_voice_config().get('pre_play_command') or '').strip()
    if not cmd:
        return
    try:
        subprocess.run(cmd, shell=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.warning(f"[Voice] 播放前命令执行失败: {cmd}, 错误: {e}")


def _build_ffplay_env() -> dict:
    """构造 ffplay 运行环境，按配置指定音频驱动与输出设备"""
    env = os.environ.copy()
    audio_cfg = get_voice_config()
    driver = (audio_cfg.get('audio_driver') or '').strip()
    device = (audio_cfg.get('audio_device') or '').strip()
    if driver:
        env['SDL_AUDIODRIVER'] = driver
    if device:
        env['AUDIODEV'] = device
    return env


# ffplay 打开音频设备失败时的 stderr 特征（此时退出码仍可能为 0，必须按内容判定）
_FFPLAY_FAIL_MARKERS = (
    'audio open failed',
    'could not open audio device',
    'failed to open file',
)


def _extract_ffplay_error(stderr_text: str, returncode: int) -> str:
    """返回 ffplay 播放失败的说明，无失败时返回空字符串

    ffplay 打不开音频设备时仍可能以 0 退出，仅看退出码会误判为播放成功。
    """
    text = ' '.join(stderr_text.split())
    for marker in _FFPLAY_FAIL_MARKERS:
        if marker in text.lower():
            return text
    if returncode != 0:
        return text or f'exit code {returncode}'
    return ''


def _play_with_ffplay(path: str, volume: float, cancel: threading.Event = None,
                      on_spawn=None, timeout: int = 120) -> bool:
    """使用 ffplay（FFmpeg 附带）播放 MP3

    on_spawn(proc) 把子进程登记给调用方，打断时可直接终止该进程。
    stderr 写入临时文件而非丢弃：用于识别「设备打不开但退出码为 0」的假成功。
    """
    volume_pct = int(round(max(0.0, min(1.0, volume)) * 100))
    cmd = [
        "ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
        "-volume", str(volume_pct), path,
    ]
    try:
        with tempfile.TemporaryFile() as errfile:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=errfile,
                env=_build_ffplay_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if on_spawn:
                on_spawn(proc)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"[Voice] ffplay 播放超时，已终止: {path}")
                _terminate_proc(proc)
                return True
            errfile.seek(0)
            err_text = errfile.read().decode('utf-8', 'ignore')
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"[Voice] ffplay 启动失败: {e}")
        return False

    error = _extract_ffplay_error(err_text, proc.returncode)
    if error:
        device = (get_voice_config().get('audio_device') or '').strip() or '系统默认设备'
        logger.warning(
            f"[Voice] ffplay 播放失败(rc={proc.returncode}, 设备={device}): {error[:200]}"
        )
        return False
    return True


# ========================================
# 播报服务（单例）
# ========================================

class VoiceBroadcaster:
    """报警语音播报服务

    - 同一种报警正在播报时忽略重复触发（多目标同时超限会并发触发同一报警）。
    - 不同报警触发时打断上一次播报，并立即终止其音频子进程（避免声音重叠）。
    - 播放在线程中执行，不阻塞检测主流程。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()  # 当前播放任务的取消信号
        self._thread = None
        self._proc = None                 # 当前 ffplay 子进程（打断时终止）
        self._current_mp3 = None          # 当前正在播报的预置文件

    def _register_proc(self, proc):
        """登记当前播放子进程，供打断时终止"""
        with self._lock:
            self._proc = proc

    def _stop_current(self):
        """取消当前播报并终止其音频子进程（调用方需持有 self._lock）"""
        self._cancel.set()
        _terminate_proc(self._proc)
        self._proc = None

    def _load_global_config(self) -> dict:
        """从 system_settings 读取全局语音配置"""
        try:
            from src.system.system_manager import get_system_manager
            mgr = get_system_manager()
            return {
                _KEYS["enabled"]: mgr.get_setting(_KEYS["enabled"], "1"),
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

        with self._lock:
            # 同一种报警正在播报时忽略重复触发：多目标同时超限会并发触发同一报警，
            # 重复打断只会让声音反复重启，听起来像重叠
            if self._current_mp3 == cfg["mp3"] and self._thread and self._thread.is_alive():
                return

            # 不同报警：打断上一次播报并终止其音频子进程
            self._stop_current()

            cancel = threading.Event()
            self._cancel = cancel
            self._current_mp3 = cfg["mp3"]
            thread = threading.Thread(
                target=self._play_worker,
                args=(cfg["mp3"], cfg["loop_count"], cfg["interval_ms"], cfg["volume"], cancel),
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def _play_worker(self, mp3_name: str, loop_count: int, interval_ms: int, volume: float,
                     cancel: threading.Event):
        ensure_preset_dir()
        fpath = Path(PRESET_DIR) / Path(mp3_name).name
        if not fpath.is_file():
            logger.warning(f"[Voice] 预置 MP3 不存在，跳过播报: {fpath}")
            return

        path = str(fpath)
        start = time.time()
        logger.info(
            f"[Voice] 开始报警语音播报: {mp3_name}, 循环={loop_count}, 间隙={interval_ms}ms, 音量={volume}"
        )
        for i in range(loop_count):
            if cancel.is_set():
                logger.info("[Voice] 本次语音播报被新的报警打断，停止")
                return

            _run_pre_play_command()
            played = _play_with_ffplay(path, volume, cancel, on_spawn=self._register_proc)
            if not played:
                played = _play_with_pygame(path, volume, cancel)
            if not played:
                if cancel.is_set():
                    logger.info("[Voice] 本次语音播报被新的报警打断，停止")
                    return
                logger.warning("[Voice] 无可用的音频播放后端，报警语音播报已跳过")
                return

            # 最后一次播放后无需等待间隙
            if i < loop_count - 1 and interval_ms > 0 and not cancel.is_set():
                cancel.wait(interval_ms / 1000.0)

        logger.info(f"[Voice] 报警语音播报完成: {mp3_name}, 耗时 {time.time() - start:.1f}s")


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