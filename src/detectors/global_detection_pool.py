"""
全局检测进程池管理器

设计目标：
1. 根据模型类型创建检测器进程（避免重复加载）
2. 所有任务共享全局检测进程池
3. 通过任务队列分发检测任务
4. 动态管理检测器数量

架构：
    主进程
      └── GlobalDetectionPoolManager
          ├── detector_pool_yolov11n (3个检测器 - 临时测试)
          ├── detector_pool_yolov11m (1个检测器)
          └── detector_pool_yolov8n (1个检测器)

    任务1 → detection_queue → GlobalDetectionPoolManager
    任务2 → detection_queue → GlobalDetectionPoolManager
    任务3 → detection_queue → GlobalDetectionPoolManager
"""

import logging
import multiprocessing as mp
from typing import Dict, List, Any
from dataclasses import dataclass
import time

# ✅ 导入项目的自定义 Process 类（确保日志正确收集）
from src.utils.process import Process

logger = logging.getLogger(__name__)


@dataclass
class DetectorPool:
    """检测器池（按模型类型）"""
    model_type: str              # 模型类型（如 yolov11n, yolov11m）
    model_path: str              # 模型路径
    num_detectors: int           # 检测器数量
    detection_queues: List[mp.Queue]  # 检测队列列表
    detector_processes: List[mp.Process]  # 检测器进程列表
    stop_events: List[mp.Event]  # 停止事件列表
    output_events: Dict[str, mp.Event]  # 输出事件字典
    detection_results: dict      # 检测结果字典（共享）
    result_queues: Dict[str, mp.Queue] = None  # {task_id: result_queue}


