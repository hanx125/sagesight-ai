"""
视频解码器模块

使用 FFmpeg 进行 RTSP 流硬件加速解码，输出 YUV420p 格式到共享内存。
支持 DXVA2（Windows）、CUDA（NVIDIA）、QSV（Intel）硬件加速。

解码流程：
RTSP 流 → FFmpeg 解复用 → 硬件解码（DXVA2/CUDA/QSV） → YUV420p → 共享内存
"""

import subprocess
import threading
import time
import numpy as np
from typing import Optional, Union, Dict, Any
from dataclasses import dataclass
from ..utils.shared_memory import SharedMemoryManager, FrameHeader, create_shared_memory
from ..utils.frame_queue import FrameQueue, QueuedFrame
from ..utils.shared_memory_ring_buffer import SharedMemoryRingBuffer
from ..utils.frame_index_queue import FrameIndexQueue, FrameIndex


class DecoderError(Exception):
    """解码器异常基类"""
    pass


class RTSPConnectionError(DecoderError):
    """RTSP 连接失败"""
    pass


class DecodeError(DecoderError):
    """解码失败"""
    pass


@dataclass
class DecoderConfig:
    """解码器配置"""
    rtsp_url: str = ""
    video_file: str = ""
    width: int = 640
    height: int = 640
    hwaccel: str = "dxva2"
    output_format: str = "auto"
    rtsp_transport: str = "tcp"
    reconnect: bool = True
    reconnect_delay: int = 5
    buffer_size: int = 1024 * 1024
    frame_queue_size: int = 5

    # 共享内存配置参数
    shm_total_size: Optional[int] = None
    shm_max_retries: int = 3
    
    # 环形缓冲区 + 索引队列架构（默认启用）
    use_ring_buffer: bool = True
    ring_buffer_block_count: int = 5
    index_queue_size: int = 30


