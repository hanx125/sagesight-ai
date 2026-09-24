"""
人员统计 - 计数区域配置结构

CountingZone 描述一个被独立计数的区域（任务级），支持两种计数模式：
  - line      （模式A）单摄像机画分界线，跟踪目标跨越方向判断进出
  - direction （模式B）多摄像机按进出方向汇总（入口机+1 / 出口机-1）

与通道级 zones_config（多边形进出/徘徊）物理隔离，互不影响。
"""
import json
from dataclasses import dataclass, field
from typing import List, Optional

# 计数模式
MODE_LINE = 'line'
MODE_DIRECTION = 'direction'
VALID_MODES = (MODE_LINE, MODE_DIRECTION)

# 在 direction 模式下，本相机在当前区域内承担的角色
ROLE_LINE = 'line'      # 本相机即画线区域相机
ROLE_IN = 'in'          # 入口机：检测到人 +1
ROLE_OUT = 'out'        # 出口机：检测到人 -1


def _load_points(value: Optional[str]) -> Optional[List]:
    """解析 cross_line 归一化两点 JSON（兼容已反序列化的 list）"""
    if value is None:
        return None
    if isinstance(value, list):
        if len(value) == 2:
            return value
        return None
    try:
        data = json.loads(value)
        if isinstance(data, list) and len(data) == 2:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _load_id_list(value: Optional[str]) -> List[int]:
    """解析 in_channels / out_channels 通道ID列表 JSON"""
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [int(x) for x in data]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


@dataclass
class CountingZone:
    """人员统计计数区域配置（运行时结构）"""
    zone_name: str
    mode: str = MODE_LINE
    channel_id: Optional[int] = None        # 仅 mode='line'：分界线相机通道ID
    role: str = ROLE_LINE                    # 本相机在该区域的角色（line/in/out）
    task_id: Optional[int] = None

    # mode='line' 参数
    cross_line: Optional[List] = None        # 归一化两点 [[x1,y1],[x2,y2]]，范围[0-1]
    in_side: int = 1                         # 分界线哪侧为"进"（+1/-1，直线法向量侧）
    inertia: int = 3                         # 连续N帧确认，防抖动
    min_displacement: float = 0.02           # 最小归一化位移阈值，防横跳

    # mode='direction' 参数
    in_channels: List[int] = field(default_factory=list)    # 入口机通道ID列表
    out_channels: List[int] = field(default_factory=list)   # 出口机通道ID列表

    @classmethod
    def from_row(cls, row, role: str = ROLE_LINE) -> 'CountingZone':
        """从 counting_zones 表行构造；role 标识本相机在该区域承担的角色"""
        return cls(
            task_id=row.get('task_id'),
            zone_name=row.get('zone_name', ''),
            mode=row.get('mode', MODE_LINE),
            channel_id=row.get('channel_id'),
            role=row.get('role', role),
            cross_line=_load_points(row.get('cross_line')),
            in_side=int(row.get('in_side', 1)),
            inertia=int(row.get('inertia', 3)),
            min_displacement=float(row.get('min_displacement', 0.02)),
            in_channels=_load_id_list(row.get('in_channels')),
            out_channels=_load_id_list(row.get('out_channels')),
        )

    def is_line(self) -> bool:
        return self.mode == MODE_LINE

    def is_direction(self) -> bool:
        return self.mode == MODE_DIRECTION