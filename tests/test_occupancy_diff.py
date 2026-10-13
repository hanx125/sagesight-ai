"""差分分析单元测试（合成帧，不依赖摄像头）"""

import numpy as np
import pytest

from src.occupancy.diff_analyzer import DiffAnalyzer


@pytest.fixture
def analyzer():
    return DiffAnalyzer(diff_threshold=25)


def _solid_frame(value=100, size=(240, 320)):
    return np.full(size, value, dtype=np.uint8)


def _rect_polygon(size=(240, 320), pad=20):
    """全幅多边形（四角内缩 pad 像素）"""
    h, w = size
    return [(pad, pad), (w - pad, pad), (w - pad, h - pad), (pad, h - pad)]


class TestAreaRatio:
    def test_identical_frames_zero_ratio(self, analyzer):
        """帧与基线一致 → 差异占比为 0"""
        frame = _solid_frame(100)
        result = analyzer.analyze(frame, frame.copy(), _rect_polygon())
        assert result.area_ratio == 0.0
        assert result.bbox is None

    def test_synthetic_object_ratio(self, analyzer):
        """纯色基线 + 合成矩形物体 → area_ratio 计算正确（形态学后近似）"""
        baseline = _solid_frame(100)
        frame = baseline.copy()
        # 80x60 矩形 = 4800 像素；多边形内约 (320-40)*(240-40)=56000 像素
        frame[100:160, 120:200] = 200
        result = analyzer.analyze(frame, baseline, _rect_polygon())
        expected = 4800 / 56000
        # 形态学与归一化有少量边缘影响，放宽断言
        assert expected * 0.8 <= result.area_ratio <= expected * 1.2
        # bbox 覆盖目标区域
        assert result.bbox is not None
        bx1, by1, bx2, by2 = result.bbox
        assert bx1 <= 120 and bx2 >= 200 and by1 <= 100 and by2 >= 160


class TestNormalization:
    def test_global_brightness_offset_ignored(self, analyzer):
        """全局亮度偏移（±20）归一化后差异占比≈0"""
        baseline = _solid_frame(100)
        frame = _solid_frame(80)  # 全局变暗 20
        result = analyzer.analyze(frame, baseline, _rect_polygon())
        assert result.area_ratio < 0.01

    def test_real_object_survives_offset(self, analyzer):
        """全局偏移同时存在局部物体 → 物体仍被检出"""
        baseline = _solid_frame(100)
        frame = _solid_frame(80)
        frame[100:160, 120:200] = 200
        result = analyzer.analyze(frame, baseline, _rect_polygon())
        assert result.area_ratio > 0.02


class TestPolygonMask:
    def test_pixels_outside_polygon_ignored(self, analyzer):
        """多边形外像素不计入"""
        baseline = _solid_frame(100)
        frame = baseline.copy()
        # 物体放在画面左上角（多边形外，多边形内缩 60px）
        frame[5:25, 5:25] = 250
        small_polygon = [(60, 60), (260, 60), (260, 180), (60, 180)]
        result = analyzer.analyze(frame, baseline, small_polygon)
        assert result.area_ratio == 0.0


class TestGlobalChange:
    def test_full_frame_change_exceeds_global_ratio(self, analyzer):
        """全画面差异（如昼夜切换）→ ratio ≥ 0.6 供上层判定全局突变"""
        baseline = _solid_frame(100)
        frame = _solid_frame(200)  # 差 100，远超阈值
        result = analyzer.analyze(frame, baseline, _rect_polygon())
        assert result.area_ratio >= 0.6
