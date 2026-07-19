"""
YOLO 检测器统一入口

提供统一的 YOLO 检测器类，内部根据配置选择合适的后端。
"""

from .factory import create_detector, create_detector_from_config, DetectorConfig
from .base import BaseDetector, Detection


class YOLODetector:
    """
    YOLO 检测器统一类
    
    这是一个适配器类，内部使用 factory 模式创建实际的检测器。
    用于算法工厂注册。
    """
    
    def __init__(self, config):
        """
        初始化 YOLO 检测器
        
        Args:
            config: 检测器配置（DetectorConfig 或字典）
        """
        # 使用工厂创建检测器
        self._detector = create_detector_from_config(config)
        
    def __getattr__(self, name):
        """代理所有其他属性到内部检测器"""
        return getattr(self._detector, name)
    
    def initialize(self):
        """初始化检测器"""
        return self._detector.initialize()
    
    def detect(self, frame):
        """执行检测"""
        return self._detector.detect(frame)
    
    def infer(self, input_tensor):
        """执行推理"""
        return self._detector.infer(input_tensor)
    
    def load_model(self):
        """加载模型"""
        return self._detector.load_model()
    
    def unload_model(self):
        """卸载模型"""
        return self._detector.unload_model()
    
    def get_inference_stats(self):
        """获取推理统计"""
        return self._detector.get_inference_stats()


# 导出类
__all__ = ['YOLODetector']