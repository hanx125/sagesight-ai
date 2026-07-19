"""
日志管理器

提供统一的日志管理功能：
- 多进程日志收集（QueueHandler）
- 日志监听器（LogListener）
- 统一日志格式
- 文件日志 + 控制台日志

使用场景：
- 多进程应用日志收集
- 统一日志管理
- 日志文件轮转
"""

import logging
import logging.handlers
import multiprocessing as mp
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Optional


class LogManager:
    """
    日志管理器
    
    提供：
    1. 日志监听器（多进程日志收集）
    2. 统一日志格式
    3. 文件日志 + 控制台日志
    """
    
    _instance: Optional['LogManager'] = None
    
    def __init__(
        self,
        log_dir: str = "logs",
        log_level: int = logging.INFO,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        format_string: Optional[str] = None,
    ):
        """
        初始化日志管理器
        
        Args:
            log_dir: 日志目录
            log_level: 日志级别
            max_bytes: 单个日志文件最大字节数
            backup_count: 日志文件备份数量
            format_string: 日志格式字符串
        """
        self.log_dir = Path(log_dir)
        self.log_level = log_level
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.format_string = format_string or self._get_default_format()
        
        # 创建日志目录
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 日志队列（用于多进程日志收集）
        self.log_queue: mp.Queue = mp.Queue()
        
        # 日志监听器
        self.log_listener: Optional[QueueListener] = None
        
        # 是否已启动
        self._started = False
    
    @classmethod
    def get_instance(cls, **kwargs) -> 'LogManager':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance
    
    def _get_default_format(self) -> str:
        """获取默认日志格式"""
        return (
            '%(asctime)s - %(processName)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    def start(self):
        """启动日志监听器"""
        if self._started:
            return
        
        # 创建日志格式器
        formatter = logging.Formatter(self.format_string)
        
        # 创建文件处理器（轮转）
        file_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "sagesight.log",
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(self.log_level)
        
        # 创建控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(self.log_level)
        
        # 创建日志监听器
        self.log_listener = QueueListener(
            self.log_queue,
            file_handler,
            console_handler,
            respect_handler_level=True
        )
        
        # 启动监听器
        self.log_listener.start()
        self._started = True
        
        print(f"日志监听器已启动，日志目录: {self.log_dir}")
    
    def stop(self):
        """停止日志监听器"""
        if self.log_listener and self._started:
            self.log_listener.stop()
            self._started = False
            print("日志监听器已停止")
    
    def get_queue_handler(self, logger_name: str) -> QueueHandler:
        """
        获取队列处理器（用于多进程日志）
        
        Args:
            logger_name: 日志器名称
            
        Returns:
            QueueHandler 实例
        """
        return QueueHandler(self.log_queue)
    
    def setup_logger(self, logger_name: str) -> logging.Logger:
        """
        设置日志器（用于多进程）
        
        Args:
            logger_name: 日志器名称
            
        Returns:
            配置好的日志器
        """
        logger = logging.getLogger(logger_name)
        logger.setLevel(self.log_level)
        
        # 清除现有处理器
        logger.handlers.clear()
        
        # 添加队列处理器
        queue_handler = self.get_queue_handler(logger_name)
        logger.addHandler(queue_handler)
        
        return logger


# ========================================
# 全局日志监听器
# ========================================

class LogListener:
    """日志监听器（全局日志管理）"""
    
    def __init__(self):
        self.manager = LogManager.get_instance()
        self.queue = self.manager.log_queue
    
    def start(self):
        """启动监听器"""
        self.manager.start()
    
    def stop(self):
        """停止监听器"""
        self.manager.stop()


# 全局日志监听器实例
log_listener = LogListener()


def setup_logging(log_dir: str = "logs", log_level: int = logging.INFO):
    """
    设置全局日志（便捷函数）
    
    Args:
        log_dir: 日志目录
        log_level: 日志级别
    """
    manager = LogManager.get_instance(log_dir=log_dir, log_level=log_level)
    manager.start()


def get_logger(name: str) -> logging.Logger:
    """
    获取日志器（便捷函数）
    
    Args:
        name: 日志器名称
        
    Returns:
        配置好的日志器
    """
    manager = LogManager.get_instance()
    return manager.setup_logger(name)