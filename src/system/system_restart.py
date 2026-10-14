"""SageSight AI - 整系统重启工具

清理全部任务进程与后台服务后退出主进程，由 systemd（Restart=always）自动拉起
全新进程，从而一次性释放所有累积的内存并加载最新配置。

注意：此处刻意不将运行中任务的 DB 状态改为 stopped，run.py 启动时只会自动拉起
status == 'running' 的任务，从而保证整系统重启后任务自动恢复运行。
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# run.py 主进程启动时注册的进程管理器实例（TaskProcessManager）
_process_manager = None


def register_process_manager(pm):
    """注册主进程的进程管理器（run.py 启动时调用）"""
    global _process_manager
    _process_manager = pm


def perform_restart(delay_seconds: float = 0, reason: str = '手动触发'):
    """触发整系统重启（后台线程执行）

    Args:
        delay_seconds: 延迟秒数（API 场景下先让 HTTP 响应返回浏览器再执行）
        reason: 触发原因（写入日志便于排查）
    """

    def _restart_worker():
        try:
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            logger.warning(f"[Restart] 触发整系统重启（{reason}），开始清理...")

            _cleanup_before_exit()

            logger.warning("[Restart] 主进程即将退出，交由 systemd 自动拉起全新进程")
            # os._exit 强制终止，避免解释器收尾阶段 join 卡住导致的残留；注册的
            # atexit.cleanup_handler 在此不会执行，任务 DB 状态保持 running 以便重启后恢复。
            os._exit(0)
        except SystemExit:
            raise
        except Exception as e:
            logger.error(f"[Restart] 重启流程异常: {e}")
            os._exit(1)

    threading.Thread(target=_restart_worker, daemon=True).start()


def _cleanup_before_exit():
    """退出前的清理：任务进程、后台服务与残留子进程（保持 DB 任务状态不变）"""
    pm = _process_manager

    # 1. 停止任务进程（进程树），但保持 DB 任务状态不变
    if pm is not None:
        try:
            pm.running = False
            pm.stop_all_tasks()
        except Exception as e:
            logger.error(f"[Restart] 停止任务进程失败: {e}")

    # 2. 停止节点管理服务（心跳线程 + HTTP 服务）
    try:
        from src.node_agent import get_node_agent_service
        get_node_agent_service().stop()
    except Exception as e:
        logger.error(f"[Restart] 停止节点管理服务失败: {e}")

    # 3. 停止事件清理后台调度
    try:
        from src.events.event_cleanup_service import stop_event_cleanup_scheduler
        stop_event_cleanup_scheduler()
    except Exception:
        pass

    # 4. 强杀所有残留子进程（最后手段）
    try:
        import psutil
        parent = psutil.Process(os.getpid())
        children = parent.children(recursive=True)
        if children:
            logger.warning(f"[Restart] 发现 {len(children)} 个残留子进程，强制清理")
            _gone, alive = psutil.wait_procs(children, timeout=3)
            for child in alive:
                try:
                    child.kill()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"[Restart] 清理残留子进程失败: {e}")

    # 5. 停止日志监听器（避免退出后残留句柄/日志写入冲突）
    try:
        from src.utils.log_manager import LogManager
        LogManager.get_instance().stop()
    except Exception:
        pass
