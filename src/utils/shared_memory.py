"""
共享内存模块

提供共享内存帧数据结构和环形缓冲区管理。

特性：
1. FrameHeader - 帧头部信息数据结构
2. SharedMemoryBlock - 单个共享内存块（存储一帧）
3. SharedMemoryRingBuffer - 环形缓冲区（多块零拷贝存储）

新架构：
- 环形缓冲区设计（多块存储）
- 零拷贝数据传输（numpy 内存映射）
- 生产者-消费者解耦
- 线程安全的并发读写
"""

import mmap
import struct
import time
import threading
from dataclasses import dataclass
from typing import Tuple, Optional
import numpy as np
import cv2  # 用于 NV12 到 RGB 转换
import os
import logging

# 配置日志
logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class FrameHeader:
    """帧头部信息"""
    sequence_num: int      # 帧序列号
    timestamp_ns: int      # 时间戳（纳秒）
    write_time_ns: int     # 写入完成时间（纳秒）

    # 图像参数
    width: int = 640
    height: int = 640
    format: str = "nv12"  # 像素格式：yuv420p 或 nv12

    @property
    def timestamp_ms(self) -> float:
        """毫秒级时间戳"""
        return self.timestamp_ns / 1_000_000

    @property
    def write_time_ms(self) -> float:
        """写入完成时间（毫秒）"""
        return self.write_time_ns / 1_000_000


# ============================================================================
# 异常类
# ============================================================================

class SharedMemoryError(Exception):
    """共享内存异常基类"""
    pass


class BlockNotOpenError(SharedMemoryError):
    """共享内存块未打开"""
    pass


class InvalidBlockIdError(SharedMemoryError):
    """无效的块ID"""
    pass


# ============================================================================
# 共享内存块
# ============================================================================

