"""
视频解码器模块

使用 FFmpeg 进行 RTSP 流硬件加速解码，输出 YUV420p/NV12 格式到共享内存。
支持多种硬件加速：NVIDIA CUDA、Intel QSV、AMD VAAPI、Windows DXVA2、Rockchip RKMPP 等。

特性：
1. 硬件加速解码预设（自动选择最优方案）
2. 共享内存环形缓冲区（零拷贝传输）
3. 帧索引队列（生产者-消费者解耦）
4. YUV 图像处理（裁剪、缩放、格式转换）

================================================================================
数据流程图（DXVA2 方案 - 当前实现）
================================================================================

[网络摄像头/视频文件]
     |
     | H.264/H.265 压缩数据流
     ↓
[FFmpeg 拉流/读取] (CPU)
     |
     | 编码数据包（系统内存）
     ↓
[硬件解码] DXVA2 (GPU)
     |
     | YUV420p 原始帧（GPU 显存）
     ↓
[显存→内存拷贝] cudaMemcpy (CPU 参与)
     |
     | YUV420p 原始帧（系统内存）
     ↓
[缩放] scale=640:640 (CPU 软件缩放) ⚠️ CPU 占用 15-25%
     |
     | 缩放后的 YUV420p 帧（系统内存）
     ↓
[YUV420p → NV12 格式转换] (CPU)
     |
     | NV12 格式帧：Y 平面 + UV 交错平面（系统内存）
     ↓
[写入共享内存环形缓冲区] (零拷贝，内存映射)
     |
     | 帧数据在共享内存块中（系统内存）
     ↓
[帧索引队列] (生产者-消费者解耦)
     |
     | 帧索引：序列号 + 块ID + 时间戳
     ↓
[AI 对象检测] (CPU 或 NPU)
     |
     | 从共享内存零拷贝读取帧数据
     ↓
[输出检测结果]

CPU 占用分析：
- 解码：GPU 负责（< 5% CPU）
- 缩放：CPU 负责（15-25% CPU）⚠️ 主要瓶颈
- 内存拷贝：CPU 负责（5-10% CPU）
- 格式转换：CPU 负责（2-5% CPU）
- 总计：15-45% CPU

================================================================================
数据流程图（NVIDIA CUDA 方案 - 理想实现）
================================================================================

[网络摄像头/视频文件]
     |
     | H.264/H.265 压缩数据流
     ↓
[FFmpeg 拉流/读取] (CPU)
     |
     | 编码数据包（系统内存）
     ↓
[硬件解码] NVIDIA NVDEC (GPU)
     |
     | YUV420p 原始帧（GPU 显存）
     ↓
[缩放] scale_cuda (GPU 硬件加速) ✅ 零 CPU 占用
     |
     | 缩放后的 NV12 帧（GPU 显存）
     ↓
[显存→内存拷贝] cudaMemcpy (CPU 参与)
     |
     | NV12 格式帧（系统内存）
     ↓
[写入共享内存环形缓冲区] (零拷贝)
     |
     ↓
[AI 对象检测] (CPU 或 NPU)
     |
     ↓
[输出检测结果]

CPU 占用分析：
- 解码：GPU 负责（< 5% CPU）
- 缩放：GPU 负责（< 2% CPU）✅ 性能提升 60-70%
- 内存拷贝：CPU 负责（5-10% CPU）
- 总计：5-15% CPU

================================================================================
关键组件说明
================================================================================

1. 环形缓冲区（SharedMemoryRingBuffer）：
   - 多块存储（默认 5 块）
   - 零拷贝读取（numpy 内存映射）
   - 避免新帧覆盖正在读取的帧

2. 帧索引队列（FrameIndexQueue）：
   - 生产者-消费者解耦
   - 只存储索引（序列号、块ID、时间戳）
   - 不存储实际帧数据（节省内存）

3. 零拷贝架构：
   - 写入：FFmpeg → 共享内存块（一次拷贝）
   - 读取：共享内存块 → numpy 数组视图（零拷贝）
   - 检测器直接从共享内存读取，无需额外拷贝

================================================================================
使用方式（预设名称 - 推荐）
================================================================================

from src.decoders.video_decoder import create_decoder
from src.utils import FFMPEG_HWACCEL_DXVA2

decoder = create_decoder(
    rtsp_url="rtsp://camera.example.com/stream",
    hwaccel_preset=FFMPEG_HWACCEL_DXVA2,  # Windows DXVA2
    width=640,
    height=640
)

# 读取帧数据（零拷贝）
frame_index = decoder.get_frame_index()
if frame_index:
    y, uv = decoder.get_frame_from_block(frame_index.shm_block_id)

================================================================================
"""

