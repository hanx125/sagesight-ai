#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PTZ 自动跟踪闭环模拟器（无需真实云台摄像机）

解决的问题
----------
自动跟拍功能依赖真实 PTZ 设备才能验证，现场没有设备就只能靠猜。本工具用
"虚拟云台 + 世界几何投影"完整复现闭环，且调用的是**生产代码本身**：

    NorfairTracker.match_and_update   ← 真实单相机跟踪（含 PTZ 期间放宽阈值）
    PTZMetrics                        ← 真实跨进程共享状态
    PTZAutoTrackService.tick_once     ← 真实控制律（死区/限速/超时/安全阀）

只有两处是假的，且都是边界：
    SimPanTilt     假的云台设备（实现 move_velocity / stop_move / is_moving）
    DetectionSource 假的检测器（由世界投影算出目标框，可加抖动与丢帧）

仿真的物理链路
--------------
    世界角度 alpha(t)  →  相机角度 theta  →  归一化画面坐标 x = 0.5 + (alpha-theta)/fov
    控制律下发速度     →  相机角度积分     →  画面里的目标因此移动

也就是说：云台"转"是通过改变裁切窗口实现的，目标在画面里真的会动，
Norfair 真的会因此匹配失败或成功——这正是要验证的东西。

用法
----
    python tools/ptz_sim.py                       # 默认场景，输出视频
    python tools/ptz_sim.py --scenario cross      # 匀速横穿
    python tools/ptz_sim.py --no-ptz              # 对照组：关掉自动跟踪
    python tools/ptz_sim.py --device-invert-pan   # 复现"方向反了"，验证校准流程
    python tools/ptz_sim.py --no-video            # 只跑数值，不渲染（CI 用）

输出
----
    output/ptz_sim_<scenario>.mp4   可视化回放（含跟踪框、死区、状态 HUD）
    控制台报告                        ID 断裂次数 / 居中误差 / 转动占比
