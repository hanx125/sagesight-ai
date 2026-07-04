"""
RKNN Executor - RK3588/RKNN推理引擎包装类

用于在瑞芯微RK3588等平台上加载和运行RKNN模型
"""

# RKNN API 导入（仅在RK3588平台上可用）
RKNN_AVAILABLE = False
try:
    from rknn.api import RKNN
    RKNN_AVAILABLE = True
    print("[RKNNExecutor] RKNN API 已加载")
except ImportError:
    print("[RKNNExecutor] RKNN API 未安装（仅RK3588平台可用）")


class RKNNExecutor:
    """
    RKNN模型推理引擎
    
    支持平台：
    - RK3588 (RKNPU2)
    - RK3566/RK3568 (RKNPU1)
    - RV1103/RV1106
    
    使用方式：
    ```python
    executor = RKNNExecutor('models/yolov11n.rknn', target='rk3588')
    outputs = executor.run(input_image)
    executor.release()
    ```
    """
    
    def __init__(self, model_path: str, target: str = None, device_id: str = None):
        """
        初始化RKNN推理引擎
        
        Args:
            model_path: RKNN模型文件路径（.rknn格式）
            target: 目标平台（'rk3588', 'rk3566', 'rk3568', 'rv1103', 'rv1106'）
            device_id: 设备ID（多设备时使用）
        """
        if not RKNN_AVAILABLE:
            raise ImportError("RKNN API 未安装，请在RK3588平台上安装 rknn-toolkit2")
        
        self.rknn = RKNN()
        self.model_path = model_path
        self.target = target
        self.device_id = device_id
        
        # 加载RKNN模型
        print(f"[RKNNExecutor] 加载模型: {model_path}")
        ret = self.rknn.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f"加载RKNN模型失败: {model_path}, 错误码: {ret}")
        
        # 初始化runtime环境
        print('[RKNNExecutor] 初始化runtime环境')
        if target is None:
            ret = self.rknn.init_runtime()
        else:
            ret = self.rknn.init_runtime(target=target, device_id=device_id)
        
        if ret != 0:
            raise RuntimeError(f"初始化runtime环境失败, target={target}, 错误码: {ret}")
        
        print('[RKNNExecutor] Runtime环境初始化完成')
    
    def run(self, inputs):
        """
        执行推理
        
        Args:
            inputs: 输入数据（numpy数组或列表）
        
        Returns:
            list: 输出结果列表
        """
        if self.rknn is None:
            raise RuntimeError("RKNN引擎已释放，无法推理")
        
        # 统一输入格式
        if not isinstance(inputs, (list, tuple)):
            inputs = [inputs]
        
        # 执行推理
        result = self.rknn.inference(inputs=inputs)
        
        return result
    
    def release(self):
        """
        释放RKNN引擎资源
        """
        if self.rknn is not None:
            self.rknn.release()
            self.rknn = None
            print('[RKNNExecutor] RKNN引擎已释放')
    
    def __del__(self):
        """
        析构函数，自动释放资源
        """
        self.release()


def is_rknn_available() -> bool:
    """
    检查RKNN API是否可用
    
    Returns:
        bool: True表示RKNN API已安装
    """
    return RKNN_AVAILABLE


def get_rknn_platform_info() -> dict:
    """
    获取RKNN平台信息
    
    Returns:
        dict: 平台信息字典
    """
    if not RKNN_AVAILABLE:
        return {
            'available': False,
            'message': 'RKNN API 未安装'
        }
    
    return {
        'available': True,
        'supported_targets': ['rk3588', 'rk3566', 'rk3568', 'rv1103', 'rv1106'],
        'message': 'RKNN API 已加载'
    }