import subprocess
import threading
import time
import numpy as np
from typing import Optional, Union, Dict, Any, List, Tuple
from dataclasses import dataclass

# 导入新架构组件
from ..utils.shared_memory import FrameHeader, SharedMemoryRingBuffer
from ..utils.frame_index_queue import FrameIndexQueue, FrameIndex

# 导入 FFmpeg 预设
from ..utils.ffmpeg_presets import (
    parse_preset_hardware_acceleration_decode,
    parse_preset_hardware_acceleration_scale,
    parse_preset_input,
    EncodeTypeEnum,
)


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
    """
    解码器配置（新架构）

    配置来源优先级：
    1. 任务表中的 decoder_config（最高优先级）
    2. YAML 配置文件中的默认值
    3. 本类中的硬编码默认值（最低优先级）

    推荐使用方式1（预设名称）：
        from src.utils import FFMPEG_HWACCEL_DXVA2
        config = DecoderConfig(
            rtsp_url="rtsp://...",
            hwaccel_preset=FFMPEG_HWACCEL_DXVA2,
        )
    """
    # 输入源配置
    rtsp_url: str = ""
    video_file: str = ""
    input_preset: str = ""  # 输入预设（如 "preset-rtsp-generic")
    camera_id: str = ""  # ✅ 新增：摄像机ID（用于生成唯一的共享内存名称）

    # 视频参数
    width: int = 640
    height: int = 640
    fps: int = 30  # 检测帧率（推荐 30）

    # 硬件加速配置
    hwaccel_preset: str = ""  # 硬件加速预设（如 "preset-nvidia"）

    # 输出配置
    output_format: str = "auto"  # "auto", "nv12", "yuv420p"

    # RTSP 配置
    rtsp_transport: str = "tcp"
    reconnect: bool = True
    reconnect_delay: int = 5
    buffer_size: int = 2 * 1024 * 1024  # 缓冲区大小（2MB）

    # 环形缓冲区配置
    ring_buffer_block_count: int = 10  # 块数量（推荐 10）
    index_queue_size: int = 50  # 索引队列大小（推荐 50）

    # FFmpeg 滤镜参数（用于视频预处理）
    ffmpeg_filters: List[str] = None  # FFmpeg 滤镜参数（如降噪、锐化等）

    def __post_init__(self):
        """初始化后处理"""
        if self.ffmpeg_filters is None:
            self.ffmpeg_filters = []