"""

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2  # noqa: E402

FRAME_W, FRAME_H = 1280, 720
FOV_DEG = 58.0                 # 相机水平视场角（度）
PAN_LIMIT = 35.0               # 云台机械行程（度）
TARGET_H_NORM = 0.40           # 目标框高度（归一化）


# ========================================
# 世界：目标轨迹
# ========================================

_SCENARIOS = {
    'walk': ('目标走-停-回走（最接近实际场景）', 14.0),
    'cross': ('目标匀速横穿画面', 12.0),
    'zigzag': ('目标来回摆动（考验死区与限速）', 16.0),
    'exit': ('目标走出云台行程（考验丢失停转）', 14.0),
}

# 纵向视场角：按 16:9 由水平视场角折算
FOV_V_DEG = FOV_DEG * FRAME_H / FRAME_W


def target_angle(scenario: str, t: float, speed: float) -> float:
    """
    目标在世界坐标系中的水平方位角（度）

    所有场景都限制在 ±30 度内（exit 除外），保证云台机械行程够用，
    否则测的就不是跟踪而是行程限位。
    """
    if scenario == 'cross':
        return -26.0 + (52.0 / 12.0) * t
    if scenario == 'zigzag':
        return 20.0 * math.sin(0.5 * t)
    if scenario == 'exit':
        # 一路向右，越过"云台行程 + 半视场角"后必然出画，验证丢失停转
        return -20.0 + 7.0 * t
    # walk：向右走 4s → 停 2s → 向左回走 4s → 停 2s → 再向右
    leg = speed * 4.0
    if t < 4.0:
        return -20.0 + speed * t
    if t < 6.0:
        return -20.0 + leg
    if t < 10.0:
        return -20.0 + leg - speed * (t - 6.0)
    if t < 12.0:
        return -20.0
    return -20.0 + speed * (t - 12.0)


def target_elev(t: float) -> float:
    """
    目标在世界坐标系中的俯仰角（度）

    真实场景里人不会上下飘，但目标远近变化会让俯仰角缓慢变化
    （走上下坡、或相机并非水平安装）。这里给一个缓慢摆动，
    否则 tilt 轴永远不会被激励，tilt_sign 也就无法校准。
    """
    return 4.0 * math.sin(0.22 * t)



# ========================================
# 假云台设备
# ========================================

class SimPanTilt:
    """
    假云台：只接受速度指令并积分成角度

    刻意实现得"很笨"——它不懂跟踪，只按厂商约定解释速度符号。
    invert_pan=True 可复现"ONVIF 正负定义与预期相反"的现场问题，
    用来验证 config/ptz_autotrack.yaml 里 pan_sign 的校准流程。
    """

    def __init__(
        self,
        max_speed_deg: float = 40.0,
        pan_limit: float = PAN_LIMIT,
        tilt_limit: float = 20.0,
        invert_pan: bool = False,
        invert_tilt: bool = False,
        dead_zone_pct: float = 0.03,
    ):
        self.max_speed_deg = max_speed_deg
        self.pan_limit = pan_limit
        self.tilt_limit = tilt_limit
        self.invert_pan = -1.0 if invert_pan else 1.0
        self.invert_tilt = -1.0 if invert_tilt else 1.0

        self.pan = 0.0
        self.tilt = 0.0
        self.pan_vel = 0.0
        self.tilt_vel = 0.0
        self.moving = False
        self.commands = 0
        self.total_move_time = 0.0
        self._moving_since: Optional[float] = None
        self._now = 0.0

    # ---- 与 PTZController 对齐的接口 ----
    def move_velocity(self, pan: float, tilt: float, zoom: float = 0.0) -> bool:
        self.pan_vel = max(-1.0, min(1.0, pan)) * self.max_speed_deg * self.invert_pan
        self.tilt_vel = max(-1.0, min(1.0, tilt)) * self.max_speed_deg * self.invert_tilt
        self.commands += 1
        if not self.moving:
            self.moving = True
            self._moving_since = self._now
        return True

    def stop_move(self) -> bool:
        self.pan_vel = 0.0
        self.tilt_vel = 0.0
        if self.moving and self._moving_since is not None:
            self.total_move_time += self._now - self._moving_since
        self.moving = False
        self._moving_since = None
        return True

    @property
    def is_moving(self) -> bool:
        return self.moving

    # ---- 仿真推进 ----
    def step(self, dt: float, now: float) -> None:
        self._now = now
        self.pan = max(-self.pan_limit,
                       min(self.pan_limit, self.pan + self.pan_vel * dt))
        self.tilt = max(-self.tilt_limit,
                        min(self.tilt_limit, self.tilt + self.tilt_vel * dt))


# ========================================
# 假检测器
# ========================================

class DetectionSource:
    """
    由世界投影生成检测框

    jitter_px      检测框抖动（真实检测器不会逐帧完全稳定）
    drop_moving    云台转动时的丢帧概率——运动模糊会让检测真的断档，
                   这是 lost_timeout 取值的现实依据，必须模拟。
    """

    def __init__(self, jitter_px: float = 2.0, drop_moving: float = 0.08,
                 drop_still: float = 0.01, seed: int = 7):
        self.jitter_px = jitter_px
        self.drop_moving = drop_moving
        self.drop_still = drop_still
        self.rng = np.random.default_rng(seed)

    def step(
        self,
        x_norm: float,
        cy_norm: float,
        h_norm: float,
        frame_w: int,
        frame_h: int,
        moving: bool,
        label: str = 'person',
    ) -> Optional[list]:
        p = self.drop_moving if moving else self.drop_still
        if self.rng.random() < p:
            return None

        cx = x_norm * frame_w
        cy = cy_norm * frame_h
        h_px = h_norm * frame_h
        w_px = h_px * 0.42
        j = self.jitter_px
        x1 = cx - w_px / 2 + self.rng.uniform(-j, j)
        x2 = cx + w_px / 2 + self.rng.uniform(-j, j)
        y1 = cy - h_px / 2 + self.rng.uniform(-j, j)
        y2 = cy + h_px / 2 + self.rng.uniform(-j, j)
        # 目标部分出画时，框仍可能被检测到（截断到画面内）
        x1 = max(0.0, min(frame_w - 1.0, x1))
        x2 = max(0.0, min(frame_w - 1.0, x2))
        y1 = max(0.0, min(frame_h - 1.0, y1))
        y2 = max(0.0, min(frame_h - 1.0, y2))
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        box = [int(x1), int(y1), int(x2), int(y2)]
        area = (x2 - x1) * (y2 - y1)
        ratio = (x2 - x1) / max(1.0, (y2 - y1))
        return (label, 0.92, box, area, ratio, box)


# ========================================
# 渲染
# ========================================

def _px_per_deg() -> float:
    return FRAME_W / FOV_DEG


def render(
    theta: float,
    alpha: float,
    targets: List[Tuple[float, float]],
    tracked_box: Optional[List[int]],
    hud: List[str],
    deadzone_x: float,
    deadzone_y: float,
    moving: bool,
) -> np.ndarray:
    img = np.full((FRAME_H, FRAME_W, 3), 28, np.uint8)
    ppd = _px_per_deg()
    horizon = int(FRAME_H * 0.70)

    # 天空渐变
    for y in range(0, horizon, 4):
        shade = int(46 + 34 * (y / horizon))
        img[y:y + 4, :] = (shade + 18, shade + 10, shade)

    # 世界锚点：每 10 度一根立柱，相机转动时整幅背景平移，肉眼可辨
    for a in range(-80, 81, 10):
        sx = int(FRAME_W / 2 + (a - theta) * ppd)
        if -40 <= sx <= FRAME_W + 40:
            color = (70, 78, 88) if (a // 10) % 2 == 0 else (52, 60, 70)
            img[horizon - 150:horizon, max(0, sx - 3):max(0, sx + 3)] = color
            if 0 <= sx < FRAME_W:
                # 刻度文字用 ASCII，避免字体缺中文
                cv2.putText(img, f"{a}", (sx + 5, horizon - 158),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (120, 130, 140), 1)

    img[horizon:, :] = (34, 40, 46)

    # 目标（世界投影到画面）
    for (tn_x, tn_y) in targets:
        cx = int(tn_x * FRAME_W)
        cy = int(tn_y * FRAME_H)
        h_px = int(TARGET_H_NORM * FRAME_H)
        w_px = int(h_px * 0.42)
        top = cy - h_px // 2
        cv2.rectangle(img, (cx - w_px // 2, top + 34),
                      (cx + w_px // 2, top + h_px), (0, 190, 255), -1)
        cv2.circle(img, (cx, top + 18), max(10, w_px // 4), (0, 190, 255), -1)

    # 跟踪框（来自 Norfair 的真实输出）
    if tracked_box and len(tracked_box) >= 4:
        x1, y1, x2, y2 = tracked_box[:4]
        cv2.rectangle(img, (x1, y1), (x2, y2), (60, 230, 90), 2)

    # 中心十字与死区
    dzx = int(deadzone_x * FRAME_W)
    dzy = int(deadzone_y * FRAME_H)
    cv2.rectangle(img, (FRAME_W // 2 - dzx, FRAME_H // 2 - dzy),
                  (FRAME_W // 2 + dzx, FRAME_H // 2 + dzy), (95, 95, 95), 1)
    cv2.line(img, (FRAME_W // 2 - 14, FRAME_H // 2),
             (FRAME_W // 2 + 14, FRAME_H // 2), (150, 150, 150), 1)
    cv2.line(img, (FRAME_W // 2, FRAME_H // 2 - 14),
             (FRAME_W // 2, FRAME_H // 2 + 14), (150, 150, 150), 1)

    # 状态条
    bar = (0, 0, 0) if not moving else (0, 60, 120)
    cv2.rectangle(img, (0, 0), (FRAME_W, 30), bar, -1)
    color = (80, 220, 255) if moving else (170, 170, 170)
    cv2.putText(img, 'PAN' if moving else 'STOP', (10, 21),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    for i, line in enumerate(hud):
        cv2.putText(img, line, (110 + i * 235, 21),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (215, 215, 215), 1)
    return img


# ========================================
# 主仿真
# ========================================

@dataclass
class SimResult:
    scenario: str
    frames: int
    duration: float
    id_breaks: int
    lock_id_changes: int
    identity_swaps: int
    mean_err: float
    max_err: float
    final_err: float
    move_segments: int
    move_time: float
    target_visible_ratio: float
    commands: int
    ptz_enabled: bool
    ended_moving: bool = False

def ground_truth(t: float, args) -> List[Tuple[float, float]]:
    """
    本帧世界里所有目标的角度 [(方位角, 俯仰角), ...]

    --targets 2 时加入一个相向而行的路人，两人会在画面中部交叉。
    这是专门用来暴露"云台转动期间放宽匹配阈值"的代价：阈值放宽到 1.01
    后任何一对框都允许配对，全靠匈牙利算法取最近邻，两人交错时就有
    换人可能。平行同向行走测不出问题（间距恒定，匹配毫无歧义）。
    """
    alpha = target_angle(args.scenario, t, args.speed)
    elev = target_elev(t)
    people = [(alpha, elev)]
    for i in range(1, max(1, args.targets)):
        if i % 2 == 1:
            # 反向行走 + 稍高（近处的人显得更大更靠上），避免两框完全重合
            people.append((-alpha + 2.5, elev + 3.0))
        else:
            people.append((alpha - 12.0, elev - 2.0))
    return people


def run_sim(args) -> SimResult:


    from src.tracking.norfair_tracker import NorfairTracker
    from src.cameras.ptz_autotrack import (
        PTZAutoTrackService, get_autotrack_config, reload_autotrack_config,
    )

    cfg = dict(reload_autotrack_config())
    if args.pan_sign is not None:
        cfg['pan_sign'] = args.pan_sign
    if args.tick is not None:
        cfg['tick_interval'] = args.tick

    fps = args.fps
    dt = 1.0 / fps
    duration = args.duration or _SCENARIOS[args.scenario][1]

    # --- 设备与虚拟时钟 ---
    device = SimPanTilt(
        max_speed_deg=args.device_speed,
        invert_pan=args.device_invert_pan,
        invert_tilt=args.device_invert_tilt,
    )
    sim_time = [0.0]

    def clock() -> float:
        return sim_time[0]

    # --- 真实控制服务 ---
    service = PTZAutoTrackService()
    metrics = service.ensure_metrics(
        'sim-cam', autotracker_enabled=bool(args.ptz))
    service.attach_controller('sim-cam', device)
    service.set_clock(clock)
    # 让配置缓存生效：_tick 内部会重新 get_autotrack_config()
    import src.cameras.ptz_autotrack as _autotrack_mod
    _autotrack_mod._config_cache = cfg

    # --- 真实跟踪器 ---
    tracker = NorfairTracker(
        camera_id='sim-cam',
        frame_shape=(FRAME_H, FRAME_W),
        detect_config={
            'min_initialized': 3,
            'max_disappeared': 45,
            'stationary_threshold': 10,
            'threshold': 0.5,
            'fps': fps,
        },
        ptz_metrics=metrics,
    )
    if args.ptz_threshold is not None:
        # 转动期间的距离阈值，1.01=只要 IoU>=0 就允许配对（最宽松）。
        # 多目标场景下可把它调小做 A/B，观察 ID 断裂与串人的取舍。
        tracker._ptz_distance_threshold = float(args.ptz_threshold)

    detector = DetectionSource(seed=args.seed)

    writer = None
    if not args.no_video:
        out_path = Path(args.out) if args.out else (
            PROJECT_ROOT / 'output' / f'ptz_sim_{args.scenario}.mp4')
        out_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(
            str(out_path), cv2.VideoWriter_fourcc(*'mp4v'), fps,
            (FRAME_W, FRAME_H))
        print(f"[sim] 视频输出: {out_path}")

    locked_id: Optional[str] = None
    last_active: set = set()
    id_breaks = 0
    lock_changes = 0
    identity_swaps = 0
    swap_frames = 0
    errs: List[float] = []
    visible_count = 0
    tick_acc = 0.0
    frame_idx = 0
    n_frames = int(duration * fps)

    while frame_idx < n_frames:
        sim_time[0] = frame_idx * dt
        t = sim_time[0]

        device.step(dt, t)
        truths = ground_truth(t, args)
        alpha, elev = truths[0]

        # 世界 → 画面投影（水平/俯仰两条轴各自独立换算）
        # 水平：目标方位角大于相机方位角 → 目标在画面右侧
        # 俯仰：相机抬头(tilt 增大) → 目标相对光轴下移 → 画面 y 增大
        #       即 y = 0.5 + (tilt - elev)/FOV_V，符号不可反，
        #       反了会让 tilt_sign 的负反馈变成正反馈，云台顶到限位。
        projs: List[Tuple[float, float, bool]] = []
        for (a, e) in truths:
            px = 0.5 + (a - device.pan) / FOV_DEG
            py = 0.5 + (device.tilt - e) / FOV_V_DEG
            projs.append((px, py, 0.0 <= px <= 1.0 and 0.0 <= py <= 1.0))

        x_norm, y_norm, in_frame = projs[0]
        if in_frame:
            visible_count += 1

        # 检测（目标完全出画则无检测）
        # 多人交错时，被挡住的那个人检测会消失——这是 ID 换人最常见的诱因，
        # 必须模拟，否则永远测不出"放宽阈值"的代价。
        occluded_idx = set()
        if len(truths) >= 2 and abs(truths[0][0] - truths[1][0]) < 3.0:
            occluded_idx.add(0)   # 0 号被较近的 1 号挡住

        dets = []
        for i, (px, py, ok) in enumerate(projs):
            if not ok or i in occluded_idx:
                continue
            det = detector.step(
                px, py, TARGET_H_NORM, FRAME_W, FRAME_H, device.moving)
            if det:
                dets.append(det)
        tracker.match_and_update(
            f"{t:.3f}", t, dets)
        objs = tracker.get_tracked_objects_dict()

        # --- 锁定目标（复刻 camera_state 的 autotrack 回调语义）---
        # 多目标时锁定画面里最靠左的那个，保证锁的是 0 号真值
        person_objs = {k: v for k, v in objs.items()
                       if str(v.get('label', '')) == 'person'}

        def _leftmost(_objs):
            return min(
                _objs,
                key=lambda k: ((_objs[k].get('box') or [0, 0, 0, 0])[0]))

        if locked_id is not None and locked_id not in objs:
            if in_frame and person_objs:
                # 目标明明还在画面里，却被分配了新 ID —— 这就是 ID 断裂
                new_id = _leftmost(person_objs)
                if new_id != locked_id:
                    id_breaks += 1
                    lock_changes += 1
                    if args.verbose:
                        print(f"  [ID BREAK] t={t:.2f}s {locked_id} → {new_id}")
                locked_id = new_id
            elif not in_frame:
                locked_id = None   # 真的走了，等同于 _clear_ptz_tracking

        if locked_id is None and person_objs:
            locked_id = _leftmost(person_objs)
            lock_changes += 1

        # --- 串人检测（多目标时验证放宽阈值的代价）---
        # 跟踪 ID 没变，但框已经盖到另一个人身上 = 身份串了。
        # 这是 ID 断裂之外另一种失败模式，且更隐蔽：云台会跟着错误的人转。
        if (args.targets >= 2 and locked_id in objs and len(projs) >= 2
                and projs[0][2] and projs[1][2]):
            box = objs[locked_id].get('box') or [0, 0, 0, 0]
            cx_n = (box[0] + box[2]) / 2.0 / FRAME_W
            d0 = abs(cx_n - projs[0][0])
            d1 = abs(cx_n - projs[1][0])
            if d1 + 0.04 < d0:
                swap_frames += 1
                if swap_frames >= 8:
                    identity_swaps += 1
                    swap_frames = 0
                    if args.verbose:
                        print(f"  [ID SWAP] t={t:.2f}s 跟踪框已跟到另一个人身上")
            else:
                swap_frames = 0

        # --- 复刻 _update_ptz_target：每帧刷位置 ---
        if args.ptz and locked_id:
            obj = objs.get(locked_id)
            if obj:
                box = obj.get('box') or []
                if len(box) >= 4:
                    x1, y1, x2, y2 = (float(v) for v in box[:4])
                    if x2 > x1 and y2 > y1:
                        # 与生产代码一致：外推框只更新位置，不刷新"最后可见"
                        coasting = int(obj.get('disappeared') or 0) > 0
                        metrics.update_target(
                            cx=((x1 + x2) / 2.0) / FRAME_W,
                            cy=((y1 + y2) / 2.0) / FRAME_H,
                            w=(x2 - x1) / FRAME_W,
                            h=(y2 - y1) / FRAME_H,
                            label=str(obj.get('label') or ''),
                            obj_id=str(locked_id),
                            ts=t,               # 使用虚拟时钟
                            frame_time=t,
                            refresh_seen=not coasting,
                        )
            elif in_frame:
                metrics.mark_lost()

        # --- 控制周期 ---
        tick_acc += dt
        if tick_acc >= float(cfg.get('tick_interval') or 0.2) - 1e-9:
            tick_acc = 0.0
            service.tick_once()

        # --- 统计 ---
        if in_frame:
            errs.append(abs(x_norm - 0.5))

        # --- 渲染 ---
        if writer is not None:
            tracked_box = None
            if locked_id and locked_id in objs:
                tracked_box = objs[locked_id].get('box')
            hud = [
                f"t={t:5.1f}s",
                f"pan={device.pan:+6.2f}",
                f"alpha={alpha:+6.2f}",
                f"err={abs(x_norm - 0.5) if in_frame else -1:+.2f}",
                f"id={locked_id[:10] if locked_id else 'NONE'}",
                f"break={id_breaks}",
            ]
            writer.write(render(
                device.pan, alpha,
                [(px, py) for (px, py, ok) in projs if ok],
                tracked_box, hud,
                float(cfg.get('deadzone_x') or 0.12),
                float(cfg.get('deadzone_y') or 0.12),
                device.moving))

        frame_idx += 1
        if args.verbose and frame_idx % fps == 0:
            print(f"  t={t:5.1f}s pan={device.pan:+6.2f} "
                  f"err={errs[-1] if errs else -1:.3f} "
                  f"moving={device.moving} id={locked_id} "
                  f"objs={len(person_objs)}")

    if writer is not None:
        writer.release()
    ended_moving = device.moving
    if device.moving:
        device.stop_move()

    move_segments = device.commands
    return SimResult(
        scenario=args.scenario,
        frames=frame_idx,
        duration=frame_idx * dt,
        id_breaks=id_breaks,
        lock_id_changes=lock_changes,
        identity_swaps=identity_swaps,
        mean_err=float(np.mean(errs)) if errs else -1.0,
        max_err=float(np.max(errs)) if errs else -1.0,
        final_err=float(errs[-1]) if errs else -1.0,
        move_segments=move_segments,
        move_time=device.total_move_time + (
            sim_time[0] - device._moving_since
            if device._moving_since is not None else 0.0),
        target_visible_ratio=visible_count / max(1, frame_idx),
        commands=device.commands,
        ptz_enabled=bool(args.ptz),
        ended_moving=ended_moving,
    )


def verdict(r: SimResult) -> Tuple[str, str]:
    """
    根据指标给出结论

    只看 ID 断裂是不够的：方向配反时目标会一直被推出画面，但每次都是
    "同一个 ID 追着跑"，ID 断裂反而是 0。必须同时看目标出画比例与居中误差。
    """
    if not r.ptz_enabled:
        return ('baseline',
                '对照组：自动跟踪关闭，目标无法被拉回中心，误差随目标远离而增大。')
    if r.scenario == 'exit':
        # 该场景的目标出画是设计如此，判定重点变成"出画后云台是否停转"
        if r.ended_moving:
            return ('bad',
                    '目标已出画但云台仍在转 —— 丢失停转失效，'
                    '实机上会一路顶到机械限位。')
        return ('ok',
                '目标出画后云台已停转（出画属于本场景的预期行为）。')
    if r.id_breaks > 0:
        return ('bad',
                f'ID 断裂 {r.id_breaks} 次 —— 云台转动期间匹配失败，'
                '需调小 tick_interval 或放宽 _ptz_distance_threshold。')
    if r.identity_swaps > 0:
        return ('bad',
                f'身份串人 {r.identity_swaps} 次 —— 跟踪 ID 未变但框已跟到另一个人，'
                '转动期间 _ptz_distance_threshold=1.01 过于宽松，'
                '多目标场景应收紧（可先试 0.95）。')
    if r.target_visible_ratio < 0.9:
        return ('bad',
                f'目标出画严重（仅 {r.target_visible_ratio * 100:.0f}% 在画内）'
                ' —— 优先检查 pan_sign / tilt_sign 是否配反，其次看 speed_max。')
    if r.mean_err > 0.22:
        return ('warn',
                f'ID 稳定但居中偏差偏大（均值 {r.mean_err:.3f}）'
                ' —— 可调小 deadzone 或提高 speed_min。')
    return ('ok',
            '云台转动期间跟踪稳定，目标保持在画面中心区域。')


def print_report(r: SimResult) -> None:
    print()
    print("=" * 62)
    print(f" PTZ 自动跟踪仿真报告  ({'自动跟踪开' if r.ptz_enabled else '对照：自动跟踪关'})")
    print("=" * 62)
    print(f" 场景           : {r.scenario}  ({_SCENARIOS.get(r.scenario, ('',))[0]})")
    print(f" 时长/帧数      : {r.duration:.1f}s / {r.frames}")
    print(f" 目标在画内占比 : {r.target_visible_ratio * 100:.1f}%")
    print(f" 云台指令次数   : {r.commands}")
    print(f" 云台转动累计   : {r.move_time:.1f}s")
    print(f" ID 断裂次数    : {r.id_breaks}      身份串人次数 : {r.identity_swaps}")
    print(f" 居中误差(均值) : {r.mean_err:.3f}   (归一化，0.5 表示满屏半宽)")
    print(f" 居中误差(最大) : {r.max_err:.3f}")
    print("-" * 62)
    level, message = verdict(r)
    icon = {'ok': ' ✅', 'warn': ' ⚠️ ', 'bad': ' ❌', 'baseline': ' ○'}[level]
    print(f"{icon} {message}")
    print("=" * 62)


def main() -> int:
    p = argparse.ArgumentParser(
        description='PTZ 自动跟踪闭环模拟器（无需真实云台）')
    p.add_argument('--scenario', default='walk', choices=list(_SCENARIOS),
                   help='目标运动场景')
    p.add_argument('--duration', type=float, default=0.0, help='仿真时长(秒)')
    p.add_argument('--fps', type=int, default=25, help='帧率')
    p.add_argument('--speed', type=float, default=10.0,
                   help='目标角速度(度/秒)')
    p.add_argument('--tick', type=float, default=None,
                   help='控制周期(秒)，默认读配置')
    p.add_argument('--pan-sign', type=float, default=None,
                   help='覆盖配置 pan_sign（1/-1）')
    p.add_argument('--device-speed', type=float, default=40.0,
                   help='假云台最大角速度(度/秒)')
    p.add_argument('--device-invert-pan', action='store_true',
                   help='模拟 ONVIF pan 正负与预期相反')
    p.add_argument('--device-invert-tilt', action='store_true',
                   help='模拟 ONVIF tilt 正负与预期相反')
    p.add_argument('--no-ptz', dest='ptz', action='store_false',
                   help='关闭自动跟踪（对照组）')
    p.add_argument('--no-video', action='store_true', help='不渲染视频')
    p.add_argument('--out', default='', help='视频输出路径')
    p.add_argument('--ptz-threshold', type=float, default=None,
                   help='覆盖云台转动期间的 IoU 距离阈值（默认 1.01，越小越严）')
    p.add_argument('--targets', type=int, default=1,
                   help='画面内人数（2 用于验证转动期间是否串人）')
    p.add_argument('--seed', type=int, default=7, help='随机种子')
    p.add_argument('--verbose', action='store_true', help='打印逐秒状态')
    p.add_argument('--json', default='', help='把结果写入 JSON 文件')
    args = p.parse_args()

    started = time.time()
    result = run_sim(args)
    print_report(result)
    print(f" 仿真耗时       : {time.time() - started:.1f}s")
    if args.json:
        Path(args.json).write_text(
            json.dumps(result.__dict__, ensure_ascii=False, indent=2),
            encoding='utf-8')
        print(f" 结果已写入     : {args.json}")
    return 0 if verdict(result)[0] in ('ok', 'baseline') else 2


if __name__ == '__main__':
    sys.exit(main())