class GlobalDetectionPoolManager:
    """
    全局检测进程池管理器（单例模式）

    核心功能：
    1. 根据模型类型创建检测器池（避免重复）
    2. 所有任务共享检测器池
    3. 动态管理检测器数量
    """

    _instance = None

    def __new__(cls):
        """单例模式：确保全局只有一个实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化全局检测进程池管理器"""
        if hasattr(self, '_initialized'):
            return

        self.detector_pools: Dict[str, DetectorPool] = {}  # {model_type: DetectorPool}

        # ✅ 创建全局 Manager 实例（确保整个生命周期存活）
        self.manager = mp.Manager()

        self._initialized = True

        logger.info("[GlobalDetectionPoolManager] 初始化完成")

    def register_task(
        self,
        task_id: str,
        model_type: str,
        model_path: str,
        detector_config: Dict[str, Any],
        num_detectors: int = 2,
        camera_ids: List[str] = None  # ✅ 新增：摄像机ID列表（用于创建独立队列）
    ) -> tuple:
        """
        注册任务到检测器池

        Args:
            task_id: 任务ID
            model_type: 模型类型（如 yolov11n）
            model_path: 模型路径
            detector_config: 检测器配置
            num_detectors: 检测器数量
            camera_ids: 摄像机ID列表（如果为None，则使用任务级队列）

        Returns:
            tuple: (task_queue, result_queues) 检测队列和结果队列字典
                   如果camera_ids为None，返回(task_queue, result_queue)
                   如果camera_ids有值，返回(task_queue, {camera_id: result_queue})
        """
        # 检查是否已存在该模型的检测器池
        if model_type not in self.detector_pools:
            # 创建新的检测器池
            self._create_detector_pool(model_type, model_path, detector_config, num_detectors)
            logger.info(f"[GlobalDetectionPoolManager] 创建检测器池: {model_type} ({num_detectors}个检测器)")

        # 获取检测器池
        pool = self.detector_pools[model_type]

        # 使用全局共享队列（所有任务共享）
        # 返回检测器池的第一个队列（全局共享）
        task_queue = pool.detection_queues[0]

        # ✅ 关键修改：为每个摄像机创建独立的结果队列
        if camera_ids and len(camera_ids) > 0:
            # 为每个摄像机创建独立的result_queue（避免多摄像机队列混淆）
            result_queues = {}
            for camera_id in camera_ids:
                result_queue = self.manager.Queue(maxsize=100)  # ✅ 增大队列容量从50到100，避免堆积
                pool.result_queues[camera_id] = result_queue  # 使用camera_id作为key
                pool.output_events[camera_id] = mp.Event()
                result_queues[camera_id] = result_queue
                logger.info(f"[GlobalDetectionPoolManager] 摄像机 {camera_id} 注册到检测器池 {model_type}")

            # 也注册task_id（用于向后兼容）
            pool.result_queues[f"task_{task_id}"] = result_queues  # 保存所有队列引用

            return task_queue, result_queues
        else:
            # 向后兼容：任务级队列（单摄像机或旧代码）
            result_queue = self.manager.Queue(maxsize=100)
            pool.result_queues[task_id] = result_queue
            pool.output_events[task_id] = mp.Event()

            logger.info(f"[GlobalDetectionPoolManager] 任务 {task_id} 注册到检测器池 {model_type}")

            return task_queue, result_queue

    def _create_detector_pool(
        self,
        model_type: str,
        model_path: str,
        detector_config: Dict[str, Any],
        num_detectors: int
    ):
        """
        创建检测器池

        Args:
            model_type: 模型类型
            model_path: 模型路径
            detector_config: 检测器配置
            num_detectors: 检测器数量
        """
        detection_queues = []
        detector_processes = []
        stop_events = []
        output_events = {}

        # ✅ 使用全局 manager 创建共享字典（避免被垃圾回收）
        detection_results = self.manager.dict()

        # ✅ 使用全局 manager 创建共享的 result_queues 字典
        result_queues = self.manager.dict()  # 共享字典

        # ✅ 使用全局共享队列（所有任务共享）
        # ✅ 增大容量到1000，支持更多摄像机并发检测
        # 计算：5个摄像机 * 25fps * 3倍余量 = 375，设置为1000提供充足缓冲
        # 避免 CameraWorker-48a8451f 处理慢导致队列满溢
        global_task_queue = mp.Queue(maxsize=1000)
        detection_queues.append(global_task_queue)

        # 创建检测器进程
        for i in range(num_detectors):
            stop_event = mp.Event()
            stop_events.append(stop_event)

            # ✅ 关键修复：使用标准multiprocessing.Process，避免Process类包装问题
            # Process类的__getattribute__包装可能导致target函数不被调用
            import multiprocessing as mp_std
            process = mp_std.Process(
                target=_run_detector_process,
                args=(
                    f"detector-{model_type}-{i}",
                    global_task_queue,  # 所有检测器共享同一个队列
                    detector_config,
                    output_events,
                    detection_results,
                    stop_event,
                    result_queues
                ),
                name=f"Detector-{model_type}-{i}"
            )
            process.start()
            detector_processes.append(process)

            # ✅ 进程启动日志
            logger.info(f"[GlobalDetectionPoolManager] 检测器进程已启动: PID={process.pid}, name={process.name}")

        # 创建检测器池对象
        self.detector_pools[model_type] = DetectorPool(
            model_type=model_type,
            model_path=model_path,
            num_detectors=num_detectors,
            detection_queues=detection_queues,
            detector_processes=detector_processes,
            stop_events=stop_events,
            output_events=output_events,
            detection_results=detection_results,
            result_queues=result_queues
        )

    def unregister_task(self, task_id: str, model_type: str):
        """
        注销任务

        Args:
            task_id: 任务ID
            model_type: 模型类型
        """
        if model_type in self.detector_pools:
            pool = self.detector_pools[model_type]

            # 移除输出事件
            if task_id in pool.output_events:
                del pool.output_events[task_id]

            # 移除结果队列
            if task_id in pool.result_queues:
                del pool.result_queues[task_id]

            logger.info(f"[GlobalDetectionPoolManager] 任务 {task_id} 从检测器池 {model_type} 注销")


