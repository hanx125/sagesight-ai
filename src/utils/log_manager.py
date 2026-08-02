"""
日志管理器

提供统一的日志管理功能：
- 主进程日志配置（setup_main_process）
- 子进程日志配置（setup_worker_process）
- 多进程日志收集（QueueHandler → QueueListener）
- 文件日志 + 控制台日志

使用方式：
- 主进程：LogManager.get_instance().setup_main_process(log_config)
- 子进程：LogManager.get_instance().setup_worker_process()
- 所有模块：logger = logging.getLogger(__name__)
"""

import logging
import logging.handlers
import multiprocessing as mp
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Optional, Dict, Any


class LogManager:
    """
    日志管理器（单例）

    统一管理主进程和子进程的日志配置：
    1. 主进程：setup_main_process() 配置 root logger + 启动 QueueListener
    2. 子进程：setup_worker_process() 配置 root logger 的 QueueHandler
    """

    _instance: Optional['LogManager'] = None

    def __init__(self):
        # 日志队列（子进程通过 QueueHandler 发送日志到此队列）
        self.log_queue: mp.Queue = mp.Queue()
        # 日志监听器（主进程启动，从队列读取子进程日志）
        self.log_listener: Optional[QueueListener] = None
        self._started = False

    @classmethod
    def get_instance(cls) -> 'LogManager':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def setup_main_process(self, config: Optional[Dict[str, Any]] = None):
        """
        主进程日志配置

        1. 从 config 读取日志级别、格式、文件路径等
        2. 配置 root logger（RotatingFileHandler + StreamHandler）
        3. 启动 QueueListener（接收子进程日志）

        Args:
            config: 日志配置字典，来自 system_config.yaml 的 logging 部分
        """
        if self._started:
            return

        config = config or {}
        log_level = getattr(logging, str(config.get('level', 'INFO')).upper(), logging.INFO)
        log_format = config.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        log_file = config.get('file', 'logs/system.log')
        log_max_size = int(config.get('max_size', 10)) * 1024 * 1024
        log_backup_count = int(config.get('backup_count', 5))
        log_console = config.get('console', True)

        # 确保日志目录存在
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 创建日志格式器
        formatter = logging.Formatter(log_format)

        # 配置 root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.handlers.clear()

        # 文件 handler（轮转）
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=log_max_size, backupCount=log_backup_count, encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # 控制台 handler
        console_handler = None
        if log_console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(log_level)
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)

        # 启动 QueueListener（接收子进程通过队列发来的日志）
        # QueueListener 从队列读取日志记录，转发到 file_handler 和 console_handler
        handlers = [file_handler]
        if console_handler:
            handlers.append(console_handler)
        self.log_listener = QueueListener(
            self.log_queue,
            *handlers,
            respect_handler_level=True
        )
        self.log_listener.start()
        self._started = True

        root_logger.info(
            f"日志系统初始化完成: file={log_file}, level={config.get('level', 'INFO')}, "
            f"max_size={config.get('max_size', 10)}MB"
        )

    def setup_worker_process(self):
        """
        子进程日志配置

        配置 root logger 的 QueueHandler，将日志通过队列发送到主进程。
        在 Process.before_run() 中调用。
        """
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.addHandler(QueueHandler(self.log_queue))

    def stop(self):
        """停止日志监听器"""
        if self.log_listener and self._started:
            self.log_listener.stop()
            self._started = False


# 全局日志管理器实例（替代原 log_listener）
log_listener = LogManager.get_instance()