class VideoDecoder:
    """
    视频解码器
    
    使用 FFmpeg 子进程进行硬件加速解码，将 YUV420p 帧写入共享内存。
    """
    
    def __init__(self, config: Union[DecoderConfig, Dict]):
        """
        初始化视频解码器
        
        Args:
            config: 解码器配置（支持DecoderConfig对象或字典）
        """
        # 如果传入字典，转换为DecoderConfig对象
        if isinstance(config, dict):
            # 字典字段映射（兼容不同的字段名）
            config = DecoderConfig(
                rtsp_url=config.get('rtsp_url', config.get('input_uri', '')),
                video_file=config.get('video_file', ''),
                width=config.get('width', 640),
                height=config.get('height', 640),
                hwaccel=config.get('hwaccel', 'dxva2'),
                output_format=config.get('output_format', 'auto'),
                rtsp_transport=config.get('rtsp_transport', 'tcp'),
                reconnect=config.get('reconnect', True),
                reconnect_delay=config.get('reconnect_delay', 5),
                buffer_size=config.get('buffer_size', 1024 * 1024),
                frame_queue_size=config.get('frame_queue_size', 5),
                shm_total_size=config.get('shm_total_size'),
                shm_max_retries=config.get('shm_max_retries', 3),
                use_ring_buffer=config.get('use_ring_buffer', False),
                ring_buffer_block_count=config.get('ring_buffer_block_count', 5),
                index_queue_size=config.get('index_queue_size', 30)
            )
        
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.shared_memory: Optional[SharedMemoryManager] = None
        self.frame_queue: Optional[FrameQueue] = None
        
        # 新架构组件（可选）
        self.ring_buffer: Optional[SharedMemoryRingBuffer] = None
        self.index_queue: Optional[FrameIndexQueue] = None
        
        self.is_running = False
        self.sequence_num = 0
        self.thread: Optional[threading.Thread] = None
        self.stats = {
            'frames_decoded': 0,
            'decode_errors': 0,
            'reconnect_count': 0,
            'start_time': None,
            'last_frame_time': None,
        }
        
        # 根据硬件加速方式选择 FFmpeg 参数
        self._init_ffmpeg_args()
    
    def _init_ffmpeg_args(self):
        """初始化 FFmpeg 命令行参数"""
        # 基础参数
        self.ffmpeg_args = [
            'ffmpeg',
            '-loglevel', 'warning',  # 日志级别
        ]
        
        # 判断是视频文件还是 RTSP 流
        if self.config.video_file:
            # 视频文件输入（按原始帧率解码）
            
            # 硬件加速参数（输入选项，放在 -i 前面）
            if self.config.hwaccel == 'dxva2':
                self.ffmpeg_args.extend([
                    '-hwaccel', 'dxva2',
                ])
            elif self.config.hwaccel == 'cuda':
                self.ffmpeg_args.extend([
                    '-hwaccel', 'cuda',
                    '-hwaccel_output_format', 'nv12',
                ])
            elif self.config.hwaccel == 'qsv':
                self.ffmpeg_args.extend([
                    '-hwaccel', 'qsv',
                ])
            
            # 输入源（使用 -re 按原始帧率读取）
            self.ffmpeg_args.extend([
                '-re',  # 按原始帧率读取（避免全速解码）
                '-i', self.config.video_file,
            ])
            
            print(f"[VideoDecoder] 视频文件：{self.config.video_file}")
        else:
            # RTSP 流输入
            self.ffmpeg_args.extend([
                '-rtsp_transport', self.config.rtsp_transport,
            ])
            
            # 注意：FFmpeg 不支持 -reconnect 选项（这是 ffplay 特有的）
            # RTSP 重连由 Python 代码在 FFmpeg 进程退出后处理
            
            # 硬件加速参数
            if self.config.hwaccel == 'dxva2':
                # DXVA2（Windows 硬件加速）- 推荐用于 RTX 2070
                self.ffmpeg_args.extend([
                    '-hwaccel', 'dxva2',
                    # DXVA2 自动输出 NV12，无需额外参数
                ])
            elif self.config.hwaccel == 'cuda':
                # CUDA（NVIDIA GPU）- 需要特殊配置
                # 注意：CUDA 解码 2K 视频会失败（表面数量超限）
                # 使用 NV12 格式而非 yuv420p
                self.ffmpeg_args.extend([
                    '-hwaccel', 'cuda',
                    '-hwaccel_output_format', 'nv12',  # CUDA 原生格式
                    '-extra_hw_frames', '16',  # 限制硬件帧数量，避免超限
                    '-threads', '16',  # 限制线程数，避免 CUDA 表面超限（关键！）
                ])
            elif self.config.hwaccel == 'qsv':
                # QSV（Intel 核显）
                self.ffmpeg_args.extend([
                    '-hwaccel', 'qsv',
                    '-hwaccel_output_format', 'nv12',
                ])
            else:
                # 无硬件加速（CPU 解码）
                self.ffmpeg_args.extend([
                    '-c:v', 'h264',
                ])
            
            # 输入源
            self.ffmpeg_args.extend([
                '-i', self.config.rtsp_url,
            ])
        # 输出格式：根据配置选择
        if self.config.output_format == 'auto':
            # 自动模式：根据硬件加速方式选择最优格式
            if self.config.hwaccel in ['dxva2', 'cuda', 'qsv']:
                # 硬件加速使用 NV12 格式（性能最优）
                pix_fmt = 'nv12'
            else:
                # CPU 解码使用 YUV420p 格式（兼容性好）
                pix_fmt = 'yuv420p'
        else:
            # 用户指定格式
            pix_fmt = self.config.output_format

        self.ffmpeg_args.extend([
            '-pix_fmt', pix_fmt,
        ])

        print(f"[VideoDecoder] 输出格式：{pix_fmt}（硬件加速：{self.config.hwaccel}，配置：{self.config.output_format}）")

        # 缩放和格式转换（使用滤镜确保正确的 stride）
        # 注意：DXVA2/QSV 硬件加速解码后，FFmpeg 输出的 rawvideo 可能有 stride 对齐
        # 使用 -vf 滤镜可以确保输出紧凑的帧数据（无 stride 填充）
        vf_filters = []
        if self.config.width or self.config.height:
            vf_filters.append(f'scale={self.config.width}:{self.config.height}')
        # 强制使用指定的像素格式（去除硬件解码器的 stride 对齐）
        vf_filters.append(f'format={pix_fmt}')
        
        if vf_filters:
            self.ffmpeg_args.extend([
                '-vf', ','.join(vf_filters),
            ])

        self.ffmpeg_args.extend([
            '-f', 'rawvideo',  # 原始视频格式
            'pipe:1',  # 输出到 stdout
        ])
        
        print(f"[VideoDecoder] FFmpeg 命令：{' '.join(self.ffmpeg_args)}")
    
    def start(self) -> bool:
        """
        启动解码线程
        
        Returns:
            bool: 启动是否成功
        """
        if self.is_running:
            print("[VideoDecoder] 已在运行中")
            return True
        
        try:
            # 创建共享内存（写入端）
            self.shared_memory = create_shared_memory(
                total_size=self.config.shm_total_size,
                max_retries=self.config.shm_max_retries
            )
            
            # 创建帧队列
            self.frame_queue = FrameQueue(max_size=self.config.frame_queue_size)
            
            # 新架构：创建环形缓冲区和索引队列（可选）
            if self.config.use_ring_buffer:
                self.ring_buffer = SharedMemoryRingBuffer(
                    block_count=self.config.ring_buffer_block_count,
                    width=self.config.width,
                    height=self.config.height
                )
                
                self.index_queue = FrameIndexQueue(
                    max_size=self.config.index_queue_size
                )
                
                print(f"[VideoDecoder] 使用新架构：环形缓冲区 + 索引队列")
            else:
                print(f"[VideoDecoder] 使用旧架构：单帧共享内存 + 帧队列")
            
            # 启动 FFmpeg 进程
            # stderr=subprocess.STDOUT: 将错误输出重定向到标准输出，便于调试
            self.process = subprocess.Popen(
                self.ffmpeg_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # 重定向 stderr 到 stdout
                bufsize=self.config.buffer_size,
            )
            
            # 等待 FFmpeg 启动（检查是否立即退出）
            time.sleep(0.5)
            if self.process.poll() is not None:
                # FFmpeg 立即退出，读取错误输出
                stderr_output = self.process.stdout.read().decode('utf-8', errors='replace')
                print(f"[VideoDecoder] FFmpeg 启动失败，返回码={self.process.poll()}")
                print(f"[VideoDecoder] 错误信息：{stderr_output[:1000]}")
                return False
            
            # 启动解码线程
            self.is_running = True
            self.stats['start_time'] = time.time()
            self.thread = threading.Thread(target=self._decode_loop, daemon=True)
            self.thread.start()
            
            print(f"[VideoDecoder] 启动成功（{self.config.hwaccel} 硬件加速）")
            return True
        
        except FileNotFoundError:
            raise DecoderError("FFmpeg 未找到，请确保已安装 FFmpeg 并添加到 PATH")
        except Exception as e:
            raise DecoderError(f"启动解码器失败：{e}")
    
    def stop(self):
        """停止解码线程"""
        self.is_running = False
        
        # 终止 FFmpeg 进程
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        
        # 关闭共享内存
        if self.shared_memory:
            self.shared_memory.close()
            self.shared_memory = None
        
        # 关闭环形缓冲区（新架构）
        if self.ring_buffer:
            self.ring_buffer.close()
            self.ring_buffer = None
        
        # 清空索引队列（新架构）
        if self.index_queue:
            self.index_queue.clear()
            self.index_queue = None
        
        # 清空帧队列
        if self.frame_queue:
            self.frame_queue.clear()
            self.frame_queue = None
        
        print("[VideoDecoder] 已停止")
    
    def _decode_loop(self):
        """解码主循环"""
        # 根据输出格式计算帧大小
        # 确定实际使用的像素格式
        if self.config.output_format == 'auto':
            if self.config.hwaccel in ['dxva2', 'cuda', 'qsv']:
                pix_fmt = 'nv12'
            else:
                pix_fmt = 'yuv420p'
        else:
            pix_fmt = self.config.output_format

        if pix_fmt == 'nv12':
            # NV12 格式：Y 平面 + UV 交错平面
            frame_size = self.config.width * self.config.height
            y_size = frame_size
            uv_size = frame_size // 2  # UV 交错
            total_size = y_size + uv_size
            is_nv12 = True
        else:
            # YUV420p 格式
            frame_size = self.config.width * self.config.height
            y_size = frame_size
            u_size = frame_size // 4
            v_size = frame_size // 4
            total_size = y_size + u_size + v_size
            is_nv12 = False

        buffer = b''
        error_count = 0
        
        while self.is_running:
            try:
                # 检查 FFmpeg 进程状态
                if self.process.poll() is not None:
                    # FFmpeg 进程已退出
                    ret_code = self.process.poll()
                    print(f"[VideoDecoder] FFmpeg 进程退出，返回码={ret_code}")
                    if error_count < 5:
                        # 读取 stderr 输出（已重定向到 stdout）
                        if self.process.stdout:
                            stderr_output = self.process.stdout.read().decode('utf-8', errors='replace')
                            if stderr_output:
                                print(f"[VideoDecoder] FFmpeg 输出:\n{stderr_output[:2000]}")
                            else:
                                print("[VideoDecoder] FFmpeg stdout 为空")
                        error_count += 1
                    if self.config.reconnect:
                        print(f"[VideoDecoder] {self.config.reconnect_delay}秒后重连...")
                        time.sleep(self.config.reconnect_delay)
                        self.stats['reconnect_count'] += 1
                        self._restart_ffmpeg()
                    else:
                        break
                    continue
                
                # 从 FFmpeg stdout 读取数据（非阻塞）
                try:
                    # 设置超时（Windows 下使用 select 不可用，直接读取）
                    chunk = self.process.stdout.read(4096)
                    
                    if not chunk:
                        # 没有数据可读，可能是 FFmpeg 还未开始输出
                        time.sleep(0.1)
                        continue
                except Exception as e:
                    # 读取错误，等待后重试
                    print(f"[VideoDecoder] 读取错误：{e}")
                    time.sleep(0.1)
                    continue
                
                buffer += chunk
                
                # 当缓冲区有足够数据时，提取帧
                while len(buffer) >= total_size:
                    # 提取一帧数据
                    frame_data = buffer[:total_size]
                    buffer = buffer[total_size:]
                    
                    if is_nv12:
                        # NV12 格式解析
                        y_data = frame_data[:y_size]
                        uv_data = frame_data[y_size:]
                        
                        # 转换为 numpy 数组
                        # NV12: Y 平面 (640x640) + UV 交错平面 (320x640)
                        y = np.frombuffer(y_data, dtype=np.uint8).reshape((self.config.height, self.config.width))
                        uv = np.frombuffer(uv_data, dtype=np.uint8).reshape((self.config.height // 2, self.config.width))
                        
                        # 调试输出
                        if self.stats['frames_decoded'] < 3:
                            print(f"[VideoDecoder] NV12 帧 {self.stats['frames_decoded']}: Y shape={y.shape}, UV shape={uv.shape}, Y range=[{y.min()}, {y.max()}], UV range=[{uv.min()}, {uv.max()}]")
                        
                        # 写入共享内存（NV12 格式）
                        self._write_to_shared_memory_nv12(y, uv)
                    else:
                        # YUV420p 格式解析
                        y_data = frame_data[:y_size]
                        u_data = frame_data[y_size:y_size + u_size]
                        v_data = frame_data[y_size + u_size:]
                        
                        # 转换为 numpy 数组
                        y = np.frombuffer(y_data, dtype=np.uint8).reshape((self.config.height, self.config.width))
                        u = np.frombuffer(u_data, dtype=np.uint8).reshape((self.config.height // 2, self.config.width // 2))
                        v = np.frombuffer(v_data, dtype=np.uint8).reshape((self.config.height // 2, self.config.width // 2))
                        
                        # 写入共享内存（YUV420p 格式）
                        self._write_to_shared_memory(y, u, v)
                    
                    # 更新统计
                    self.stats['frames_decoded'] += 1
                    self.stats['last_frame_time'] = time.time()
            
            except Exception as e:
                self.stats['decode_errors'] += 1
                print(f"[VideoDecoder] 解码错误：{e}")
                if self.config.reconnect:
                    time.sleep(self.config.reconnect_delay)
                    self._restart_ffmpeg()
                else:
                    break
    
    def _write_to_shared_memory(self, y: np.ndarray, u: np.ndarray, v: np.ndarray):
        """
        写入帧到共享内存（YUV420p 格式）
        
        流程：
        1. 创建帧头部
        2. 将帧加入队列（用于CameraWorker读取）
        3. 直接写入共享内存（不从队列取）
        
        Args:
            y: Y 平面
            u: U 平面
            v: V 平面
        """
        if not self.shared_memory or not self.shared_memory.is_open:
            return
        
        # 创建帧头部
        header = FrameHeader(
            sequence_num=self.sequence_num,
            timestamp_ns=time.time_ns(),
            write_time_ns=time.time_ns(),
            width=self.config.width,
            height=self.config.height,
            format="yuv420p"
        )
        
        # 将帧加入队列（供CameraWorker读取）
        if self.frame_queue:
            self.frame_queue.enqueue(header, y, u, v)
        
        # 直接写入共享内存（不使用队列，避免帧被消费）
        self.shared_memory.write_complete_frame(header, y, u, v)
        
        # 更新序列号
        self.sequence_num += 1
    
    def _write_to_shared_memory_nv12(self, y: np.ndarray, uv: np.ndarray):
        """
        写入帧到共享内存（NV12 格式）
        
        使用环形缓冲区 + 索引队列架构，实现零拷贝数据传输。
        
        Args:
            y: Y 平面
            uv: UV 交错平面
        """
        if not self.shared_memory or not self.shared_memory.is_open:
            return
        
        header = FrameHeader(
            sequence_num=self.sequence_num,
            timestamp_ns=time.time_ns(),
            write_time_ns=time.time_ns(),
            width=self.config.width,
            height=self.config.height,
            format="nv12"
        )
        
        # 写入环形缓冲区（返回块ID）
        if self.ring_buffer and self.index_queue:
            block_id = self.ring_buffer.write_frame_nv12(y, uv)
            
            # 生成FrameIndex并加入索引队列
            self.index_queue.enqueue_index(
                sequence_num=self.sequence_num,
                shm_block_id=block_id,
                timestamp_ns=header.timestamp_ns
            )
        
        self.shared_memory.write_frame_nv12(y, uv)
        self.shared_memory.write_header(header)
        
        self.sequence_num += 1
    
    def _restart_ffmpeg(self):
        """重启 FFmpeg 进程"""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        try:
            self.process = subprocess.Popen(
                self.ffmpeg_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=self.config.buffer_size,
            )
            print("[VideoDecoder] FFmpeg 进程已重启")
        except Exception as e:
            print(f"[VideoDecoder] 重启 FFmpeg 失败：{e}")
    
    def get_frame_index(self) -> Optional[FrameIndex]:
        """
        获取帧索引（新架构：索引队列）
        
        Returns:
            FrameIndex: 帧索引，如果队列为空则返回None
        """
        if not self.index_queue or not self.is_running:
            return None
        
        return self.index_queue.dequeue_index()
    
    def get_frame(self, timeout_ms: float = 100) -> Optional[QueuedFrame]:
        """
        获取解码后的帧（从帧队列取出）
        
        Args:
            timeout_ms: 获取帧的超时时间（毫秒），默认100ms
        
        Returns:
            QueuedFrame: 帧数据（包含header、Y/U/V平面），如果队列为空则返回None
        """
        if not self.frame_queue or not self.is_running:
            return None
        
        # 尝试从帧队列获取帧
        frame = self.frame_queue.dequeue()
        
        # 如果队列为空，短暂等待后再次尝试
        if frame is None and timeout_ms > 0:
            wait_time = min(timeout_ms / 1000, 0.1)  # 最多等待100ms
            time.sleep(wait_time)
            frame = self.frame_queue.dequeue()
        
        return frame
    
    def get_latest_frame(self) -> Optional[QueuedFrame]:
        """
        获取最新的帧（丢弃中间帧）
        
        适用场景：
        - 实时性要求高（不需要处理所有帧）
        - 检测器处理速度跟不上解码速度
        
        Returns:
            QueuedFrame: 最新帧数据，如果队列为空则返回None
        """
        if not self.frame_queue or not self.is_running:
            return None
        
        return self.frame_queue.dequeue_latest()
    
    def peek_frame(self) -> Optional[QueuedFrame]:
        """
        查看最新的帧（不取出）
        
        适用场景：
        - 检查是否有帧可用
        - 获取帧信息但不处理
        
        Returns:
            QueuedFrame: 最新帧数据，如果队列为空则返回None
        """
        if not self.frame_queue or not self.is_running:
            return None
        
        return self.frame_queue.peek()
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取解码统计信息
        
        Returns:
            Dict: 包含解码器统计信息的字典
        """
        stats = self.stats.copy()
        
        # 添加帧队列统计
        if self.frame_queue:
            queue_stats = self.frame_queue.get_stats()
            stats['frame_queue'] = {
                'size': queue_stats.current_size,
                'max_size': queue_stats.max_size,
                'total_frames': queue_stats.total_frames,
                'dropped_frames': queue_stats.dropped_frames,
                'avg_delay_ms': queue_stats.avg_queue_delay_ms,
            }
        
        return stats
    
    def print_stats(self):
        """打印详细统计信息"""
        print("\n" + "="*60)
        print("视频解码器统计信息")
        print("="*60)
        print(f"解码帧数：{self.stats['frames_decoded']}")
        print(f"解码错误：{self.stats['decode_errors']}")
        print(f"重连次数：{self.stats['reconnect_count']}")
        
        if self.stats['start_time']:
            elapsed = time.time() - self.stats['start_time']
            print(f"运行时间：{elapsed:.1f} 秒")
            if elapsed > 0:
                fps = self.stats['frames_decoded'] / elapsed
                print(f"平均 FPS: {fps:.2f}")
        
        # 打印帧队列统计
        if self.frame_queue:
            print("\n帧队列统计:")
            self.frame_queue.print_stats()
        
        print("="*60)
    
    def get_stats_old(self) -> Dict[str, Any]:
        """
        获取解码统计信息（旧版本，保留兼容）
        
        Returns:
            统计信息字典
        """
        stats = self.stats.copy()
        
        # 计算 FPS
        if stats['start_time']:
            elapsed = time.time() - stats['start_time']
            stats['elapsed_seconds'] = elapsed
            stats['fps'] = stats['frames_decoded'] / elapsed if elapsed > 0 else 0
        else:
            stats['elapsed_seconds'] = 0
            stats['fps'] = 0
        
        # 计算实时 FPS（最近 1 秒）
        if stats['last_frame_time']:
            recent_frames = stats['frames_decoded']
            recent_time = time.time() - stats['start_time']
            stats['recent_fps'] = recent_frames / recent_time if recent_time > 0 else 0
        
        return stats
    
    def print_stats(self):
        """打印统计信息"""
        stats = self.get_stats()
        print("\n" + "="*50)
        print("视频解码器统计信息")
        print("="*50)
        print(f"运行时间：{stats['elapsed_seconds']:.1f} 秒")
        print(f"解码帧数：{stats['frames_decoded']} 帧")
        print(f"平均 FPS: {stats['fps']:.2f}")
        print(f"解码错误：{stats['decode_errors']}")
        print(f"重连次数：{stats['reconnect_count']}")
        print(f"硬件加速：{self.config.hwaccel}")
        print("="*50)


def create_decoder(
    rtsp_url: str,
    width: int = 640,
    height: int = 640,
    hwaccel: str = "dxva2"
) -> VideoDecoder:
    """
    创建视频解码器
    
    Args:
        rtsp_url: RTSP 流地址
        width: 输出宽度
        height: 输出高度
        hwaccel: 硬件加速方式（dxva2/cuda/qsv/none）
    
    Returns:
        VideoDecoder: 解码器实例
    """
    config = DecoderConfig(
        rtsp_url=rtsp_url,
        width=width,
        height=height,
        hwaccel=hwaccel
    )
    return VideoDecoder(config)
