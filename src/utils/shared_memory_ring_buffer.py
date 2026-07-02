"""
共享内存环形缓冲区模块

实现多帧环形缓冲区，支持并发读写，避免新帧覆盖正在读取的帧。

特性：
- 环形缓冲区设计（多块存储）
- 写入指针和读取指针管理
- 支持并发读写（不同块）
- 真正的零拷贝数据传输

使用场景：
VideoDecoder写入帧数据 → SharedMemoryRingBuffer → CameraWorker读取帧数据
"""

import mmap
import struct
import time
from dataclasses import dataclass
from typing import Tuple, Optional
import numpy as np
import os


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


class SharedMemoryBlock:
    """单个共享内存块（存储一帧）"""
    
    HEADER_SIZE = 20  # 头部大小（字节）
    
    def __init__(self, block_id: int, width: int = 640, height: int = 640):
        """
        初始化共享内存块
        
        Args:
            block_id: 块ID
            width: 帧宽度
            height: 帧高度
        """
        self.block_id = block_id
        self.width = width
        self.height = height
        
        # 计算帧大小（NV12格式）
        y_size = width * height
        uv_size = width * height // 2
        self.total_size = self.HEADER_SIZE + y_size + uv_size
        
        # 创建共享内存
        self.name = f"yolo_detection_block_{block_id}"
        self.mmap: Optional[mmap.mmap] = None
        self.is_open = False
        
        # 创建或打开共享内存
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
            return
        
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
            raise Exception(f"Block {self.block_id} 未打开")
        
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
        # 使用 np.ndarray 创建可读写的数组视图，数据直接映射到共享内存
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
        # 注意：FFmpeg 输出的 NV12 可能有 stride 对齐，需要考虑实际的内存布局
        uv_size = y_size // 2
        uv_offset = y_offset + y_size
        
        # 为了兼容 stride 对齐，我们仍然按照 (height//2, width) 读取
        # 但如果实际数据有 stride，width 会比有效宽度大
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


class SharedMemoryRingBuffer:
    """
    共享内存环形缓冲区
    
    支持多帧存储，写入指针和读取指针管理，并发读写。
    """
    
    def __init__(self, block_count: int = 5, width: int = 640, height: int = 640):
        """
        初始化环形缓冲区
        
        Args:
            block_count: 块数量（默认5块）
            width: 帧宽度
            height: 帧高度
        """
        self.block_count = block_count
        self.width = width
        self.height = height
        
        # 创建多个块
        self.blocks = [
            SharedMemoryBlock(block_id=i, width=width, height=height)
            for i in range(block_count)
        ]
        
        # 写入指针和读取指针
        self.write_ptr = 0  # 写入指针
        self.read_ptr = 0   # 读取指针（初始跟随写入指针）
        
        # 帧计数器
        self.frame_count = 0
        
        # 锁（防止并发冲突）
        import threading
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
            raise ValueError(f"Invalid block_id: {block_id}")
        
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