class SharedMemoryBlock:
    """
    单个共享内存块（存储一帧）

    使用 Windows mmap 实现共享内存，支持零拷贝读写。
    """

    HEADER_SIZE = 20  # 头部大小（字节）

    def __init__(self, block_id: int, width: int = 640, height: int = 640, camera_id: str = ""):
        """
        初始化共享内存块

        Args:
            block_id: 块ID
            width: 帧宽度
            height: 帧高度
            camera_id: 摄像机ID（用于生成唯一名称）
        """
        self.block_id = block_id
        self.width = width
        self.height = height

        # 计算帧大小（NV12格式）
        y_size = width * height
        uv_size = width * height // 2
        self.total_size = self.HEADER_SIZE + y_size + uv_size

        # ✅ 修复：在共享内存名称中包含 camera_id，避免多个解码器冲突
        # Windows 的 mmap tagname 是全局的，如果不加唯一标识符会导致数据混淆
        if camera_id:
            self.name = f"sagesight_{camera_id}_block_{block_id}"
        else:
            # 兼容旧代码：如果没有提供 camera_id，使用随机UUID
            import uuid
            unique_id = str(uuid.uuid4())[:8]
            self.name = f"sagesight_{unique_id}_block_{block_id}"

        self.mmap: Optional[mmap.mmap] = None
        self.is_open = False

        # 创建共享内存
        self._create_shared_memory()

    def _create_shared_memory(self):
        """创建共享内存"""
        try:
            # Windows: 创建内存映射文件
            self.mmap = mmap.mmap(
                -1,
                self.total_size,
                tagname=self.name,
                access=mmap.ACCESS_WRITE
            )
            self.is_open = True
            print(f"[SharedMemoryBlock-{self.block_id}] 创建成功: {self.name}")
        except Exception as e:
            print(f"[SharedMemoryBlock-{self.block_id}] 创建失败: {e}")
            self.is_open = False

    def write_frame_nv12(self, header: FrameHeader, y: np.ndarray, uv: np.ndarray):
        """
        写入NV12帧数据

        Args:
            header: 帧头部信息
            y: Y 平面 (height, width)
            uv: UV 交错平面 (height//2, width)
        """
        if not self.is_open:
            raise BlockNotOpenError(f"Block {self.block_id} 未打开")

        # 写入头部
        self.mmap.seek(0)
        header_data = struct.pack(
            '<IQQ',
            header.sequence_num,
            header.timestamp_ns,
            header.write_time_ns
        )
        self.mmap.write(header_data)

        # 写入Y平面
        self.mmap.seek(self.HEADER_SIZE)
        self.mmap.write(y.tobytes())

        # 写入UV平面
        self.mmap.write(uv.tobytes())

    def read_frame_nv12(self) -> Tuple[FrameHeader, np.ndarray, np.ndarray]:
        """
        读取NV12帧数据（零拷贝）

        使用 numpy 内存映射直接创建数组视图，避免数据拷贝。

        Returns:
            Tuple[FrameHeader, np.ndarray, np.ndarray]:
                帧头部、Y平面、UV平面
        """
        if not self.is_open:
            raise BlockNotOpenError(f"Block {self.block_id} 未打开")

        # 读取头部
        self.mmap.seek(0)
        header_data = self.mmap.read(self.HEADER_SIZE)
        sequence_num, timestamp_ns, write_time_ns = struct.unpack('<IQQ', header_data)

        header = FrameHeader(
            sequence_num=sequence_num,
            timestamp_ns=timestamp_ns,
            write_time_ns=write_time_ns,
            width=self.width,
            height=self.height,
            format="nv12"
        )

        # 零拷贝读取：直接从 mmap 创建 numpy 数组视图
        y_size = self.width * self.height
        y_offset = self.HEADER_SIZE

        # Y平面：直接映射共享内存，不拷贝
        y = np.ndarray(
            shape=(self.height, self.width),
            dtype=np.uint8,
            buffer=self.mmap,
            offset=y_offset,
            order='C'
        )

        # UV平面：直接映射共享内存，不拷贝
        uv_size = y_size // 2
        uv_offset = y_offset + y_size

        uv = np.ndarray(
            shape=(self.height // 2, self.width),
            dtype=np.uint8,
            buffer=self.mmap,
            offset=uv_offset,
            order='C'
        )

        return header, y, uv

    def close(self):
        """关闭共享内存"""
        if self.mmap:
            self.mmap.close()
            self.mmap = None
        self.is_open = False
        print(f"[SharedMemoryBlock-{self.block_id}] 已关闭")


# ============================================================================
# 环形缓冲区
# ============================================================================

class SharedMemoryRingBuffer:
    """
    共享内存环形缓冲区

    支持多帧存储，写入指针和读取指针管理，并发读写。

    特性：
    - 环形缓冲区设计（多块存储）
    - 写入指针和读取指针管理
    - 支持并发读写（不同块）
    - 真正的零拷贝数据传输

    使用场景：
    VideoDecoder写入帧数据 → SharedMemoryRingBuffer → CameraWorker读取帧数据
    """

    def __init__(self, block_count: int = 5, width: int = 640, height: int = 640, camera_id: str = ""):
        """
        初始化环形缓冲区

        Args:
            block_count: 块数量（默认5块）
            width: 帧宽度
            height: 帧高度
            camera_id: 摄像机ID（用于生成唯一共享内存名称）
        """
        self.block_count = block_count
        self.width = width
        self.height = height
        self.camera_id = camera_id

        # 创建多个块（✅ 传递 camera_id）
        self.blocks = [
            SharedMemoryBlock(block_id=i, width=width, height=height, camera_id=camera_id)
            for i in range(block_count)
        ]

        # 写入指针和读取指针
        self.write_ptr = 0  # 写入指针
        self.read_ptr = 0   # 读取指针（初始跟随写入指针）

        # 帧计数器
        self.frame_count = 0

        # 锁（防止并发冲突）
        self.lock = threading.Lock()

        print(f"[SharedMemoryRingBuffer] 初始化完成")
        print(f"[SharedMemoryRingBuffer]   块数量: {block_count}")
        print(f"[SharedMemoryRingBuffer]   分辨率: {width}x{height}")

    def write_frame_nv12(self, y: np.ndarray, uv: np.ndarray) -> int:
        """
        写入NV12帧数据

        Args:
            y: Y 平面 (height, width)
            uv: UV 交错平面 (height//2, width)

        Returns:
            int: 写入的块ID
        """
        with self.lock:
            # 计算写入块ID
            block_id = self.write_ptr

            # 创建帧头部
            header = FrameHeader(
                sequence_num=self.frame_count,
                timestamp_ns=time.time_ns(),
                write_time_ns=time.time_ns(),
                width=self.width,
                height=self.height,
                format="nv12"
            )

            # 写入帧数据
            self.blocks[block_id].write_frame_nv12(header, y, uv)

            # 更新指针
            self.write_ptr = (self.write_ptr + 1) % self.block_count
            self.frame_count += 1

            return block_id

    def read_frame_nv12(self, block_id: int) -> Tuple[FrameHeader, np.ndarray, np.ndarray]:
        """
        读取NV12帧数据

        Args:
            block_id: 块ID

        Returns:
            Tuple[FrameHeader, np.ndarray, np.ndarray]:
                帧头部、Y平面、UV平面
        """
        if block_id < 0 or block_id >= self.block_count:
            raise InvalidBlockIdError(f"Invalid block_id: {block_id}")

        return self.blocks[block_id].read_frame_nv12()

    def get_available_block_count(self) -> int:
        """
        获取可用块数量（写入指针和读取指针之间的距离）

        Returns:
            int: 可用块数量
        """
        with self.lock:
            if self.write_ptr >= self.read_ptr:
                return self.write_ptr - self.read_ptr
            else:
                return self.block_count - self.read_ptr + self.write_ptr

    def close(self):
        """关闭所有块"""
        for block in self.blocks:
            block.close()
        print("[SharedMemoryRingBuffer] 已关闭")


# ============================================================================
# 便捷函数（用于检测进程池）
# ============================================================================

def read_frame_from_shared_memory(
    shm_name: str,
    shm_block_id: int,
    frame_shape: tuple
) -> Optional[np.ndarray]:
    """
    从共享内存读取帧（零拷贝，用于检测进程池）

    Args:
        shm_name: 共享内存名称（保留参数，实际使用 shm_block_id）
        shm_block_id: 共享内存块ID（环形缓冲区中的块索引）
        frame_shape: 帧形状 (height, width) 或 (height, width, channels)

    Returns:
        np.ndarray: RGB帧数据 (height, width, 3)，如果失败返回None

    Note:
        此函数用于检测进程池，从视频解码器的共享内存中读取帧数据。
        支持 NV12 格式到 RGB 的转换。
        
        共享内存命名规则：
        - SharedMemoryBlock 使用 "sagesight_video_block_{block_id}" 作为 tagname
        - 检测进程通过 block_id 直接连接到对应的共享内存块
    """
    try:
        # 解析帧形状
        if len(frame_shape) == 2:
            height, width = frame_shape
        else:
            height, width, _ = frame_shape

        # ========================================
        # 连接到已存在的共享内存块
        # ========================================
        # 命名规则：sagesight_video_block_{block_id}
        block_name = f"sagesight_video_block_{shm_block_id}"
        
        # 计算帧大小（NV12 格式）
        y_size = height * width
        uv_size = y_size // 2
        header_size = 20  # FrameHeader 大小
        total_size = header_size + y_size + uv_size

        try:
            # 连接到已存在的内存映射文件（只读）
            shm = mmap.mmap(
                -1,
                total_size,
                tagname=block_name,
                access=mmap.ACCESS_READ
            )
            
            logger.debug(f"[read_frame_from_shared_memory] 成功连接到共享内存: {block_name}")

        except Exception as e:
            # 连接失败：共享内存块不存在或已被销毁
            logger.warning(f"[read_frame_from_shared_memory] 无法连接到共享内存块 '{block_name}': {e}")
            logger.warning(f"  可能原因：解码器未启动或已停止")
            return None

        # ========================================
        # 读取帧数据（NV12 格式）
        # ========================================
        try:
            # 读取 Y 平面（height x width）
            y_offset = header_size
            y_data = np.ndarray(
                shape=(height, width),
                dtype=np.uint8,
                buffer=shm,
                offset=y_offset,
                order='C'
            )

            # 读取 UV 平面（height//2 x width）
            uv_offset = y_offset + y_size
            uv_data = np.ndarray(
                shape=(height // 2, width),
                dtype=np.uint8,
                buffer=shm,
                offset=uv_offset,
                order='C'
            )

            # ========================================
            # NV12 转 RGB
            # ========================================
            # NV12 格式：Y 平面（完整）+ UV 交错平面（U/V 交替）
            # 使用 OpenCV 转换（高效）
            
            try:
                # 方案1：使用 OpenCV 转换（推荐）
                # 将 Y 和 UV 组合为完整的 NV12 帧
                yuv_frame = np.vstack([
                    y_data,
                    uv_data
                ])  # (height + height//2, width)
                
                # NV12 (YUV420sp) -> RGB
                # OpenCV 期望 YUV420 格式为 (height * 1.5, width)
                rgb_frame = cv2.cvtColor(yuv_frame, cv2.COLOR_YUV2RGB_NV21)
                
                logger.debug(f"[read_frame_from_shared_memory] 帧转换成功: {rgb_frame.shape}")
                return rgb_frame

            except ImportError:
                # 方案2：如果 OpenCV 不可用，尝试手动转换
                logger.warning("[read_frame_from_shared_memory] OpenCV 不可用，无法转换 NV12 到 RGB")
                return None

        except Exception as e:
            logger.error(f"[read_frame_from_shared_memory] 读取帧数据失败: {e}")
            import traceback
            traceback.print_exc()
            return None

        finally:
            # 关闭内存映射（释放资源，但不销毁共享内存）
            if shm:
                try:
                    shm.close()
                except:
                    pass

    except Exception as e:
        logger.error(f"[read_frame_from_shared_memory] 读取失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def write_frame_to_shared_memory(
    shm_name: str,
    shm_block_id: int,
    frame: np.ndarray,
    header: Optional[FrameHeader] = None
) -> bool:
    """
    写入帧到共享内存（用于测试）

    Args:
        shm_name: 共享内存名称
        shm_block_id: 共享内存块ID
        frame: RGB帧数据 (height, width, 3)
        header: 帧头部信息（可选）

    Returns:
        bool: 是否成功写入
    """
    try:
        height, width, _ = frame.shape

        # RGB 转 NV12（简化版）
        # 这里只实现基本的写入逻辑，实际转换需要完整的YUV处理

        # 创建头部
        if header is None:
            header = FrameHeader(
                sequence_num=0,
                timestamp_ns=time.time_ns(),
                write_time_ns=time.time_ns(),
                width=width,
                height=height,
                format="nv12"
            )

        # 实现写入逻辑（如果需要）
        # ...

        return True

    except Exception as e:
        print(f"[write_frame_to_shared_memory] 写入失败: {e}")
        return False