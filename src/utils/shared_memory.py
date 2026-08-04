"""
共享内存环形缓冲区模块

实现零拷贝的帧传递,用于多进程间高效传递视频帧。

特性:
- 环形缓冲区设计(支持多帧缓冲)
- 零拷贝传递(减少内存拷贝)
- 跨进程共享(multiprocessing.shared_memory)
- I420格式支持(Y+U+V分离,优化预处理性能)
- 跨进程锁保护(Windows命名互斥锁 / Linux fcntl文件锁)

使用场景:
解码进程 -> SharedMemoryRingBuffer -> 检测进程池

"""

import logging
import os
import time as _time
import numpy as np
import struct
from typing import Tuple, Optional, Dict, Any
from multiprocessing import shared_memory
import multiprocessing as mp
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class _ShmLock:
    """跨进程共享内存锁

    Windows 用 win32event 命名互斥锁，Linux 用 fcntl.flock 文件锁。
    超时后不阻塞，直接放行（与原逻辑一致，仍有竞争风险但避免死锁）。
    """

    def __init__(self, name: str, timeout_ms: int = 1000):
        self.name = name
        self.timeout_ms = timeout_ms
        self._mutex = None
        self._lock_file = None
        self._acquired = False

    def __enter__(self):
        if os.name == 'nt':
            try:
                import win32event
                self._mutex = win32event.CreateMutex(
                    None, False, f"Global\\{self.name}_mutex")
                wait_result = win32event.WaitForSingleObject(
                    self._mutex, self.timeout_ms)
                if wait_result == win32event.WAIT_OBJECT_0:
                    self._acquired = True
            except ImportError:
                pass
        else:
            try:
                import fcntl
                safe_name = self.name.replace('/', '_').replace('\\', '_')
                lock_path = f"/tmp/sagesight_shm_lock_{safe_name}"
                self._lock_file = open(lock_path, 'w')
                deadline = _time.monotonic() + self.timeout_ms / 1000.0
                while True:
                    try:
                        fcntl.flock(
                            self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self._acquired = True
                        break
                    except (BlockingIOError, OSError):
                        if _time.monotonic() >= deadline:
                            break
                        _time.sleep(0.001)
            except Exception as e:
                logger.debug(f"[_ShmLock] fcntl 锁不可用: {e}")
        return self

    def __exit__(self, *args):
        if self._acquired:
            if os.name == 'nt' and self._mutex:
                try:
                    import win32event
                    win32event.ReleaseMutex(self._mutex)
                except Exception:
                    pass
            elif self._lock_file:
                try:
                    import fcntl
                    fcntl.flock(self._lock_file, fcntl.LOCK_UN)
                except Exception:
                    pass
        if self._mutex:
            try:
                import win32api
                win32api.CloseHandle(self._mutex)
            except Exception:
                pass
        if self._lock_file:
            try:
                self._lock_file.close()
            except Exception:
                pass


# 共享内存块头信息(参考Frigate)
# 头信息结构(48字节):
# - 魔数(4字节): 'I420'
# - 帧序号(4字节): uint32
# - 时间戳(8字节): int64(纳秒)
# - 帧宽度(4字节): uint32
# - 帧高度(4字节): uint32
# - 设备ID长度(4字节): uint32
# - 任务ID长度(4字节): uint32
# - 保留字段(4字节): uint32
# - 设备ID(16字节): char[16]
# - 任务ID(16字节): char[16]

HEADER_FORMAT = '<4sIQLLLL16s16s'  # 小端: 魔数(4) + 帧序号(4) + 时间戳(8) + 宽度(4) + 高度(4) + 设备ID长度(4) + 任务ID长度(4) + 设备ID(16) + 任务ID(16)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 使用calcsize获取实际大小（包含对齐填充）
MAGIC_NUMBER = b'I420'


@dataclass
class FrameHeader:
    """帧头部信息"""
    sequence_num: int      # 帧序列号
    timestamp_ns: int      # 采集时间戳(纳秒)
    width: int = 0         # 帧宽度
    height: int = 0        # 帧高度
    camera_id: str = ""    # 设备ID
    task_id: str = ""      # 任务ID


class SharedMemoryBlock:
    """单个共享内存块 - 存储一帧I420格式的视频帧,包括头信息和帧数据"""
    
    def __init__(self, name: str, size: int, block_id: int):
        """
        初始化共享内存块
        
        Args:
            name: 共享内存名称
            size: 块大小(字节)
            block_id: 块ID(用于日志)
        """
        self.name = name
        self.size = size
        self.block_id = block_id
        
        # 创建或连接共享内存
        try:
            self.shm = shared_memory.SharedMemory(name=name, create=True, size=size)
            logger.debug(f"[SharedMemoryBlock-{block_id}] 创建新共享内存: {name}, size={size}")
        except FileExistsError:
            # ✅ 已存在，检查大小并复用（Windows 无法立即删除被持有的共享内存）
            try:
                self.shm = shared_memory.SharedMemory(name=name, create=False)

                if self.shm.size != size:
                    # 大小不匹配，无法复用
                    self.shm.close()
                    raise RuntimeError(
                        f"共享内存 '{name}' 大小不匹配（期望 {size}，实际 {self.shm.size}）。\n"
                        f"请手动清理共享内存：重启系统或运行清理脚本。"
                    )

                logger.debug(f"[SharedMemoryBlock-{block_id}] 复用现有共享内存: {name}, size={size}")

            except FileNotFoundError:
                # 被其他进程删除了，重新创建
                self.shm = shared_memory.SharedMemory(name=name, create=True, size=size)
                logger.debug(f"[SharedMemoryBlock-{block_id}] 重建共享内存成功: {name}, size={size}")

            except Exception as e:
                logger.error(f"[SharedMemoryBlock-{block_id}] 共享内存连接失败: {e}")
                raise
        
        # 创建内存映射
        self.mmap = self.shm.buf
        
        logger.debug(f"[SharedMemoryBlock-{block_id}] 共享内存块初始化完成: size={size}")
    
    def write_frame_i420(self, frame_seq: int, timestamp_ns: int, 
                         y: np.ndarray, u: np.ndarray, v: np.ndarray,
                         camera_id: str = "", task_id: str = "") -> bool:
        """
        写入I420格式的帧(带跨进程锁保护)
        
        Args:
            frame_seq: 帧序号
            timestamp_ns: 时间戳(纳秒)
            y: Y平面数据(height, width)
            u: U平面数据(height/2, width/2)
            v: V平面数据(height/2, width/2)
            camera_id: 设备ID(可选)
            task_id: 任务ID(可选)
        
        Returns:
            bool: 是否成功写入
        """
        # 构建头信息
        height, width = y.shape
        
        # 将ID转换为固定长度的字节(16字节)
        camera_id_bytes = camera_id.encode('utf-8')[:16].ljust(16, b'\x00')
        task_id_bytes = task_id.encode('utf-8')[:16].ljust(16, b'\x00')
        
        header = struct.pack(
            HEADER_FORMAT,
            MAGIC_NUMBER,
            frame_seq,
            timestamp_ns,
            width,
            height,
            len(camera_id.encode('utf-8')),  # 设备ID长度
            len(task_id.encode('utf-8')),    # 任务ID长度
            camera_id_bytes,
            task_id_bytes
        )
        
        # ✅ 原子写入: 一次性写入整个帧(头信息 + Y + U + V),避免数据竞争
        full_data = header + y.tobytes() + u.tobytes() + v.tobytes()
        
        # 尝试使用跨进程锁保护(如果pywin32可用)
        # 跨进程锁保护写入（Windows 命名互斥锁 / Linux fcntl 文件锁）
        with _ShmLock(self.name, timeout_ms=1000):
            arr = np.ndarray((self.shm.size,), dtype=np.uint8, buffer=self.shm.buf)
            arr[:len(full_data)] = np.frombuffer(full_data, dtype=np.uint8)

        return True
    
    def close(self):
        """关闭共享内存"""
        try:
            self.shm.close()
        except:
            pass
    
    def unlink(self):
        """销毁共享内存"""
        try:
            self.shm.unlink()
        except:
            pass


class SharedMemoryRingBuffer:
    """共享内存环形缓冲区 - 管理多个共享内存块,实现帧的循环缓冲"""
    
    def __init__(self, camera_id: str, block_count: int = 8,
                 width: int = 640, height: int = 640, task_id: str = ""):
        """
        初始化环形缓冲区

        Args:
            camera_id: 摄像机ID
            block_count: 块数量(默认8)
            width: 帧宽度(默认640)
            height: 帧高度(默认640)
            task_id: 任务ID（避免同一摄像机的多个任务共享内存冲突）
        """
        self.camera_id = camera_id
        self.task_id = task_id
        self.block_count = block_count
        self.width = width
        self.height = height
        
        # 计算每个块的大小
        # NV12格式: Y平面(height * width) + UV平面(height/2 * width)
        self.frame_size = height * width + (height // 2) * width
        self.block_size = HEADER_SIZE + self.frame_size

        # 创建共享内存块（名称包含 task_id + camera_id，避免多摄像机冲突）
        self.blocks: Dict[int, SharedMemoryBlock] = {}
        for i in range(block_count):
            # ✅ 修复：使用 task_id + camera_id 组合命名，避免一个任务多个摄像机时抢同一个内存块
            # 格式：sagesight_{task_id}_{camera_id}_block_{i}
            if task_id and camera_id:
                block_name = f"sagesight_{task_id}_{camera_id}_block_{i}"
            elif task_id:
                # 向后兼容：只有 task_id 的情况（单摄像机任务）
                block_name = f"sagesight_{task_id}_block_{i}"
            else:
                # 向后兼容：只有 camera_id 的情况（旧代码）
                block_name = f"sagesight_{camera_id}_block_{i}"

            # ✅ 调试日志：显示共享内存名称
            if i == 0:  # 只打印第一个，避免日志过多
                logger.info(f"[SharedMemoryRingBuffer] 创建共享内存块:")
                logger.info(f"  camera_id={camera_id}, task_id={task_id}")
                logger.info(f"  最终名称: {block_name}")

            self.blocks[i] = SharedMemoryBlock(
                name=block_name,
                size=self.block_size,
                block_id=i
            )
        
        # 当前写入位置
        self.write_index = mp.Value('i', 0)
        self.write_lock = mp.Lock()
        
        # 帧序列号（自动递增）
        self.frame_sequence = mp.Value('i', 0)

        logger.debug(f"[SharedMemoryRingBuffer] 初始化完成: camera_id={camera_id}, blocks={block_count}")
    
    def write_frame_i420(self, frame_seq: int, timestamp_ns: int,
                        y: np.ndarray, u: np.ndarray, v: np.ndarray,
                        camera_id: str = "", task_id: str = "") -> int:
        """
        写入I420格式的帧到环形缓冲区
        
        Args:
            frame_seq: 帧序号
            timestamp_ns: 时间戳(纳秒)
            y: Y平面数据
            u: U平面数据
            v: V平面数据
            camera_id: 设备ID(可选)
            task_id: 任务ID(可选)
        
        Returns:
            int: 写入的块ID
        """
        with self.write_lock:
            block_id = self.write_index.value
            block = self.blocks[block_id]
            
            # 写入帧
            block.write_frame_i420(frame_seq, timestamp_ns, y, u, v, camera_id, task_id)
            
            # 更新写入位置(环形)
            self.write_index.value = (block_id + 1) % self.block_count
            self.frame_sequence.value += 1
            
            return block_id
    
    def read_frame_i420(self, block_id: int) -> Tuple[Optional[FrameHeader], Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
        """
        从指定块读取I420格式的帧(带跨进程锁保护)

        Args:
            block_id: 块ID

        Returns:
            Tuple[FrameHeader, np.ndarray, np.ndarray, np.ndarray]: (头部信息, Y平面, U平面, V平面), 如果失败则返回(None, None, None, None)
        """
        if block_id not in self.blocks:
            logger.error(f"[SharedMemoryRingBuffer] 无效的block_id: {block_id}")
            return None, None, None, None

        block = self.blocks[block_id]
        shm = block.shm

        y_size = self.height * self.width
        u_size = (self.height // 2) * (self.width // 2)
        v_size = u_size
        total_size = HEADER_SIZE + y_size + u_size + v_size

        # 跨进程锁保护读取（Windows 命名互斥锁 / Linux fcntl 文件锁）
        shm_name = block.name if hasattr(block, 'name') else f"sagesight_{self.camera_id}_block_{block_id}"

        try:
            with _ShmLock(shm_name, timeout_ms=100):
                # ✅ 原子读取: 一次性读取整个帧
                full_data = bytes(shm.buf[:total_size])

            # 解析头部
            header_data = struct.unpack(
                HEADER_FORMAT,
                full_data[:HEADER_SIZE]
            )

            magic = header_data[0]
            if magic != MAGIC_NUMBER:
                logger.error(f"[read_frame_i420] 魔数不匹配: {magic}")
                return None, None, None, None

            frame_seq = header_data[1]
            timestamp_ns = header_data[2]
            width = header_data[3]
            height = header_data[4]

            # 解析 camera_id 和 task_id
            camera_id_bytes = header_data[7]
            task_id_bytes = header_data[8]
            camera_id = camera_id_bytes.rstrip(b'\x00').decode('utf-8')
            task_id = task_id_bytes.rstrip(b'\x00').decode('utf-8')

            header = FrameHeader(
                sequence_num=frame_seq,
                timestamp_ns=timestamp_ns,
                width=width,
                height=height,
                camera_id=camera_id,
                task_id=task_id
            )

            # ✅ 解析帧数据(I420格式: Y + U + V)
            y_data = np.frombuffer(full_data[HEADER_SIZE:HEADER_SIZE + y_size], dtype=np.uint8).copy()
            u_data = np.frombuffer(full_data[HEADER_SIZE + y_size:HEADER_SIZE + y_size + u_size], dtype=np.uint8).copy()
            v_data = np.frombuffer(full_data[HEADER_SIZE + y_size + u_size:total_size], dtype=np.uint8).copy()

            # reshape
            y = y_data.reshape((self.height, self.width))
            u = u_data.reshape((self.height // 2, self.width // 2))
            v = v_data.reshape((self.height // 2, self.width // 2))

            return header, y, u, v

        except Exception as e:
            logger.error(f"[read_frame_i420] 读取失败: {e}", exc_info=True)
            return None, None, None, None

    def get_block_name(self, block_id: int) -> str:
        """获取块名称"""
        # ✅ 修复：使用 task_id + camera_id 组合，避免多摄像机冲突
        if self.task_id and self.camera_id:
            return f"sagesight_{self.task_id}_{self.camera_id}_block_{block_id}"
        elif self.task_id:
            return f"sagesight_{self.task_id}_block_{block_id}"
        else:
            return f"sagesight_{self.camera_id}_block_{block_id}"

    def close(self):
        """关闭所有共享内存块"""
        for block in self.blocks.values():
            block.close()

    def unlink(self):
        """销毁所有共享内存块"""
        for block in self.blocks.values():
            block.unlink()


def read_frame_from_shared_memory(
    shm_name: str,
    shm_block_id: int,
    frame_shape: Tuple[int, int],
    expected_frame_seq: Optional[int] = None,
    return_aligned: bool = False
):
    """
    从共享内存读取帧(带跨进程锁保护)

    Args:
        shm_name: 共享内存名称前缀(如 "sagesight_{camera_id}_block")
        shm_block_id: 块ID
        frame_shape: 帧形状(height, width)
        expected_frame_seq: 期望的帧序号(用于校验块是否已被覆盖)。
            环形缓冲区会被生产者循环覆盖写入，如果本块在回读时已被新帧覆盖，
            头部的 frame_seq 会与 expected_frame_seq 不匹配。
            此时降级使用块中当前最新帧（避免快照完全缺失），但通过 aligned=False 标记，
            调用方可据此记录告警或拒绝使用该帧。
        return_aligned: 是否返回对齐状态。True 返回 (frame, aligned) 元组；
            False 返回 frame（向后兼容）

    Returns:
        return_aligned=False: np.ndarray 或 None
        return_aligned=True: (np.ndarray 或 None, bool) — 第二个元素为 True 表示帧序号匹配，
            False 表示降级使用最新帧（帧与检测框可能错位）
    """
    # 构建完整的块名称
    block_name = f"{shm_name}_{shm_block_id}"

    # ✅ 计算帧大小（I420格式：Y + U + V）
    height, width = frame_shape
    y_size = height * width
    u_size = (height // 2) * (width // 2)
    v_size = u_size
    total_size = HEADER_SIZE + y_size + u_size + v_size

    # 对齐状态：True=帧序号匹配，False=降级使用最新帧
    aligned = True
    rgb_frame = None

    # 连接到共享内存
    shm = None
    try:
        shm = shared_memory.SharedMemory(name=block_name, create=False)

        try:
            with _ShmLock(block_name, timeout_ms=100):
                # ✅ 原子读取: 一次性读取头部+帧数据
                full_data = bytes(shm.buf[:total_size])

            # 解析头部，校验帧序号（防止环形缓冲区覆盖导致帧与框错位）
            header_data = struct.unpack(HEADER_FORMAT, full_data[:HEADER_SIZE])
            magic = header_data[0]
            if magic != MAGIC_NUMBER:
                logger.error(f"[read_frame_from_shared_memory] 魔数不匹配: {magic}")
                aligned = False
            else:
                actual_frame_seq = header_data[1]
                if expected_frame_seq is not None and actual_frame_seq != expected_frame_seq:
                    # 块已被新帧覆盖：检测框基于旧帧，画面已是新帧
                    # 降级策略：不丢弃，使用块中当前最新帧（框可能轻微滞后于画面，
                    # 但事件能拿到快照，避免事件快照/GIF 完全缺失）
                    # 通过 aligned=False 标记，调用方可记录告警或拒绝使用
                    aligned = False
                    logger.debug(
                        f"[read_frame_from_shared_memory] 帧序号不匹配(降级使用最新帧): "
                        f"expected={expected_frame_seq}, actual={actual_frame_seq}, "
                        f"block={block_name}"
                    )

                # ✅ 解析帧数据(I420 -> RGB)
                # I420格式：Y平面 + U平面 + V平面（分离存储）
                # 跳过头部，从 HEADER_SIZE 开始读取 YUV 数据
                yuv_data = full_data[HEADER_SIZE:]
                u_size = (height // 2) * (width // 2)
                v_size = u_size

                y_data = np.frombuffer(yuv_data[:y_size], dtype=np.uint8).copy()
                u_data = np.frombuffer(yuv_data[y_size:y_size+u_size], dtype=np.uint8).copy()
                v_data = np.frombuffer(yuv_data[y_size+u_size:y_size+u_size+v_size], dtype=np.uint8).copy()

                # reshape
                y = y_data.reshape((height, width))
                u = u_data.reshape((height // 2, width // 2))
                v = v_data.reshape((height // 2, width // 2))

                # I420 -> RGB（使用OpenCV）
                import cv2

                # FFmpeg -pix_fmt yuv420p 输出标准 I420 (Y-U-V 顺序)，
                # 直接拼接后用 COLOR_YUV2RGB_I420 转换即可，无需交换 U/V
                i420_data = np.concatenate([
                    y.flatten(),
                    u.flatten(),
                    v.flatten()
                ])

                # reshape为I420格式（height×1.5, width）
                i420_frame = i420_data.reshape((height + height // 2, width))

                # I420 -> RGB
                rgb_frame = cv2.cvtColor(i420_frame, cv2.COLOR_YUV2RGB_I420)

        except Exception as e:
            logger.error(f"[read_frame_from_shared_memory] 读取失败: {e}", exc_info=True)
            aligned = False

    except FileNotFoundError:
        logger.warning(f"[read_frame_from_shared_memory] 共享内存不存在: {block_name}")
        aligned = False
    except Exception as e:
        logger.error(f"[read_frame_from_shared_memory] 连接失败: {e}", exc_info=True)
        aligned = False
    finally:
        if shm:
            try:
                shm.close()
            except:
                pass

    # 按 return_aligned 决定返回形式
    if return_aligned:
        return rgb_frame, aligned
    return rgb_frame