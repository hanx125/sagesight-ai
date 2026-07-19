"""
视频处理工具

提供视频文件相关的工具函数
"""

import subprocess
import json
from typing import Tuple, Optional
from pathlib import Path


def get_video_file_info(video_file: str) -> Optional[dict]:
    """
    获取视频文件的基本信息（原始分辨率、帧率、时长等）

    Args:
        video_file: 视频文件路径

    Returns:
        dict: 视频信息字典，包含：
            - width: 原始宽度
            - height: 原始高度
            - fps: 帧率
            - duration: 时长（秒）
            - codec: 编码格式
        None: 如果获取失败
    """
    if not Path(video_file).exists():
        print(f"[VideoUtils] 视频文件不存在: {video_file}")
        return None

    try:
        # 使用 ffprobe 获取视频信息
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            video_file
        ]

        # 使用二进制模式读取，避免 subprocess 内部线程的编码问题
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )

        if result.returncode != 0:
            stderr_text = result.stderr.decode('utf-8', errors='ignore')
            print(f"[VideoUtils] ffprobe 失败: {stderr_text}")
            return None

        # 手动解码 JSON 输出（忽略无法解码的字符）
        stdout_text = result.stdout.decode('utf-8', errors='ignore')

        # 解析 JSON 输出
        probe_data = json.loads(stdout_text)

        # 查找视频流
        video_stream = None
        for stream in probe_data.get('streams', []):
            if stream.get('codec_type') == 'video':
                video_stream = stream
                break

        if not video_stream:
            print(f"[VideoUtils] 未找到视频流: {video_file}")
            return None

        # 提取视频信息
        width = video_stream.get('width', 0)
        height = video_stream.get('height', 0)

        # 解析帧率（格式可能是 "30/1" 或 "29.97"）
        fps_str = video_stream.get('r_frame_rate', '0/1')
        if '/' in fps_str:
            num, den = fps_str.split('/')
            fps = float(num) / float(den) if float(den) != 0 else 0
        else:
            fps = float(fps_str)

        # 时长
        duration = float(probe_data.get('format', {}).get('duration', 0))

        # 编码格式
        codec = video_stream.get('codec_name', 'unknown')

        return {
            'width': width,
            'height': height,
            'fps': fps,
            'duration': duration,
            'codec': codec
        }

    except subprocess.TimeoutExpired:
        print(f"[VideoUtils] ffprobe 超时: {video_file}")
        return None
    except json.JSONDecodeError as e:
        print(f"[VideoUtils] JSON 解析失败: {e}")
        return None
    except UnicodeDecodeError as e:
        print(f"[VideoUtils] 编码错误: {e}")
        return None
    except Exception as e:
        print(f"[VideoUtils] 获取视频信息失败: {e}")
        return None


def get_video_original_resolution(video_file: str) -> Tuple[int, int]:
    """
    获取视频文件的原始分辨率

    Args:
        video_file: 视频文件路径

    Returns:
        Tuple[int, int]: (width, height) 原始分辨率
        Tuple[int, int]: (0, 0) 如果获取失败
    """
    info = get_video_file_info(video_file)

    if info:
        return (info['width'], info['height'])
    else:
        return (0, 0)


if __name__ == "__main__":
    # 测试代码
    import sys

    if len(sys.argv) < 2:
        print("用法: python video_utils.py <视频文件路径>")
        sys.exit(1)

    video_file = sys.argv[1]
    info = get_video_file_info(video_file)

    if info:
        print(f"\n视频文件: {video_file}")
        print(f"原始分辨率: {info['width']}x{info['height']}")
        print(f"帧率: {info['fps']:.2f} fps")
        print(f"时长: {info['duration']:.2f} 秒")
        print(f"编码格式: {info['codec']}")
    else:
        print(f"获取视频信息失败: {video_file}")