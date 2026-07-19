"""
进程管理工具

提供进程管理的通用功能：
- 基础进程类（BaseProcess）
- 扩展进程类（Process）
- 信号处理
- 日志集成
- 生命周期管理

使用场景：
- 多进程任务管理
- 进程池管理
- 优雅的进程启动和停止
"""

import faulthandler
import logging
import multiprocessing as mp
import signal
import sys
import threading
from functools import wraps
from logging.handlers import QueueHandler
from typing import Any, Callable, Optional

# 导入项目内部的日志管理器
from .log_manager import log_listener


class BaseProcess(mp.Process):
    """
    基础进程类
    
    提供进程生命周期钩子：
    - before_start(): 进程启动前
    - after_start(): 进程启动后
    - before_run(): 进程运行前
    - after_run(): 进程运行后
    """
    
    def __init__(
        self,
        *,
        name: Optional[str] = None,
        target: Optional[Callable] = None,
        args: tuple = (),
        kwargs: dict = {},
        daemon: Optional[bool] = None,
    ):
        """
        初始化基础进程
        
        Args:
            name: 进程名称
            target: 目标函数
            args: 位置参数
            kwargs: 关键字参数
            daemon: 是否为守护进程
        """
        super().__init__(
            name=name, target=target, args=args, kwargs=kwargs, daemon=daemon
        )
    
    def start(self, *args, **kwargs):
        """启动进程（自动调用钩子）"""
        self.before_start()
        super().start(*args, **kwargs)
        self.after_start()
    
    def __getattribute__(self, name: str) -> Any:
        """属性访问拦截（自动包装 run 方法）"""
        if name == "run":
            run = super().__getattribute__("run")
            
            @wraps(run)
            def run_wrapper(*args, **kwargs):
                try:
                    self.before_run()
                    return run(*args, **kwargs)
                finally:
                    self.after_run()
            
            return run_wrapper
        
        return super().__getattribute__(name)
    
    def before_start(self) -> None:
        """进程启动前钩子"""
        pass
    
    def after_start(self) -> None:
        """进程启动后钩子"""
        pass
    
    def before_run(self) -> None:
        """进程运行前钩子"""
        pass
    
    def after_run(self) -> None:
        """进程运行后钩子"""
        pass


class Process(BaseProcess):
    """
    扩展进程类
    
    提供额外功能：
    - 日志集成（自动配置日志）
    - 信号处理（优雅停止）
    - 停止事件（stop_event）
    """
    
    logger: logging.Logger
    
    @property
    def stop_event(self) -> threading.Event:
        """
        停止事件（延迟初始化）
        
        Returns:
            停止事件对象
        """
        # Lazily create the stop_event. This allows the signal handler to tell if anyone is
        # monitoring the stop event, and to raise a SystemExit if not.
        if "stop_event" not in self.__dict__:
            self.__dict__["stop_event"] = threading.Event()
        return self.__dict__["stop_event"]
    
    def before_start(self) -> None:
        """进程启动前：保存日志队列"""
        self.__log_queue = log_listener.queue
    
    def before_run(self) -> None:
        """进程运行前：配置信号处理和日志"""
        # 启用故障处理（调试崩溃）
        faulthandler.enable()
        
        # 信号处理函数
        def receiveSignal(signalNumber, frame):
            # Get the stop_event through the dict to bypass lazy initialization.
            stop_event = self.__dict__.get("stop_event")
            if stop_event is not None:
                # Someone is monitoring stop_event. We should set it.
                stop_event.set()
            else:
                # Nobody is monitoring stop_event. We should raise SystemExit.
                sys.exit()
        
        # 注册信号处理器
        signal.signal(signal.SIGTERM, receiveSignal)
        signal.signal(signal.SIGINT, receiveSignal)
        
        # 配置日志
        self.logger = logging.getLogger(self.name)
        
        # 清除现有日志处理器
        logging.basicConfig(handlers=[], force=True)
        
        # 添加队列处理器（将日志发送到主进程）
        logging.getLogger().addHandler(QueueHandler(self.__log_queue))
    
    def is_running(self) -> bool:
        """
        检查进程是否正在运行
        
        Returns:
            是否正在运行
        """
        return not self.stop_event.is_set()
    
    def stop(self):
        """请求停止进程"""
        self.stop_event.set()


# ========================================
# 进程工厂函数
# ========================================

def create_process(
    target: Callable,
    name: Optional[str] = None,
    args: tuple = (),
    kwargs: dict = {},
    daemon: bool = False,
) -> Process:
    """
    创建进程（便捷函数）
    
    Args:
        target: 目标函数
        name: 进程名称
        args: 位置参数
        kwargs: 关键字参数
        daemon: 是否为守护进程
        
    Returns:
        Process 实例
    """
    return Process(
        name=name,
        target=target,
        args=args,
        kwargs=kwargs,
        daemon=daemon
    )


def run_in_process(
    target: Callable,
    name: Optional[str] = None,
    args: tuple = (),
    kwargs: dict = {},
    daemon: bool = False,
) -> Process:
    """
    在进程中运行函数（便捷函数）
    
    Args:
        target: 目标函数
        name: 进程名称
        args: 位置参数
        kwargs: 关键字参数
        daemon: 是否为守护进程
        
    Returns:
        已启动的 Process 实例
    """
    process = create_process(target, name, args, kwargs, daemon)
    process.start()
    return process