class VideoDecoder:
    """
    视频解码器（新架构）

    使用 FFmpeg 子进程进行硬件加速解码，将 YUV420p/NV12 帧写入环形缓冲区。

    特性：
    - 支持 FFmpeg 预设系统（硬件加速、输入、输出）
    - 环形缓冲区 + 索引队列（零拷贝架构）
    - 生产者-消费者解耦
    - 自动选择最优硬件加速方案
    """

    def __init__(self, config: Union[DecoderConfig, Dict]):
        """
        初始化视频解码器

        Args:
            config: 解码器配置（支持DecoderConfig对象或字典）
        """
        # 如果传入字典，转换为DecoderConfig对象
        if isinstance(config, dict):
            config = self._dict_to_config(config)

        self.config = config
        self.process: Optional[subprocess.Popen] = None

        # 新架构组件（环形缓冲区 + 索引队列）
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

        # 初始化 FFmpeg 参数
        self._init_ffmpeg_args()

    def _dict_to_config(self, config_dict: Dict) -> DecoderConfig:
        """
        字典转配置对象

        Args:
            config_dict: 配置字典

        Returns:
            DecoderConfig: 配置对象
        """
        return DecoderConfig(
            rtsp_url=config_dict.get('rtsp_url', config_dict.get('input_uri', '')),
            video_file=config_dict.get('video_file', ''),
            input_preset=config_dict.get('input_preset', ''),
            camera_id=config_dict.get('camera_id', ''),  # ✅ 新增：传递 camera_id
            width=config_dict.get('width', 640),
            height=config_dict.get('height', 640),
            fps=config_dict.get('fps', 5),
            hwaccel_preset=config_dict.get('hwaccel_preset', 'preset-dxva2'),  # 使用新版参数（项目约束）
            output_format=config_dict.get('output_format', 'auto'),
            rtsp_transport=config_dict.get('rtsp_transport', 'tcp'),
            reconnect=config_dict.get('reconnect', True),
            reconnect_delay=config_dict.get('reconnect_delay', 5),
            buffer_size=config_dict.get('buffer_size', 1024 * 1024),
            ring_buffer_block_count=config_dict.get('ring_buffer_block_count', 10),
            index_queue_size=config_dict.get('index_queue_size', 50),
            ffmpeg_filters=config_dict.get('ffmpeg_filters', []),
        )
    
    def _init_ffmpeg_args(self):
        """
        初始化 FFmpeg 参数

        使用预设系统：
        1. 使用 hwaccel_preset（硬件加速预设）
        2. 使用 input_preset（输入预设）
        """
        self.ffmpeg_args = ['ffmpeg', '-loglevel', 'warning']

        # ========== 硬件加速参数 ==========
        if self.config.hwaccel_preset:
            # 使用预设（推荐）
            hwaccel_args = parse_preset_hardware_acceleration_decode(
                self.config.hwaccel_preset,
                self.config.fps,
                self.config.width,
                self.config.height
            )
            if hwaccel_args:
                self.ffmpeg_args.extend(hwaccel_args)
                print(f"[VideoDecoder] 使用硬件加速预设：{self.config.hwaccel_preset}")
        else:
            # 未指定预设，使用 CPU 解码
            print("[VideoDecoder] 未指定硬件加速预设，使用 CPU 解码")

        # ========== 输入参数 ==========
        if self.config.video_file:
            # 视频文件输入
            self.ffmpeg_args.extend([
                '-re',  # 按原始帧率读取
                '-i', self.config.video_file,
            ])
            print(f"[VideoDecoder] 视频文件：{self.config.video_file}")
        else:
            # RTSP/网络流输入
            if self.config.input_preset:
                # 使用输入预设
                input_args = parse_preset_input(
                    self.config.input_preset,
                    self.config.fps
                )
                if input_args:
                    self.ffmpeg_args.extend(input_args)
                    print(f"[VideoDecoder] 使用输入预设：{self.config.input_preset}")
            else:
                # 使用默认 RTSP 配置
                self.ffmpeg_args.extend([
                    '-rtsp_transport', self.config.rtsp_transport,
                ])

            # 添加输入源
            self.ffmpeg_args.extend(['-i', self.config.rtsp_url])

        # ========== 输出格式 ==========
        pix_fmt = self._determine_output_format()
        print(f"[VideoDecoder] 输出格式：{pix_fmt}")

        # ========== 缩放和滤镜 ==========
        self._add_scale_filters(pix_fmt)

        # ========== 输出参数 ==========
        self.ffmpeg_args.extend([
            '-pix_fmt', pix_fmt,
            '-f', 'rawvideo',
            'pipe:1',  # 输出到 stdout
        ])

        print(f"[VideoDecoder] FFmpeg 命令：{' '.join(self.ffmpeg_args)}")

    def _determine_output_format(self) -> str:
        """确定输出像素格式"""
        if self.config.output_format != 'auto':
            return self.config.output_format

        # 自动选择最优格式（基于硬件加速预设）
        hwaccel_preset = self.config.hwaccel_preset

        if hwaccel_preset in ['preset-nvidia', 'preset-vaapi', 'preset-intel-qsv-h264',
                               'preset-intel-qsv-h265', 'preset-dxva2']:
            return 'nv12'
        else:
            return 'yuv420p'

    def _add_scale_filters(self, pix_fmt: str):
        """添加缩放滤镜"""
        vf_filters = []

        # 如果指定了硬件加速预设，使用预设的缩放参数
        if self.config.hwaccel_preset:
            scale_args = parse_preset_hardware_acceleration_scale(
                self.config.hwaccel_preset,
                self.config.ffmpeg_filters,  # FFmpeg 滤镜参数
                self.config.fps,
                self.config.width,
                self.config.height
            )

            if scale_args:
                # 预设返回的是参数列表，如 ['-r', '30', '-vf', 'fps=30,scale=640:640']
                # 我们需要提取 -vf 后面的滤镜部分
                for i, arg in enumerate(scale_args):
                    if arg == '-vf' and i + 1 < len(scale_args):
                        # 提取滤镜字符串
                        vf_str = scale_args[i + 1]
                        # 分割多个滤镜
                        vf_filters.extend(vf_str.split(','))
                        break
        else:
            # 使用默认缩放
            if self.config.width or self.config.height:
                # ✅ 添加 `:force_divisible_by=2` 避免 stride 对齐问题
                # 这确保输出的 NV12 格式每行没有额外的 padding
                vf_filters.append(
                    f'scale={self.config.width}:{self.config.height}:flags=lanczos:force_divisible_by=2'
                )

        # 添加格式转换
        if pix_fmt:
            vf_filters.append(f'format={pix_fmt}')

        # 组合滤镜
        if vf_filters:
            filter_str = ','.join(vf_filters)
            self.ffmpeg_args.extend(['-vf', filter_str])
    
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
            # 创建环形缓冲区（✅ 传递 camera_id）
            self.ring_buffer = SharedMemoryRingBuffer(
                block_count=self.config.ring_buffer_block_count,
                width=self.config.width,
                height=self.config.height,
                camera_id=self.config.camera_id  # 传递 camera_id，避免多个解码器共享内存冲突
            )

            # 创建索引队列
            self.index_queue = FrameIndexQueue(
                max_size=self.config.index_queue_size
            )

            print(f"[VideoDecoder] 初始化完成：环形缓冲区（{self.config.ring_buffer_block_count}块） + 索引队列")

            # 启动 FFmpeg 进程
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

            print(f"[VideoDecoder] 启动成功（{self.config.hwaccel_preset or 'CPU'} 硬件加速）")
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

        # 关闭环形缓冲区
        if self.ring_buffer:
            self.ring_buffer.close()
            self.ring_buffer = None

        # 清空索引队列
        if self.index_queue:
            self.index_queue.clear()
            self.index_queue = None

        print("[VideoDecoder] 已停止")
    
    def _decode_loop(self):
        """解码主循环"""
        # 根据输出格式计算帧大小
        # 确定实际使用的像素格式
        if self.config.output_format == 'auto':
            if self.config.hwaccel_preset in ['preset-dxva2', 'preset-nvidia', 'preset-intel-qsv-h264',
                                               'preset-intel-qsv-h265', 'preset-vaapi']:
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

                        # 写入环形缓冲区（NV12 格式）
                        self._write_frame_to_ring_buffer(y, uv)
                    else:
                        # YUV420p 格式解析
                        # YUV420p: Y 平面 + U 平面 + V 平面（分开存储）
                        y_size = self.config.height * self.config.width
                        u_size = (self.config.height // 2) * (self.config.width // 2)

                        y_data = frame_data[:y_size]
                        u_data = frame_data[y_size:y_size + u_size]
                        v_data = frame_data[y_size + u_size:y_size + u_size * 2]

                        # 转换为 numpy 数组
                        y = np.frombuffer(y_data, dtype=np.uint8).reshape((self.config.height, self.config.width))
                        u = np.frombuffer(u_data, dtype=np.uint8).reshape((self.config.height // 2, self.config.width // 2))
                        v = np.frombuffer(v_data, dtype=np.uint8).reshape((self.config.height // 2, self.config.width // 2))

                        # 调试输出
                        if self.stats['frames_decoded'] < 3:
                            print(f"[VideoDecoder] YUV420p 帧 {self.stats['frames_decoded']}: Y shape={y.shape}, U shape={u.shape}, V shape={v.shape}")

                        # 将 YUV420p 转换为 NV12 格式
                        uv = self._convert_yuv420p_to_nv12(u, v)

                        # 写入环形缓冲区（NV12 格式）
                        self._write_frame_to_ring_buffer(y, uv)

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

    def _write_frame_to_ring_buffer(self, y: np.ndarray, uv: np.ndarray):
        """
        写入帧到环形缓冲区（NV12 格式）

        使用环形缓冲区 + 索引队列架构，实现零拷贝数据传输。

        Args:
            y: Y 平面 (height, width)
            uv: UV 交错平面 (height//2, width)
        """
        if not self.ring_buffer or not self.index_queue:
            return

        # 写入环形缓冲区（返回块ID）
        block_id = self.ring_buffer.write_frame_nv12(y, uv)

        # 创建帧索引并加入索引队列
        self.index_queue.enqueue_index(
            sequence_num=self.sequence_num,
            shm_block_id=block_id,
            timestamp_ns=time.time_ns()
        )

        # 更新序列号
        self.sequence_num += 1

    def _convert_yuv420p_to_nv12(self, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        """
        将 YUV420p 的 U 和 V 平面转换为 NV12 的 UV 交错平面

        Args:
            u: U 平面 (height//2, width//2)
            v: V 平面 (height//2, width//2)

        Returns:
            uv: UV 交错平面 (height//2, width)
        """
        height, width = u.shape

        # 创建 UV 交错平面
        uv = np.zeros((height, width * 2), dtype=np.uint8)

        # 交错存储 U 和 V
        uv[:, 0::2] = u  # U 在偶数列
        uv[:, 1::2] = v  # V 在奇数列

        return uv

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
        获取帧索引（从索引队列）

        Returns:
            FrameIndex: 帧索引，如果队列为空则返回None
        """
        if not self.index_queue or not self.is_running:
            return None

        return self.index_queue.dequeue_index()

    def get_frame_from_block(self, block_id: int) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """
        从环形缓冲区的指定块读取帧数据（零拷贝）

        Args:
            block_id: 块ID

        Returns:
            Tuple[np.ndarray, np.ndarray]: (Y平面, UV平面)，如果失败返回None
        """
        if not self.ring_buffer or not self.is_running:
            return None

        try:
            header, y, uv = self.ring_buffer.read_frame_nv12(block_id)
            return y, uv
        except Exception as e:
            print(f"[VideoDecoder] 读取块 {block_id} 失败：{e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """
        获取解码统计信息

        Returns:
            Dict: 包含解码器统计信息的字典
        """
        stats = self.stats.copy()

        # 添加环形缓冲区统计
        if self.ring_buffer:
            stats['ring_buffer'] = {
                'block_count': self.ring_buffer.block_count,
                'frame_count': self.ring_buffer.frame_count,
                'available_blocks': self.ring_buffer.get_available_block_count(),  # 添加可用块数
            }

        # 添加索引队列统计
        if self.index_queue:
            queue_stats = self.index_queue.get_stats()
            stats['index_queue'] = {
                'current_size': queue_stats.current_size,
                'max_size': queue_stats.max_size,
                'total_indices': queue_stats.total_indices,
            }

        return stats

    def print_stats(self):
        """打印详细统计信息"""
        print("\n" + "="*60)
        print("视频解码器统计信息（新架构）")
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

        # 打印环形缓冲区统计
        if self.ring_buffer:
            print("\n环形缓冲区:")
            print(f"  块数量：{self.ring_buffer.block_count}")
            print(f"  帧计数：{self.ring_buffer.frame_count}")

        # 打印索引队列统计
        if self.index_queue:
            print("\n索引队列:")
            queue_stats = self.index_queue.get_stats()
            print(f"  当前大小：{queue_stats.current_size}")
            print(f"  总索引数：{queue_stats.total_indices}")

        print("="*60)


def create_decoder(
    rtsp_url: str = "",
    video_file: str = "",
    width: int = 640,
    height: int = 640,
    fps: int = 30,
    hwaccel_preset: str = "",
    input_preset: str = "",
    ring_buffer_block_count: int = 10,
    index_queue_size: int = 50,
    ffmpeg_filters: List[str] = None,
    **kwargs
) -> VideoDecoder:
    """
    创建视频解码器

    推荐使用方式1（预设名称）：
        from src.utils import FFMPEG_HWACCEL_DXVA2
        decoder = create_decoder(
            rtsp_url="rtsp://...",
            hwaccel_preset=FFMPEG_HWACCEL_DXVA2,  # 推荐方式
            width=1920,
            height=1080
        )

    Args:
        rtsp_url: RTSP 流地址
        video_file: 本地视频文件路径
        width: 输出宽度
        height: 输出高度
        fps: 检测帧率（默认 30）
        hwaccel_preset: 硬件加速预设（必须使用）
            - FFMPEG_HWACCEL_DXVA2: Windows DXVA2（推荐）
            - FFMPEG_HWACCEL_NVIDIA: NVIDIA CUDA
            - FFMPEG_HWACCEL_VAAPI: AMD VAAPI
        input_preset: 输入预设（如 "preset-rtsp-generic"）
        ring_buffer_block_count: 环形缓冲区块数量（默认 10）
        index_queue_size: 索引队列大小（默认 50）
        ffmpeg_filters: FFmpeg 滤镜参数（如降噪、锐化等）
        **kwargs: 其他参数

    Returns:
        VideoDecoder: 解码器实例
    """
    config = DecoderConfig(
        rtsp_url=rtsp_url,
        video_file=video_file,
        width=width,
        height=height,
        fps=fps,
        hwaccel_preset=hwaccel_preset,
        input_preset=input_preset,
        ring_buffer_block_count=ring_buffer_block_count,
        index_queue_size=index_queue_size,
        ffmpeg_filters=ffmpeg_filters or [],
        **kwargs
    )
    return VideoDecoder(config)
