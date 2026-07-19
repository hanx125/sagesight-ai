"""
解码器配置参数分析和优化建议
"""

# ============================================================================
# 当前配置参数分析
# ============================================================================

CURRENT_CONFIG = """
当前配置类（DecoderConfig）：

@dataclass
class DecoderConfig:
    """解码器配置（新架构）"""
    
    # ========== 输入源配置 ==========
    rtsp_url: str = ""                  # RTSP 流地址
    video_file: str = ""                # 视频文件路径
    input_preset: str = ""              # 输入预设
    
    # ========== 视频参数 ==========
    width: int = 640                    # 输出宽度
    height: int = 640                   # 输出高度
    fps: int = 5                        # 检测帧率 ⚠️ 默认值可能需要调整
    
    # ========== 硬件加速配置 ==========
    hwaccel_preset: str = ""            # 预设方式（推荐）
    hwaccel: str = "auto"               # 旧版参数（兼容）
    
    # ========== 输出配置 ==========
    output_format: str = "auto"         # 输出格式
    
    # ========== RTSP 配置 ==========
    rtsp_transport: str = "tcp"         # RTSP 传输协议
    reconnect: bool = True              # 是否重连
    reconnect_delay: int = 5            # 重连延迟（秒）
    buffer_size: int = 1024 * 1024      # 缓冲区大小（1MB）
    
    # ========== 环形缓冲区配置 ==========
    ring_buffer_block_count: int = 5    # 块数量
    index_queue_size: int = 30          # 索引队列大小
    
    # ========== 检测参数 ==========
    detect_args: List[str] = None       # 检测器参数
"""

# ============================================================================
# 优化建议
# ============================================================================

OPTIMIZATION_SUGGESTIONS = """
================================================================================
解码器配置参数优化建议
================================================================================

根据实际使用场景，建议优化以下参数：

================================================================================
1. fps 参数优化（优先级：高）
================================================================================

当前值：fps: int = 5
问题：默认值太低，不适合实时视频流

建议值：fps: int = 30

理由：
- 30 FPS 是视频流的常见帧率
- 5 FPS 太低，会导致大量帧丢失
- 如果需要降低帧率，用户可以手动指定

示例代码：
    config = DecoderConfig(
        rtsp_url="rtsp://...",
        fps=30,  # 推荐 30
        width=640,
        height=640,
    )

================================================================================
2. hwaccel_preset 参数优化（优先级：高）
================================================================================

当前值：hwaccel_preset: str = ""
问题：默认值为空，没有使用硬件加速

建议值：根据平台自动选择
- Windows: hwaccel_preset = "preset-dxva2"
- NVIDIA GPU: hwaccel_preset = "preset-nvidia"
- Intel GPU: hwaccel_preset = "preset-intel-qsv-h264"

理由：
- 硬件加速应该默认启用
- 根据平台自动选择最优方案
- 用户可以手动覆盖

实现方式：
    import platform
    
    def get_default_hwaccel_preset():
        if platform.system() == "Windows":
            return "preset-dxva2"
        else:
            return ""

示例代码：
    config = DecoderConfig(
        rtsp_url="rtsp://...",
        hwaccel_preset="preset-dxva2",  # 推荐：默认启用
        width=640,
        height=640,
    )

================================================================================
3. input_preset 参数优化（优先级：中）
================================================================================

当前值：input_preset: str = ""
问题：默认值为空，使用默认 RTSP 配置

建议：
- 对于 RTSP 流：input_preset = "preset-rtsp-generic"
- 对于视频文件：保持为空

理由：
- RTSP 流需要特殊配置（超时、传输协议等）
- 使用预设可以简化配置

示例代码：
    config = DecoderConfig(
        rtsp_url="rtsp://...",
        input_preset="preset-rtsp-generic",  # 推荐
        hwaccel_preset="preset-dxva2",
        width=640,
        height=640,
    )

================================================================================
4. ring_buffer_block_count 参数优化（优先级：低）
================================================================================

当前值：ring_buffer_block_count: int = 5
建议值：ring_buffer_block_count: int = 10

理由：
- 5 块可能不够，如果检测器处理速度慢
- 10 块可以缓存更多帧，避免丢帧
- 内存占用增加不大（每块约 0.6MB，总共 6MB）

示例代码：
    config = DecoderConfig(
        rtsp_url="rtsp://...",
        ring_buffer_block_count=10,  # 推荐 10
        index_queue_size=50,         # 对应增加到 50
    )

================================================================================
5. buffer_size 参数优化（优先级：低）
================================================================================

当前值：buffer_size: int = 1024 * 1024  # 1MB
建议值：buffer_size: int = 2 * 1024 * 1024  # 2MB

理由：
- 1MB 对于高码率视频可能不够
- 2MB 更安全，避免缓冲区溢出
- 内存占用增加不大

================================================================================
"""

