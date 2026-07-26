"""
算法工厂

动态加载和管理检测算法，支持注册新算法。
"""

from typing import Dict, Type, List, Any


class AlgorithmFactory:
    """算法工厂 - 管理检测算法的注册和创建"""

    # 算法注册表（移除类型约束，支持任意检测器类）
    _registry: Dict[str, Type] = {}

    @classmethod
    def register(cls, algorithm_name: str, detector_class: Type):
        """
        注册算法

        Args:
            algorithm_name: 算法名称（唯一标识）
            detector_class: 检测器类（无需继承特定基类）
        """
        if algorithm_name in cls._registry:
            print(f"[AlgorithmFactory] 警告：算法 '{algorithm_name}' 已存在，将被覆盖")

        cls._registry[algorithm_name] = detector_class
        print(f"[AlgorithmFactory] 已注册算法: {algorithm_name} -> {detector_class.__name__}")

    @classmethod
    def unregister(cls, algorithm_name: str) -> bool:
        """
        注销算法

        Args:
            algorithm_name: 算法名称

        Returns:
            是否注销成功
        """
        if algorithm_name in cls._registry:
            del cls._registry[algorithm_name]
            print(f"[AlgorithmFactory] 已注销算法: {algorithm_name}")
            return True
        return False

    @classmethod
    def create(cls, algorithm_name: str, config: Dict) -> Any:
        """
        创建检测器实例

        Args:
            algorithm_name: 算法名称
            config: 检测器配置

        Returns:
            检测器实例

        Raises:
            ValueError: 如果算法未注册
        """
        if algorithm_name not in cls._registry:
            available = cls.get_available_algorithms()
            raise ValueError(f"未知算法: '{algorithm_name}'。可用算法: {available}")

        detector_class = cls._registry[algorithm_name]
        detector = detector_class(config)

        print(f"[AlgorithmFactory] 创建检测器: {algorithm_name}")
        return detector

    @classmethod
    def get_available_algorithms(cls) -> List[str]:
        """
        获取可用算法列表

        Returns:
            算法名称列表
        """
        return list(cls._registry.keys())

    @classmethod
    def is_registered(cls, algorithm_name: str) -> bool:
        """
        检查算法是否已注册

        Args:
            algorithm_name: 算法名称

        Returns:
            是否已注册
        """
        return algorithm_name in cls._registry

    @classmethod
    def get_detector_class(cls, algorithm_name: str) -> Type:
        """
        获取检测器类

        Args:
            algorithm_name: 算法名称

        Returns:
            检测器类

        Raises:
            ValueError: 如果算法未注册
        """
        if algorithm_name not in cls._registry:
            raise ValueError(f"未知算法: '{algorithm_name}'")

        return cls._registry[algorithm_name]


# ========================================
# 注册内置算法
# ========================================

# 注：检测器现在通过全局检测池（GlobalDetectionPoolManager）管理
# 不再需要通过算法工厂注册 YOLO 检测器
# 算法配置信息存储在 algorithm_configs 表中，通过 AlgorithmManager 管理