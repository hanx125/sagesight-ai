"""
进程管理工具使用示例

展示如何使用进程管理工具：
1. 启动日志监听器
2. 创建进程
3. 管理进程生命周期
4. 多进程日志收集
"""

import time
from src.utils import Process, setup_logging, get_logger, log_listener


# ========================================
# 示例1: 简单的进程使用
# ========================================

def simple_worker(name: str, count: int):
    """简单的工作进程"""
    logger = get_logger(name)
    
    for i in range(count):
        logger.info(f"工作进程 {name} 正在处理第 {i+1} 个任务")
        time.sleep(1)
    
    logger.info(f"工作进程 {name} 完成")


def example_simple_process():
    """示例1: 简单进程"""
    print("\n示例1: 简单进程使用")
    
    # 启动日志监听器
    setup_logging(log_dir="logs", log_level=20)  # INFO
    
    # 创建进程
    process = Process(
        name="worker_1",
        target=simple_worker,
        args=("worker_1", 5),
        daemon=False
    )
    
    # 启动进程
    process.start()
    print(f"进程已启动: {process.name} (PID: {process.pid})")
    
    # 等待进程完成
    process.join()
    print(f"进程已完成: {process.name}")


# ========================================
# 示例2: 多个进程管理
# ========================================

def multi_worker(name: str, duration: int):
    """多个工作进程"""
    logger = get_logger(name)
    
    logger.info(f"进程 {name} 开始工作")
    
    for i in range(duration):
        logger.info(f"进程 {name}: 第 {i+1}/{duration} 秒")
        time.sleep(1)
    
    logger.info(f"进程 {name} 完成")


def example_multi_processes():
    """示例2: 多个进程管理"""
    print("\n示例2: 多个进程管理")
    
    # 启动日志监听器
    setup_logging(log_dir="logs", log_level=20)
    
    # 创建多个进程
    processes = []
    for i in range(3):
        process = Process(
            name=f"worker_{i}",
            target=multi_worker,
            args=(f"worker_{i}", 3),
            daemon=False
        )
        processes.append(process)
    
    # 启动所有进程
    for process in processes:
        process.start()
        print(f"进程已启动: {process.name} (PID: {process.pid})")
    
    # 等待所有进程完成
    for process in processes:
        process.join()
        print(f"进程已完成: {process.name}")


# ========================================
# 示例3: 自定义进程类（继承 Process）
# ========================================

class CustomProcess(Process):
    """自定义进程类"""
    
    def __init__(self, name: str, task_id: int):
        super().__init__(name=name)
        self.task_id = task_id
    
    def run(self):
        """进程主函数"""
        self.logger.info(f"自定义进程 {self.name} 开始处理任务 {self.task_id}")
        
        for i in range(5):
            if self.is_running():
                self.logger.info(f"任务 {self.task_id}: 进度 {i+1}/5")
                time.sleep(1)
            else:
                self.logger.warning("收到停止信号，退出进程")
                break
        
        self.logger.info(f"自定义进程 {self.name} 完成")


def example_custom_process():
    """示例3: 自定义进程类"""
    print("\n示例3: 自定义进程类")
    
    # 启动日志监听器
    setup_logging(log_dir="logs", log_level=20)
    
    # 创建自定义进程
    process = CustomProcess(name="custom_worker", task_id=1001)
    
    # 启动进程
    process.start()
    print(f"自定义进程已启动: {process.name} (PID: {process.pid})")
    
    # 等待进程完成
    process.join()
    print(f"自定义进程已完成: {process.name}")


# ========================================
# 示例4: 优雅停止进程
# ========================================

def long_running_worker(name: str):
    """长时间运行的工作进程"""
    logger = get_logger(name)
    
    logger.info(f"长时间进程 {name} 启动")
    
    count = 0
    while True:
        count += 1
        logger.info(f"进程 {name}: 正在运行第 {count} 秒")
        time.sleep(1)


def example_graceful_stop():
    """示例4: 优雅停止进程"""
    print("\n示例4: 优雅停止进程")
    
    # 启动日志监听器
    setup_logging(log_dir="logs", log_level=20)
    
    # 创建长时间运行的进程
    process = Process(
        name="long_running_worker",
        target=long_running_worker,
        args=("long_running_worker",),
        daemon=False
    )
    
    # 启动进程
    process.start()
    print(f"长时间进程已启动: {process.name} (PID: {process.pid})")
    
    # 运行5秒
    time.sleep(5)
    
    # 请求停止（使用 stop_event）
    print("请求停止进程...")
    process.stop()
    
    # 等待进程完成
    process.join(timeout=5)
    print(f"长时间进程已停止: {process.name}")


# ========================================
# 主函数
# ========================================

if __name__ == "__main__":
    print("=" * 70)
    print("进程管理工具使用示例")
    print("=" * 70)
    
    # 运行示例
    example_simple_process()
    # example_multi_processes()
    # example_custom_process()
    # example_graceful_stop()
    
    print("\n" + "=" * 70)
    print("示例运行完成")
    print("=" * 70)