"""
视频解码器使用示例（方式1：预设名称）

展示如何使用 FFmpeg 预设系统创建视频解码器。
"""

import sys
import time
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.decoders.video_decoder import create_decoder
from src.utils import (
    FFMPEG_HWACCEL_DXVA2,
    FFMPEG_HWACCEL_NVIDIA,
    FFMPEG_HWACCEL_VAAPI,
)


def example_dxva2_decoder():
    """
    示例1：使用 DXVA2 预设（Windows 平台推荐）

    DXVA2 是 Windows 平台的硬件加速方案，支持大多数 GPU。
    """
    print("="*60)
    print("示例1：Windows DXVA2 硬件加速")
    print("="*60)

    # 方式1：使用预设名称（推荐）
    decoder = create_decoder(
        rtsp_url="rtsp://admin:password@192.168.1.100:554/stream1",
        width=1920,
        height=1080,
        hwaccel_preset=FFMPEG_HWACCEL_DXVA2,  # "preset-dxva2"
    )

    # 启动解码器
    if decoder.start():
        print("✓ 解码器启动成功")

        try:
            # 读取帧数据（零拷贝）
            frame_index = decoder.get_frame_index()
            if frame_index:
                print(f"  帧 {frame_index.sequence_num} 在块 {frame_index.shm_block_id}")

                # 从环形缓冲区读取帧数据
                y, uv = decoder.get_frame_from_block(frame_index.shm_block_id)
                print(f"  Y 平面: {y.shape}, UV 平面: {uv.shape}")

        finally:
            decoder.stop()
    else:
        print("✗ 解码器启动失败")


def example_nvidia_decoder():
    """
    示例2：使用 NVIDIA CUDA 预设

    NVIDIA GPU 使用 CUDA/NVENC 硬件加速，性能优异。
    """
    print("\n" + "="*60)
    print("示例2：NVIDIA CUDA 硬件加速")
    print("="*60)

    # 方式1：使用预设名称（推荐）
    decoder = create_decoder(
        rtsp_url="rtsp://admin:password@192.168.1.100:554/stream1",
        width=1920,
        height=1080,
        hwaccel_preset=FFMPEG_HWACCEL_NVIDIA,  # "preset-nvidia"
    )

    # 启动解码器
    if decoder.start():
        print("✓ 解码器启动成功")

        try:
            # 读取帧索引
            for i in range(10):
                frame_index = decoder.get_frame_index()
                if frame_index:
                    print(f"  帧 {frame_index.sequence_num}: 时间戳={frame_index.timestamp_ms:.2f}ms")

                    # 读取帧数据（零拷贝）
                    y, uv = decoder.get_frame_from_block(frame_index.shm_block_id)
                    print(f"    Y shape={y.shape}, UV shape={uv.shape}")

                time.sleep(0.2)

        finally:
            decoder.stop()
    else:
        print("✗ 解码器启动失败")


def example_auto_platform():
    """
    示例3：自动选择平台预设

    根据操作系统自动选择最优硬件加速方案。
    """
    print("\n" + "="*60)
    print("示例3：自动选择平台预设")
    print("="*60)

    import platform

    # 根据平台选择预设
    if platform.system() == "Windows":
        preset = FFMPEG_HWACCEL_DXVA2
        print(f"平台：Windows，使用预设：{preset}")
    elif platform.system() == "Linux":
        # 可以检查 GPU 类型选择 NVIDIA 或 VAAPI
        preset = FFMPEG_HWACCEL_NVIDIA
        print(f"平台：Linux，使用预设：{preset}")
    else:
        preset = ""
        print(f"平台：{platform.system()}，使用 CPU 解码")

    # 创建解码器
    decoder = create_decoder(
        rtsp_url="rtsp://admin:password@192.168.1.100:554/stream1",
        width=1920,
        height=1080,
        hwaccel_preset=preset,  # 自动选择的预设
    )

    # 启动解码器
    if decoder.start():
        print("✓ 解码器启动成功")

        try:
            # 打印统计信息
            time.sleep(2)
            stats = decoder.get_stats()
            print(f"\n解码统计：")
            print(f"  解码帧数：{stats['frames_decoded']}")
            print(f"  解码错误：{stats['decode_errors']}")
            print(f"  环形缓冲区块数：{stats['ring_buffer']['block_count']}")

        finally:
            decoder.stop()
    else:
        print("✗ 解码器启动失败")


def example_with_input_preset():
    """
    示例4：使用输入预设 + 硬件加速预设

    组合使用输入预设和硬件加速预设，优化 RTSP 流处理。
    """
    print("\n" + "="*60)
    print("示例4：输入预设 + 硬件加速预设")
    print("="*60)

    from src.decoders.video_decoder import DecoderConfig

    # 创建配置对象（方式1：预设名称）
    config = DecoderConfig(
        rtsp_url="rtsp://admin:password@192.168.1.100:554/stream1",
        width=1920,
        height=1080,
        fps=30,

        # 方式1：使用预设名称（推荐）
        hwaccel_preset=FFMPEG_HWACCEL_DXVA2,  # Windows 硬件加速
        input_preset="preset-rtsp-generic",    # RTSP 输入预设

        # 环形缓冲区配置
        ring_buffer_block_count=5,
        index_queue_size=30,
    )

    # 创建解码器
    decoder = VideoDecoder(config)

    # 启动解码器
    if decoder.start():
        print("✓ 解码器启动成功")
        print(f"  硬件加速：{config.hwaccel_preset}")
        print(f"  输入预设：{config.input_preset}")

        try:
            # 读取帧数据
            time.sleep(1)

            # 打印详细统计
            decoder.print_stats()

        finally:
            decoder.stop()
    else:
        print("✗ 解码器启动失败")


def main():
    """主函数"""
    print("\n" + "="*60)
    print("视频解码器使用示例（方式1：预设名称）")
    print("="*60)
    print("\n支持的硬件加速预设：")
    print(f"  1. {FFMPEG_HWACCEL_DXVA2} - Windows DXVA2（推荐）")
    print(f"  2. {FFMPEG_HWACCEL_NVIDIA} - NVIDIA CUDA")
    print(f"  3. {FFMPEG_HWACCEL_VAAPI} - AMD VAAPI")
    print("\n" + "="*60)

    # 运行示例
    # example_dxva2_decoder()        # Windows 平台
    # example_nvidia_decoder()        # NVIDIA GPU
    # example_auto_platform()         # 自动选择
    # example_with_input_preset()     # 组合预设

    print("\n提示：修改 main() 函数取消注释来运行具体示例")


if __name__ == "__main__":
    main()