def _run_detector_process(
    detector_id: str,
    detection_queue: mp.Queue,
    detector_config: Dict[str, Any],
    output_events: Dict[str, mp.Event],
    detection_results: dict,
    stop_event: mp.Event,
    result_queues: Dict[str, mp.Queue] = None
):
    """
    检测器进程主函数（模块级函数，避免pickle self）

    Args:
        detector_id: 检测器ID
        detection_queue: 检测队列
        detector_config: 检测器配置
        output_events: 输出事件字典
        detection_results: 检测结果字典（已废弃，保留向后兼容）
        stop_event: 停止事件
        result_queues: 结果队列字典（新增）

    Note:
        使用标准multiprocessing.Process启动，需要手动配置日志系统。
    """
    # ✅ 设置控制台输出为UTF-8编码（避免Windows GBK编码错误）
    import codecs
    import sys
    import os
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

    # ✅ 手动配置日志系统（因为使用标准Process，不再有before_run钩子）
    import logging
    from logging.handlers import QueueHandler

    # ✅ 配置根logger：添加QueueHandler，将日志发送到主进程
    try:
        from ..utils.log_manager import log_listener
        root_logger = logging.getLogger()
        root_logger.handlers.clear()  # 清除现有处理器
        root_logger.addHandler(QueueHandler(log_listener.queue))
        root_logger.setLevel(logging.INFO)
    except Exception as e:
        print(f"[{detector_id}] [ERROR] 日志系统配置失败: {e}", flush=True)

    # 创建检测器专属logger
    logger = logging.getLogger(detector_id)
    logger.setLevel(logging.INFO)
    logger.propagate = True  # 传播到根logger

    logger.info(f"[{detector_id}] 检测器进程日志配置完成")

    # 导入检测器（进程内）
    print(f"[{detector_id}] 开始导入检测器模块...", flush=True)
    from .factory import create_detector
    from .detection_queue import DetectionTask
    from ..utils.shared_memory import read_frame_from_shared_memory  # ✅ 全局导入（避免重复导入）
    print(f"[{detector_id}] 检测器模块导入完成", flush=True)

    # ✅ 进程启动日志（写入日志文件）
    print(f"[{detector_id}] ====================", flush=True)
    print(f"[{detector_id}] 检测器进程启动", flush=True)
    print(f"[{detector_id}] 进程ID: {os.getpid()}", flush=True)
    print(f"[{detector_id}] 检测器配置: model_path={detector_config.get('model_path')}, device={detector_config.get('device')}", flush=True)
    print(f"[{detector_id}] ====================", flush=True)
    logger.info("=" * 50)
    logger.info("检测器进程启动")
    logger.info(f"进程ID: {os.getpid()}")
    logger.info(f"检测器配置: model_path={detector_config.get('model_path')}, device={detector_config.get('device')}")
    logger.info("=" * 50)

    try:
        # 创建检测器实例
        logger.info("开始加载模型...")

        # ✅ 过滤detector_config，只保留DetectorConfig支持的参数
        from .base import DetectorConfig
        supported_params = {
            'model_path', 'model_type', 'backend', 'tensorrt_engine_path',
            'tensorrt_engine_cache_path', 'engine_cache_path', 'tensorrt_lib_path',
            'use_fp16', 'rknn_target', 'rknn_device_id', 'rknn_core_mask',
            'conf_threshold', 'iou_threshold', 'min_box_area',
            'device_id', 'gpu_mem_limit',
            'class_names', 'target_classes', 'labels_file',
            'input_width', 'input_height'
        }

        filtered_config = {k: v for k, v in detector_config.items() if k in supported_params}

        # ✅ 调试日志：显示过滤后的配置
        logger.info(f"过滤后的检测器配置: {filtered_config}")

        detector = create_detector(**filtered_config)
        logger.info(f"模型加载成功: {detector_config.get('model_path', '')}")
        logger.info("检测器进程初始化完成")

        # ✅ 全局创建预处理器（避免每次任务都创建）
        from .preprocessor import OptimizedPreprocessor
        preprocessor = OptimizedPreprocessor(
            width=detector_config.get('input_width', 640),
            height=detector_config.get('input_height', 640),
            model_input_size=detector_config.get('input_width', 640)
        )
        logger.info("预处理器初始化完成")

    except Exception as e:
        import traceback
        logger.error(f"检测器创建失败: {e}")
        logger.error(f"异常堆栈:\n{traceback.format_exc()}")
        return

    # ✅ 统计：处理任务数量
    task_count = 0

    # ✅ 新增：定期清理旧的检测结果（避免内存泄漏）
    CLEANUP_INTERVAL = 100  # 每100次检测清理一次

    # ✅ 调试日志：进入主循环
    logger.info("进入主循环，等待任务...")

    # 主循环：从队列获取任务并处理
    while not stop_event.is_set():
        try:
            # 超时获取任务（避免进程空转）
            task_data = detection_queue.get(timeout=1.0)
        except Exception as e:
            # ✅ 队列空或超时（正常情况，继续等待）
            if task_count == 0:  # 只在第一次输出
                logger.debug(f"队列空或超时，继续等待... (异常类型: {type(e).__name__})")
            continue

        try:
            task_count += 1

            if task_data is None:
                # 收到停止信号
                break

            # ✅ 性能分析：开始计时
            start_time = time.time()

            # 解析任务
            task = DetectionTask(**task_data) if isinstance(task_data, dict) else task_data
            parse_time = time.time()

            # ✅ 关键日志：任务开始处理（显示检测器ID，验证并行）
            logger.info(f"开始处理任务 #{task_count}: camera={task.camera_id}, frame_seq={task.frame_seq}")

            # ✅ 使用全局导入的函数（避免重复导入）
            frame = read_frame_from_shared_memory(
                shm_name=task.shm_name,
                shm_block_id=task.shm_block_id,
                frame_shape=task.frame_shape
            )
            read_time = time.time()

            if frame is None:
                logger.error(f"[{detector_id}] [ERROR] 无法从共享内存读取帧: {task.shm_name}:{task.shm_block_id}")
                continue

            # ✅ 调试日志：帧读取成功
            logger.debug(f"[{detector_id}] [OK] 共享内存读取成功: frame_shape={frame.shape}")

            # ✅ 定期清理旧的检测结果（避免内存泄漏）
            if task_count % CLEANUP_INTERVAL == 0 and detection_results:
                try:
                    # 只保留最近100个结果
                    keys_to_remove = list(detection_results.keys())[:-100]
                    for key in keys_to_remove:
                        try:
                            del detection_results[key]
                        except KeyError:
                            pass

                    if keys_to_remove:
                        logger.debug(f"[{detector_id}] 清理了 {len(keys_to_remove)} 个旧检测结果")
                except Exception as e:
                    logger.warning(f"[{detector_id}] 清理检测结果失败: {e}")

            # ✅ 使用全局预处理器（避免重复创建）
            tensor = preprocessor.preprocess_rgb(frame)
            preprocess_time = time.time()

            # ✅ 调试日志：预处理完成
            logger.debug(f"[{detector_id}] [OK] 预处理完成: tensor_shape={tensor.shape}")

            # 执行检测（返回字典格式）
            result_dict = detector.detect(tensor)
            detect_time = time.time()
            detections = result_dict.get('detections', [])  # 提取检测列表（字典列表）

            # ✅ 调试日志：检测结果（降级为DEBUG，避免刷屏）
            logger.debug(f"[{detector_id}] [OK] 检测完成: frame_seq={task.frame_seq}, detections={len(detections)}")

            # ✅ 核心优化：保存RGB帧（避免CameraWorker重复预处理）
            # 无论是否检测到目标，都返回RGB帧（因为可能有跟踪目标需要事件生成）
            rgb_frame_data = None
            if hasattr(preprocessor, '_last_rgb') and preprocessor._last_rgb is not None:
                rgb_frame_data = preprocessor._last_rgb.copy()
            else:
                logger.warning(f"[{detector_id}] [WARN] preprocessor._last_rgb不存在，使用原始frame（可能格式错误）")
                rgb_frame_data = frame.copy()
            copy_time = time.time()

            # 将结果写入结果队列（如果可用）
            try:
                # ✅ 关键修复：优先使用camera_id查找队列（新架构），其次使用task_id（向后兼容）
                result_queue = None

                # 1. 优先使用camera_id查找（新架构：每个摄像机独立队列）
                if result_queues and task.camera_id in result_queues:
                    result_queue = result_queues[task.camera_id]
                    logger.debug(f"[{detector_id}] 使用camera_id={task.camera_id}的独立队列")
                # 2. 向后兼容：使用task_id查找（旧架构：任务级队列）
                elif result_queues and task.task_id in result_queues:
                    result_queue = result_queues[task.task_id]
                    logger.debug(f"[{detector_id}] 使用task_id={task.task_id}的共享队列")
                else:
                    # 队列不存在
                    if not result_queues:
                        logger.warning(f"[{detector_id}] [WARN] result_queues为空，无法写入结果")
                    else:
                        logger.warning(f"[{detector_id}] [WARN] camera_id={task.camera_id} 和 task_id={task.task_id} 都不在result_queues中，可用keys: {list(result_queues.keys())[:5]}")

                # 写入结果队列
                if result_queue:
                    try:
                        result_data = {
                            'detections': detections,  # 字典列表
                            'timestamp_ns': task.timestamp_ns,
                            'frame_seq': task.frame_seq,
                            'camera_id': task.camera_id,
                            'task_id': task.task_id
                        }

                        # 直接返回RGB帧数据（避免共享内存复杂性）
                        if rgb_frame_data is not None:
                            result_data['rgb_frame'] = rgb_frame_data

                        result_queue.put_nowait(result_data)
                        queue_time = time.time()

                        # ✅ 性能分析日志（每10帧输出一次）
                        if task_count % 10 == 0:
                            total_time = queue_time - start_time
                            parse_cost = (parse_time - start_time) * 1000
                            read_cost = (read_time - parse_time) * 1000
                            preprocess_cost = (preprocess_time - read_time) * 1000
                            detect_cost = (detect_time - preprocess_time) * 1000
                            copy_cost = (copy_time - detect_time) * 1000
                            queue_cost = (queue_time - copy_time) * 1000

                            logger.info(f"[{detector_id}] 性能分析 #{task_count}: "
                                       f"总计={total_time*1000:.1f}ms "
                                       f"(解析={parse_cost:.1f}ms, "
                                       f"读取={read_cost:.1f}ms, "
                                       f"预处理={preprocess_cost:.1f}ms, "
                                       f"检测={detect_cost:.1f}ms, "
                                       f"复制={copy_cost:.1f}ms, "
                                       f"队列={queue_cost:.1f}ms)")

                        # ✅ 调试日志：结果写入成功（降级为DEBUG，避免刷屏）
                        logger.debug(f"[{detector_id}] [OK] 检测结果已写入队列: camera_id={task.camera_id}, detections={len(detections)}, rgb_frame={rgb_frame_data is not None}")
                    except Exception as e:
                        # 队列满或其他错误
                        logger.warning(f"[{detector_id}] [WARN] 结果队列写入失败，跳过帧 {task.frame_seq}: {e}")
            except (KeyError, Exception) as e:
                # Manager 对象访问异常（进程间通信失效）
                logger.error(f"[{detector_id}] [ERROR] 结果队列访问失败（Manager对象失效）: {e}")
                # 继续执行，不影响检测结果

            # 向后兼容：存储到共享字典
            try:
                result_key = f"{task.camera_id}_{task.frame_seq}"
                detection_results[result_key] = {
                    'detections': detections,
                    'timestamp_ns': task.timestamp_ns,
                    'task_id': task.camera_id
                }
            except (KeyError, Exception) as e:
                # Manager 对象访问异常
                logger.warning(f"[{detector_id}] 检测结果存储失败（Manager对象失效）: {e}")

            # 通知完成
            try:
                if task.camera_id in output_events:
                    output_events[task.camera_id].set()
            except (KeyError, Exception) as e:
                # Manager 对象访问异常
                logger.warning(f"[{detector_id}] 事件通知失败（Manager对象失效）: {e}")

        except Exception as e:
            import traceback
            logger.error(f"任务处理异常: {e}")
            logger.error(f"异常类型: {type(e).__name__}")
            logger.error(f"异常堆栈:\n{traceback.format_exc()}")
            # 继续处理下一个任务（不退出进程）

    # ✅ 进程退出日志
    logger.info("=" * 50)
    logger.info("检测器进程退出")
    logger.info(f"总处理任务数: {task_count}")
    logger.info("=" * 50)


def get_global_detection_pool_manager():
    """获取全局检测进程池管理器（单例）"""
    return GlobalDetectionPoolManager()