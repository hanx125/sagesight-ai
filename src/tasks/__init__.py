"""任务管理模块"""

from .task_manager import TaskManager, DetectionTask, EventRecord
from .task_executor import MultiCameraTaskExecutor, CameraWorker, TaskScheduler, get_task_scheduler

__all__ = ['TaskManager', 'DetectionTask', 'EventRecord', 'MultiCameraTaskExecutor', 'CameraWorker', 'TaskScheduler', 'get_task_scheduler']