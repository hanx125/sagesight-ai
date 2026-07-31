"""
中文字体绘制工具

提供基于 PIL 的 UTF-8 文本绘制功能，解决 OpenCV cv2.putText 不支持中文的问题。
自动查找 Windows/Linux/macOS 系统的中文字体，无需外网下载。
"""
import os
import logging
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def _collect_font_candidates() -> List[Path]:
    """收集系统中可用的中文字体候选路径"""
    cands: List[Path] = []

    # 环境变量指定字体路径
    env = os.environ.get("SAGESIGHT_FONT_PATH")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            cands.append(p)

    # Windows 系统字体
    win = os.environ.get("WINDIR", r"C:\Windows")
    cands.extend([
        Path(win) / "Fonts" / "msyh.ttc",       # 微软雅黑
        Path(win) / "Fonts" / "msyhbd.ttc",      # 微软雅黑粗体
        Path(win) / "Fonts" / "simhei.ttf",      # 黑体
        Path(win) / "Fonts" / "simsun.ttc",      # 宋体
        Path(win) / "Fonts" / "msjh.ttc",        # 微软准黑
        Path(win) / "Fonts" / "arialuni.ttf",    # Arial Unicode
    ])

    # Linux 系统字体
    cands.extend([
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    ])

    # macOS 系统字体
    cands.extend([
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ])

    return cands


@lru_cache(maxsize=1)
def _find_chinese_font() -> Optional[Path]:
    """查找系统可用的中文字体文件，结果缓存"""
    for cand in _collect_font_candidates():
        try:
            if cand.is_file() and cand.stat().st_size > 10000:
                logger.debug(f"找到中文字体: {cand}")
                return cand
        except OSError:
            continue
    logger.warning("未找到可用的中文字体，检测框文字将退回英文渲染（cv2.putText）")
    return None


@lru_cache(maxsize=8)
def _get_font(size: int = 14):
    """加载指定大小的中文字体，返回 PIL ImageFont 对象"""
    from PIL import ImageFont

    font_path = _find_chinese_font()
    if font_path is None:
        return None
    try:
        return ImageFont.truetype(str(font_path), size)
    except OSError as e:
        logger.warning(f"加载字体失败 {font_path}: {e}")
        return None


def _text_size(font, text: str) -> Tuple[int, int]:
    """获取文本的宽度和高度"""
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_utf8_label_on_rgb(
    img,
    text: str,
    org_xy: Tuple[int, int],
    font_size: int = 14,
    text_color_rgb: Tuple[int, int, int] = (255, 255, 255),
) -> bool:
    """
    在 RGB 图像上绘制 UTF-8 文本（支持中文）。

    使用 PIL 渲染文字，绘制到图像的指定位置。成功返回 True，失败返回 False
    （调用方可退回 cv2.putText）。

    Args:
        img: RGB 图像 (numpy array, shape=[H,W,3])
        text: 要绘制的文本（支持中文）
        org_xy: 文字左上角坐标 (x, y)
        font_size: 字体大小（像素），默认 14
        text_color_rgb: 文字颜色 (R, G, B)，默认白色

    Returns:
        True 表示绘制成功，False 表示失败（字体未找到等）
    """
    import numpy as np
    from PIL import Image, ImageDraw

    font = _get_font(font_size)
    if font is None:
        return False

    h, w = img.shape[:2]
    tw, th = _text_size(font, text)
    lx, ly = int(org_xy[0]), int(org_xy[1])
    pad = 2

    # 计算文本区域，裁剪到图像边界内
    x1, y1 = lx - pad, ly - pad
    x2, y2 = lx + tw + pad, ly + th + pad
    x1c, y1c = max(0, x1), max(0, y1)
    x2c, y2c = min(w, x2), min(h, y2)
    if x2c <= x1c or y2c <= y1c:
        return False

    # 在 ROI 上用 PIL 绘制文字（RGB 图像无需通道转换）
    roi = img[y1c:y2c, x1c:x2c]
    pil_im = Image.fromarray(roi)
    draw = ImageDraw.Draw(pil_im)
    tx, ty = lx - x1c, ly - y1c
    draw.text((tx, ty), text, font=font, fill=text_color_rgb)
    img[y1c:y2c, x1c:x2c] = np.asarray(pil_im)
    return True
