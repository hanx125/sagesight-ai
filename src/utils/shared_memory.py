"""
共享内存管理器模块

实现基于 mmap 的共享内存，用于解码器和检测器之间的高效数据传输（零拷贝）。
支持版本检查机制，避免读到半帧数据。

共享内存布局（614,420 字节）：
┌───────────────────────────────────────────────────────────┐
│  偏移量   大小     内容            说明                    │
├───────────────────────────────────────────────────────────┤
│  0        4 字节    sequence_num   帧序列号 (从 0 开始递增)   │
│  4        8 字节    timestamp_ns   时间戳 (纳秒)            │
│  12       8 字节    write_time_ns  写入完成时间 (纳秒)      │
│  20       614,400 字节 YUV 数据     YUV420p (960×640)       │
│  总计：614,420 字节                                       │
└───────────────────────────────────────────────────────────┘

注意：Windows 平台使用内存映射文件实现共享内存
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
    format: str = "yuv420p"  # 像素格式：yuv420p 或 nv12
    
    @property
    def timestamp_ms(self) -> float:
        """毫秒级时间戳"""
        return self.timestamp_ns / 1_000_000
    
    @property
    def write_time_ms(self) -> float:
        """写入完成时间（毫秒）"""
        return self.write_time_ns / 1_000_000


class SharedMemoryError(Exception):
    """共享内存异常基类"""
    pass


class MemoryNotCreatedError(SharedMemoryError):
    """共享内存未创建"""
    pass


class VersionMismatchError(SharedMemoryError):
    """版本检查失败（读到半帧数据）"""
    pass


class SharedMemoryManager:
    """
    共享内存管理器
    
    支持两种模式：
    - 写入模式（解码器使用）：创建共享内存并写入数据
    - 读取模式（检测器使用）：打开现有共享内存并读取数据
    """
    
    # 共享内存常量
    HEADER_SIZE = 20  # 头部大小（字节）
    
    # YUV420p 布局
    Y_SIZE_420P = 640 * 640  # Y 平面大小
    U_SIZE_420P = 320 * 320  # U 平面大小
    V_SIZE_420P = 320 * 320  # V 平面大小
    TOTAL_SIZE_420P = HEADER_SIZE + Y_SIZE_420P + U_SIZE_420P + V_SIZE_420P
    
    # NV12 布局
    Y_SIZE_NV12 = 640 * 640  # Y 平面大小
    UV_SIZE_NV12 = 320 * 640  # UV 交错平面大小
    TOTAL_SIZE_NV12 = HEADER_SIZE + Y_SIZE_NV12 + UV_SIZE_NV12
    
    # 使用 YUV420p 作为默认大小（向后兼容）
    TOTAL_SIZE = TOTAL_SIZE_420P
    
    # 默认重试次数
    DEFAULT_MAX_RETRIES = 3
    
    # Windows 临时文件路径
    SHM_FILE_DIR = os.path.join(os.environ.get('TEMP', 'C:\\Windows\\Temp'), 'shm')
    
    def __init__(self, name: str = "yolo_detection_channel", create: bool = False, 
                 total_size: Optional[int] = None, max_retries: int = DEFAULT_MAX_RETRIES):
        """
        初始化共享内存管理器
        
        Args:
            name: 共享内存名称
            create: 是否创建新的共享内存（True=写入端，False=读取端）
            total_size: 共享内存大小（字节），默认使用 TOTAL_SIZE_420P
            max_retries: 版本不匹配时的最大重试次数，默认 3
        """
        self.name = name
        self.create = create
        self.total_size = total_size if total_size is not None else self.TOTAL_SIZE
        self.max_retries = max_retries
        self.mmap: Optional[mmap.mmap] = None
        self.is_open = False
        self.file_path = os.path.join(self.SHM_FILE_DIR, f"{name}.dat")
        
        # 确保目录存在
        if create:
            os.makedirs(self.SHM_FILE_DIR, exist_ok=True)
        
        if create:
            self._create()
        else:
            self._open()
    
    def _create(self):
        """创建共享内存（写入端）"""
        try:
            # 创建文件并设置大小
            with open(self.file_path, 'wb') as f:
                f.seek(self.total_size - 1)
                f.write(b'\x00')
            
            # 创建内存映射
            self.mmap = mmap.mmap(-1, self.total_size, tagname=self.name)
            self.is_open = True
            print(f"[SharedMemory] 创建成功：{self.name} ({self.total_size} 字节)")
        except Exception as e:
            raise SharedMemoryError(f"创建共享内存失败：{e}")
    
    def _open(self):
        """打开共享内存（读取端）"""
        try:
            # 检查文件是否存在
            if not os.path.exists(self.file_path):
                raise FileNotFoundError(f"共享内存文件不存在：{self.file_path}")
            
            # 打开现有文件映射
            self.mmap = mmap.mmap(-1, self.total_size, tagname=self.name)
            self.is_open = True
            print(f"[SharedMemory] 打开成功：{self.name}")
        except FileNotFoundError:
            raise MemoryNotCreatedError(f"共享内存未创建：{self.name}")
        except Exception as e:
            raise SharedMemoryError(f"打开共享内存失败：{e}")
    
    def close(self):
        """关闭共享内存"""
        if self.mmap:
            self.mmap.close()
            self.is_open = False
            print(f"[SharedMemory] 已关闭：{self.name}")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # ========== 写入端方法（解码器使用） ==========
    
    def write_header(self, header: FrameHeader):
        """
        写入帧头部信息
        
        Args:
            header: 帧头部信息
        """
        if not self.is_open:
            raise MemoryNotCreatedError("共享内存未打开")
        
        # 打包头部数据（小端序）
        header_data = struct.pack(
            '<IQQ',  # uint32 + uint64 + uint64
            header.sequence_num,
            header.timestamp_ns,
            header.write_time_ns
        )
        
        # 写入头部
        self.mmap.seek(0)
        self.mmap.write(header_data)
        self.mmap.flush()
    
    def write_frame(self, y: np.ndarray, u: np.ndarray, v: np.ndarray):
        """
        写入 YUV 帧数据（YUV420p 格式）
        
        Args:
            y: Y 平面 (640, 640)
            u: U 平面 (320, 320)
            v: V 平面 (320, 320)
        """
        if not self.is_open:
            raise MemoryNotCreatedError("共享内存未打开")
        
        # 验证输入形状
        assert y.shape == (640, 640), f"Y 平面形状错误：{y.shape}"
        assert u.shape == (320, 320), f"U 平面形状错误：{u.shape}"
        assert v.shape == (320, 320), f"V 平面形状错误：{v.shape}"
        
        # 确保数据类型为 uint8
        y = y.astype(np.uint8)
        u = u.astype(np.uint8)
        v = v.astype(np.uint8)
        
        # 写入 Y 平面（偏移量 20）
        y_offset = self.HEADER_SIZE
        self.mmap.seek(y_offset)
        self.mmap.write(y.tobytes())
        
        # 写入 U 平面（偏移量 20 + 409600）
        u_offset = y_offset + self.Y_SIZE_420P
        self.mmap.seek(u_offset)
        self.mmap.write(u.tobytes())
        
        # 写入 V 平面（偏移量 20 + 409600 + 102400）
        v_offset = u_offset + self.U_SIZE_420P
        self.mmap.seek(v_offset)
        self.mmap.write(v.tobytes())
        
        # 刷新到内存
        self.mmap.flush()
    
    def write_frame_nv12(self, y: np.ndarray, uv: np.ndarray):
        """
        写入 NV12 帧数据
        
        Args:
            y: Y 平面 (640, 640)
            uv: UV 交错平面 (320, 640)
        """
        if not self.is_open:
            raise MemoryNotCreatedError("共享内存未打开")
        
        # 验证输入形状
        assert y.shape == (640, 640), f"Y 平面形状错误：{y.shape}"
        assert uv.shape == (320, 640), f"UV 平面形状错误：{uv.shape}"
        
        # 确保数据类型为 uint8
        y = y.astype(np.uint8)
        uv = uv.astype(np.uint8)
        
        # 写入 Y 平面（偏移量 20）
        y_offset = self.HEADER_SIZE
        self.mmap.seek(y_offset)
        self.mmap.write(y.tobytes())
        
        # 写入 UV 平面（偏移量 20 + 409600）
        uv_offset = y_offset + self.Y_SIZE_NV12
        self.mmap.seek(uv_offset)
        self.mmap.write(uv.tobytes())
        
        # 刷新到内存
        self.mmap.flush()
    
    def write_complete_frame(self, header: FrameHeader, y: np.ndarray, u: np.ndarray, v: np.ndarray):
        """
        写入完整帧（头部 + 数据）
        
        Args:
            header: 帧头部信息
            y: Y 平面 (640, 640)
            u: U 平面 (320, 320)
            v: V 平面 (320, 320)
        """
        self.write_header(header)
        self.write_frame(y, u, v)
    
    # ========== 读取端方法（检测器使用） ==========
    
    def read_header(self) -> FrameHeader:
        """
        读取帧头部信息
        
        Returns:
            FrameHeader: 帧头部信息
        """
        if not self.is_open:
            raise MemoryNotCreatedError("共享内存未打开")
        
        # 读取头部数据
        self.mmap.seek(0)
        header_data = self.mmap.read(self.HEADER_SIZE)
        
        # 解包数据
        sequence_num, timestamp_ns, write_time_ns = struct.unpack('<IQQ', header_data)
        
        return FrameHeader(
            sequence_num=sequence_num,
            timestamp_ns=timestamp_ns,
            write_time_ns=write_time_ns
        )
    
    def _read_y_plane(self) -> np.ndarray:
        """读取 Y 平面"""
        self.mmap.seek(self.HEADER_SIZE)
        y_data = self.mmap.read(self.Y_SIZE_420P)
        return np.frombuffer(y_data, dtype=np.uint8).reshape((640, 640))
    
    def _read_u_plane(self) -> np.ndarray:
        """读取 U 平面"""
        u_offset = self.HEADER_SIZE + self.Y_SIZE_420P
        self.mmap.seek(u_offset)
        u_data = self.mmap.read(self.U_SIZE_420P)
        return np.frombuffer(u_data, dtype=np.uint8).reshape((320, 320))
    
    def _read_v_plane(self) -> np.ndarray:
        """读取 V 平面"""
        v_offset = self.HEADER_SIZE + self.Y_SIZE_420P + self.U_SIZE_420P
        self.mmap.seek(v_offset)
        v_data = self.mmap.read(self.V_SIZE_420P)
        return np.frombuffer(v_data, dtype=np.uint8).reshape((320, 320))
    
    def read_frame(self) -> Tuple[FrameHeader, np.ndarray, np.ndarray, np.ndarray]:
        """
        读取 YUV 帧数据（带版本检查）
        
        使用序列号双重验证避免读到半帧数据：
        1. 读取头部
        2. 读取 YUV 数据
        3. 再次读取头部验证一致性
        4. 如果序列号不匹配，重新读取
        
        Returns:
            Tuple[FrameHeader, np.ndarray, np.ndarray, np.ndarray]:
                帧头部、Y 平面、U 平面、V 平面
        """
        if not self.is_open:
            raise MemoryNotCreatedError("共享内存未打开")
        
        for attempt in range(self.max_retries):
            try:
                # 读取头部
                header1 = self.read_header()
                
                # 读取 YUV 数据
                y_plane = self._read_y_plane()
                u_plane = self._read_u_plane()
                v_plane = self._read_v_plane()
                
                # 再次读取头部验证一致性
                header2 = self.read_header()
                
                # 如果序列号不匹配，说明数据被修改，重新读取
                if header1.sequence_num != header2.sequence_num:
                    if attempt < self.max_retries - 1:
                        print(f"[SharedMemory] 版本不匹配，重试 {attempt + 1}/{self.max_retries}")
                        continue
                    else:
                        raise VersionMismatchError("多次重试后版本仍不匹配")
                
                return header1, y_plane, u_plane, v_plane
            
            except VersionMismatchError:
                if attempt == self.max_retries - 1:
                    raise
                continue
        
        # 不应该到达这里
        raise SharedMemoryError("读取帧数据失败")

    def _read_y_plane_nv12(self) -> np.ndarray:
        """读取 Y 平面（NV12格式）"""
        self.mmap.seek(self.HEADER_SIZE)
        y_data = self.mmap.read(self.Y_SIZE_NV12)
        return np.frombuffer(y_data, dtype=np.uint8).reshape((640, 640))

    def _read_uv_plane_nv12(self) -> np.ndarray:
        """读取 UV 交错平面（NV12格式）"""
        uv_offset = self.HEADER_SIZE + self.Y_SIZE_NV12
        self.mmap.seek(uv_offset)
        uv_data = self.mmap.read(self.UV_SIZE_NV12)
        return np.frombuffer(uv_data, dtype=np.uint8).reshape((320, 640))

    def read_frame_nv12(self) -> Tuple[FrameHeader, np.ndarray, np.ndarray]:
        """
        读取 NV12 帧数据（带版本检查）

        使用序列号双重验证避免读到半帧数据：
        1. 读取头部
        2. 读取 NV12 数据
        3. 再次读取头部验证一致性
        4. 如果序列号不匹配，重新读取

        Returns:
            Tuple[FrameHeader, np.ndarray, np.ndarray]:
                帧头部、Y 平面、UV 交错平面
        """
        if not self.is_open:
            raise MemoryNotCreatedError("共享内存未打开")

        for attempt in range(self.max_retries):
            try:
                # 读取头部
                header1 = self.read_header()

                # 读取 NV12 数据
                y_plane = self._read_y_plane_nv12()
                uv_plane = self._read_uv_plane_nv12()

                # 再次读取头部验证一致性
                header2 = self.read_header()

                # 如果序列号不匹配，说明数据被修改，重新读取
                if header1.sequence_num != header2.sequence_num:
                    if attempt < self.max_retries - 1:
                        print(f"[SharedMemory] 版本不匹配，重试 {attempt + 1}/{self.max_retries}")
                        continue
                    else:
                        raise VersionMismatchError("多次重试后版本仍不匹配")

                return header1, y_plane, uv_plane

            except VersionMismatchError:
                if attempt == self.max_retries - 1:
                    raise
                continue

        # 不应该到达这里
        raise SharedMemoryError("读取帧数据失败")

    def get_current_header(self) -> Optional[FrameHeader]:
        """
        获取当前头部信息（不读取数据）
        
        Returns:
            FrameHeader 或 None（如果失败）
        """
        try:
            return self.read_header()
        except Exception:
            return None


def create_shared_memory(name: str = "yolo_detection_channel", 
                         total_size: Optional[int] = None, 
                         max_retries: int = SharedMemoryManager.DEFAULT_MAX_RETRIES) -> SharedMemoryManager:
    """
    创建共享内存（写入端）
    
    Args:
        name: 共享内存名称
        total_size: 共享内存大小（字节），默认使用 TOTAL_SIZE_420P
        max_retries: 版本不匹配时的最大重试次数
    
    Returns:
        SharedMemoryManager: 写入端管理器
    """
    return SharedMemoryManager(name=name, create=True, total_size=total_size, max_retries=max_retries)


def open_shared_memory(name: str = "yolo_detection_channel", 
                       total_size: Optional[int] = None,
                       max_retries: int = SharedMemoryManager.DEFAULT_MAX_RETRIES) -> SharedMemoryManager:
    """
    打开共享内存（读取端）
    
    Args:
        name: 共享内存名称
        total_size: 共享内存大小（字节），默认使用 TOTAL_SIZE_420P
        max_retries: 版本不匹配时的最大重试次数
    
    Returns:
        SharedMemoryManager: 读取端管理器
    """
    return SharedMemoryManager(name=name, create=False, total_size=total_size, max_retries=max_retries)
