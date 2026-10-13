"""区域状态机与通道聚合单元测试（合成数据，不依赖摄像头）"""

import numpy as np
import pytest

from src.occupancy.occupancy_state import (
    ChannelOccupancyMonitor,
    ZoneOccupancyState,
    CLEAR_CONFIRM_SECONDS,
    GRACE_SECONDS,
)


def _make_state(dwell=60, min_ratio=0.02) -> ZoneOccupancyState:
    state = ZoneOccupancyState('测试区域', dwell_seconds=dwell, min_area_ratio=min_ratio)
    state.recalibrate()  # 进入 MONITORING
    return state


class TestMonitoringToAlerted:
    def test_sustained_ratio_triggers_alert(self):
        """持续超阈值达 dwell → ALERTED，仅产生一次 alert"""
        state = _make_state(dwell=60)
        t0 = 1000.0
        assert state.update(0.10, t0) is None
        assert state.update(0.10, t0 + 30) is None
        assert state.update(0.10, t0 + 59) is None
        assert state.update(0.10, t0 + 60) == 'alert'
        assert state.state == 'ALERTED'
        # 已告警期间持续有差异：不重复告警
        assert state.update(0.12, t0 + 120) is None
        assert state.state == 'ALERTED'

    def test_transient_ratio_no_alert(self):
        """短暂差异未达 dwell 即回落 → 不告警"""
        state = _make_state(dwell=60)
        t0 = 1000.0
        state.update(0.10, t0)
        assert state.update(0.0, t0 + 10) is None
        assert state.update(0.0, t0 + 30) is None
        assert state.state == 'MONITORING'


class TestGraceWindow:
    def test_short_drop_within_grace_keeps_timer(self):
        """grace 窗口内短暂跌落不重置候选计时"""
        state = _make_state(dwell=60)
        t0 = 1000.0
        state.update(0.10, t0)
        # 跌落 3s（< grace 5s）后恢复
        state.update(0.0, t0 + 10)
        state.update(0.0, t0 + 12)
        state.update(0.10, t0 + 13)
        # 累计从 t0 起算，t0+60 应触发告警
        assert state.update(0.10, t0 + 60) == 'alert'

    def test_drop_beyond_grace_resets_timer(self):
        """跌落超过 grace → 计时重置，重新累计"""
        state = _make_state(dwell=60)
        t0 = 1000.0
        state.update(0.10, t0)
        state.update(0.0, t0 + 10)
        state.update(0.0, t0 + 16)  # 超过 grace 5s
        state.update(0.10, t0 + 20)
        # 从 t0+20 重新累计，t0+60 不足
        assert state.update(0.10, t0 + 60) is None
        assert state.update(0.10, t0 + 80) == 'alert'


class TestAlertedRecovery:
    def test_clear_confirmation_triggers_recovery(self):
        """ALERTED 后差异持续低于阈值 clear_confirm 秒 → recovery"""
        state = _make_state(dwell=60)
        t0 = 1000.0
        state.update(0.10, t0)
        state.update(0.10, t0 + 60)
        assert state.state == 'ALERTED'
        # t0+89 首次跌破，恢复计时开始；29s 未达条件
        assert state.update(0.0, t0 + 89) is None
        assert state.update(0.0, t0 + 118) is None
        # 持续清空 30s → 恢复
        assert state.update(0.0, t0 + 119) == 'recovery'
        assert state.state == 'MONITORING'

    def test_partial_residue_stays_alerted(self):
        """仍有残留差异（ratio >= min）→ 保持 ALERTED 不恢复"""
        state = _make_state(dwell=60)
        t0 = 1000.0
        state.update(0.10, t0)
        state.update(0.10, t0 + 60)
        state.update(0.05, t0 + 200)  # 残留
        assert state.update(0.05, t0 + 240) is None
        assert state.state == 'ALERTED'


class TestGlobalChange:
    def test_invalidate_from_monitoring_once(self):
        """MONITORING 遇全局突变 → INVALID，仅提示一次"""
        state = _make_state()
        t0 = 1000.0
        assert state.invalidate(t0) == 'global_change'
        assert state.state == 'INVALID'
        # INVALID 期间 update 不判定
        assert state.update(0.5, t0 + 1) is None
        # 重复 invalidate 不再提示
        assert state.invalidate(t0 + 2) is None

    def test_recalibrate_returns_to_monitoring(self):
        """重拍基线后 INVALID → MONITORING"""
        state = _make_state()
        state.invalidate(1000.0)
        state.recalibrate()
        assert state.state == 'MONITORING'
        assert state.update(0.10, 1001.0) is None