# ============================================================================
# 推荐的配置类
# ============================================================================

RECOMMENDED_CONFIG = """
================================================================================
推荐的配置类（优化后）
================================================================================

@dataclass
class DecoderConfig:
    \"\"\"解码器配置（新架构，优化版）\"\"\"
    
    # ========== 输入源配置 ==========
    rtsp_url: str = ""                  # RTSP 流地址
    video_file: str = ""                # 视频文件路径
    input_preset: str = "preset-rtsp-generic" if rtsp_url else ""  # 输入预设（自动选择）
    
    # ========== 视频参数 ==========
    width: int = 640                    # 输出宽度
    height: int = 640                   # 输出高度
    fps: int = 30                       # 检测帧率（推荐 30）
    
    # ========== 硬件加速配置 ==========
    hwaccel_preset: str = "preset-dxva2"  # 默认启用硬件加速（Windows）
    hwaccel: str = "auto"               # 旧版参数（兼容）
    
    # ========== 输出配置 ==========
    output_format: str = "auto"         # 输出格式
    
    # ========== RTSP 配置 ==========
    rtsp_transport: str = "tcp"         # RTSP 传输协议
    reconnect: bool = True              # 是否重连
    reconnect_delay: int = 5            # 重连延迟（秒）
    buffer_size: int = 2 * 1024 * 1024  # 缓冲区大小（2MB）
    
    # ========== 环形缓冲区配置 ==========
    ring_buffer_block_count: int = 10   # 块数量（推荐 10）
    index_queue_size: int = 50          # 索引队列大小（推荐 50）
    
    # ========== 检测参数 ==========
    detect_args: List[str] = None       # 检测器参数
    
    def __post_init__(self):
        \"\"\"初始化后处理\"\"\"
        if self.detect_args is None:
            self.detect_args = []
        
        # 自动选择输入预设
        if not self.input_preset and self.rtsp_url:
            self.input_preset = "preset-rtsp-generic"
        
        # 自动选择硬件加速预设（如果未指定）
        if not self.hwaccel_preset and not self.hwaccel:
            import platform
            if platform.system() == "Windows":
                self.hwaccel_preset = "preset-dxva2"

================================================================================
"""

# ============================================================================
# 使用示例
# ============================================================================

USAGE_EXAMPLES = """
================================================================================
使用示例（优化后）
================================================================================

示例1：RTSP 流解码（默认配置）
----------------------------------------
from src.decoders.video_decoder import create_decoder

# 使用默认配置（自动启用硬件加速）
decoder = create_decoder(
    rtsp_url="rtsp://admin:password@192.168.1.100:554/stream1",
    width=1920,
    height=1080
)

# 实际使用的配置：
# - fps: 30（默认）
# - hwaccel_preset: "preset-dxva2"（自动选择）
# - input_preset: "preset-rtsp-generic"（自动选择）
# - ring_buffer_block_count: 10（默认）

--------------------------------------------------------------------------------

示例2：视频文件解码（自定义配置）
----------------------------------------
from src.decoders.video_decoder import DecoderConfig, VideoDecoder

config = DecoderConfig(
    video_file="d:\\sagesight-ai\\打电话.mp4",
    width=640,
    height=640,
    fps=30,
    hwaccel_preset="preset-dxva2",  # Windows 硬件加速
    ring_buffer_block_count=5,       # 减少内存占用
)

decoder = VideoDecoder(config)
decoder.start()

--------------------------------------------------------------------------------

示例3：高性能配置（NVIDIA GPU）
----------------------------------------
from src.utils import FFMPEG_HWACCEL_NVIDIA

config = DecoderConfig(
    rtsp_url="rtsp://...",
    width=1920,
    height=1080,
    fps=30,
    hwaccel_preset=FFMPEG_HWACCEL_NVIDIA,  # NVIDIA CUDA
    ring_buffer_block_count=20,             # 更多缓存
    index_queue_size=100,
)

================================================================================
"""

def print_analysis():
    """打印配置分析"""
    print(CURRENT_CONFIG)
    print(OPTIMIZATION_SUGGESTIONS)
    print(RECOMMENDED_CONFIG)
    print(USAGE_EXAMPLES)


if __name__ == "__main__":
    print_analysis()