class TestChannelMonitor:
    FRAME_SHAPE = (240, 320)

    @staticmethod
    def _zones_config():
        return [
            {
                'name': '堆积区',
                'polygon': [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]],
                'enable_pileup': True,
                'enable_occupancy': False,
            },
            {
                'name': '占用区',
                'polygon': [[0.5, 0.5], [0.9, 0.5], [0.9, 0.9], [0.5, 0.9]],
                'enable_pileup': False,
                'enable_occupancy': True,
            },
            {
                'name': '未启用区',
                'polygon': [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1]],
            },
        ]

    def test_type_filter(self):
        """分类型区域筛选：pile_up 任务只处理 enable_pileup 区域"""
        monitor = ChannelOccupancyMonitor(
            1, 'pile_up', self._zones_config(), self.FRAME_SHAPE
        )
        assert [z['name'] for z in monitor.zones] == ['堆积区']

        monitor2 = ChannelOccupancyMonitor(
            1, 'abnormal_occupancy', self._zones_config(), self.FRAME_SHAPE
        )
        assert [z['name'] for z in monitor2.zones] == ['占用区']

    def test_no_baseline_returns_empty(self):
        """无基线 → 不产出任何消息（UNCALIBRATED 不告警）"""
        monitor = ChannelOccupancyMonitor(
            1, 'pile_up', self._zones_config(), self.FRAME_SHAPE
        )
        frame = np.full(self.FRAME_SHAPE, 100, dtype=np.uint8)
        assert monitor.process_frame(frame, 1000.0) == []
        assert monitor.status_summary()['state'] == 'uncalibrated'

    def test_alert_message_contains_bbox_and_dwell(self):
        """注入基线后放置物体 → alert 消息带 diff_bbox 与 dwell_seconds"""
        monitor = ChannelOccupancyMonitor(
            1, 'pile_up', self._zones_config(), self.FRAME_SHAPE
        )
        baseline = np.full(self.FRAME_SHAPE, 100, dtype=np.uint8)
        monitor.handle_baseline_captured(baseline, 1000.0)
        assert monitor.status_summary()['state'] == 'monitoring'

        # 大物体覆盖堆积区大半 → ratio 超过全局突变线会走 INVALID，
        # 用中等物体（占比约 0.2）确保走正常候选路径
        frame = baseline.copy()
        frame[30:90, 40:160] = 250  # 7200px，堆积区约 9600px，ratio≈0.75 超限
        # 缩小物体: 60x40 = 2400px / 9600px ≈ 0.25
        frame[30:90, 40:160] = 100
        frame[40:80, 50:90] = 250
        t0 = 1000.0
        # dwell 默认 300s，推进时间触发
        messages = []
        for offset in (0, 100, 200, 299, 300):
            messages.extend(monitor.process_frame(frame, t0 + offset))
        alerts = [m for m in messages if m['event'] == 'alert']
        assert len(alerts) == 1
        assert alerts[0]['zone_name'] == '堆积区'
        assert alerts[0]['dwell_seconds'] == 300
        assert alerts[0]['diff_bbox'] is not None
        assert alerts[0]['area_ratio'] > 0.02

    def test_global_change_message(self):
        """全画面突变 → 通道级 global_change 消息，区域转 INVALID"""
        monitor = ChannelOccupancyMonitor(
            1, 'pile_up', self._zones_config(), self.FRAME_SHAPE
        )
        baseline = np.full(self.FRAME_SHAPE, 100, dtype=np.uint8)
        monitor.handle_baseline_captured(baseline, 1000.0)
        frame = np.full(self.FRAME_SHAPE, 220, dtype=np.uint8)
        messages = monitor.process_frame(frame, 1001.0)
        assert any(m['event'] == 'global_change' for m in messages)
        assert monitor.status_summary()['state'] == 'invalid'
        # 持续突变不重复提示
        assert monitor.process_frame(frame, 1002.0) == []

    def test_recovery_after_clear(self):
        """告警后清空 clear_confirm 秒 → recovery 消息"""
        monitor = ChannelOccupancyMonitor(
            1, 'abnormal_occupancy', self._zones_config(), self.FRAME_SHAPE
        )
        baseline = np.full(self.FRAME_SHAPE, 100, dtype=np.uint8)
        monitor.handle_baseline_captured(baseline, 1000.0)

        # 占用区放物体（中等大小，避免全局突变）
        occupied = baseline.copy()
        occupied[140:180, 170:250] = 250  # 3200px / 占用区约 9600px ≈ 0.33
        t0 = 1000.0
        # 默认 dwell 60s
        messages = []
        for offset in (0, 30, 60):
            messages.extend(monitor.process_frame(occupied, t0 + offset))
        assert any(m['event'] == 'alert' for m in messages)

        # 清空场景，推进 clear_confirm
        messages = []
        for offset in (61, 70, 91):
            messages.extend(monitor.process_frame(baseline, t0 + offset))
        assert any(m['event'] == 'recovery' for m in messages)
        assert monitor.status_summary()['state'] == 'monitoring'

    def test_no_enabled_zones(self):
        """无启用区域 → 空转不崩溃"""
        monitor = ChannelOccupancyMonitor(1, 'pile_up', [], self.FRAME_SHAPE)
        assert monitor.zones == []
        frame = np.full(self.FRAME_SHAPE, 100, dtype=np.uint8)
        assert monitor.process_frame(frame, 1000.0) == []
