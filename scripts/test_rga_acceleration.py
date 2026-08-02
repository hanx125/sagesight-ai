"""
测试RGA硬件加速（I420格式）

验证RGA库是否正确安装并可用，测试预处理性能与正确性。
在 RK3588 盒子上运行：python scripts/test_rga_acceleration.py
"""

import os
import sys
import time

import cv2
import numpy as np

# 自动检测项目根目录（兼容盒子部署路径 /root/sagesight-ai 和本地开发路径）
_PROJECT_CANDIDATES = [
    '/root/sagesight-ai',
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
]
for _p in _PROJECT_CANDIDATES:
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
        break

from src.utils.image import is_rga_available, _rga_preprocess_i420


def _make_i420_frame(width=1920, height=1080):
    """生成连续的 I420 测试帧 (1.5*H, W)"""
    y_plane = np.random.randint(0, 255, (height, width), dtype=np.uint8)
    u_plane = np.random.randint(0, 255, (height // 2, width // 2), dtype=np.uint8)
    v_plane = np.random.randint(0, 255, (height // 2, width // 2), dtype=np.uint8)
    frame = np.concatenate([y_plane, u_plane, v_plane]).reshape(int(height * 1.5), width)
    return np.ascontiguousarray(frame)


def test_rga_availability():
    """测试RGA是否可用"""
    print("=" * 60)
    print("测试RGA硬件加速（I420格式）")
    print("=" * 60)

    if is_rga_available():
        print("✅ RGA硬件加速已启用")
        return True
    else:
        print("❌ RGA硬件加速未启用")
        print("请检查：")
        print("  1. librga.so是否安装：ldconfig -p | grep rga")
        print("  2. 系统是否为RK3588平台")
        return False


def test_rga_preprocess():
    """测试RGA预处理性能（I420→RGB 裁剪+缩放）"""
    print("\n" + "=" * 60)
    print("测试RGA硬件预处理（I420 裁剪+缩放+RGB转换）")
    print("=" * 60)

    # 生成测试数据（1920x1080 I420格式）
    width, height = 1920, 1080
    frame = _make_i420_frame(width, height)

    # 裁剪区域（中间 1280x720 区域）
    region = (320, 180, 1600, 900)

    # 测试 RGA 硬件加速
    print("\n--- RGA 硬件加速 ---")
    rga_times = []
    rgb_result = None
    for i in range(10):
        start_time = time.time()
        rgb_result = _rga_preprocess_i420(frame, region, (640, 640), 'rgb')
        elapsed_time = time.time() - start_time
        rga_times.append(elapsed_time)

    rga_avg = np.mean(rga_times)
    print(f"输出形状: {rgb_result.shape}")
    print(f"平均耗时: {rga_avg * 1000:.2f} ms")
    print(f"最小耗时: {np.min(rga_times) * 1000:.2f} ms")
    print(f"最大耗时: {np.max(rga_times) * 1000:.2f} ms")
    print(f"理论FPS: {1.0 / rga_avg:.1f}")

    # 测试 BGR 输出
    print("\n--- RGA BGR 输出 ---")
    bgr_result = _rga_preprocess_i420(frame, region, (640, 640), 'bgr')
    print(f"BGR输出形状: {bgr_result.shape}")
    # RGB 与 BGR 应当只是通道顺序相反
    bgr_as_rgb = bgr_result[:, :, ::-1]
    diff = np.abs(rgb_result.astype(np.int16) - bgr_as_rgb.astype(np.int16))
    print(f"RGB vs BGR翻转最大差异: {diff.max()}（应为0）")

    # 测试 CPU 方式对比
    print("\n--- CPU 方式对比 ---")
    from src.utils.image import yuv_crop_and_resize
    cpu_times = []
    rgb_cpu = None
    for i in range(10):
        start_time = time.time()
        yuv_cropped = yuv_crop_and_resize(frame, region)
        rgb_cpu = cv2.cvtColor(yuv_cropped, cv2.COLOR_YUV2RGB_I420)
        elapsed_time = time.time() - start_time
        cpu_times.append(elapsed_time)

    cpu_avg = np.mean(cpu_times)
    print(f"输出形状: {rgb_cpu.shape}")
    print(f"平均耗时: {cpu_avg * 1000:.2f} ms")
    print(f"最小耗时: {np.min(cpu_times) * 1000:.2f} ms")
    print(f"最大耗时: {np.max(cpu_times) * 1000:.2f} ms")
    print(f"理论FPS: {1.0 / cpu_avg:.1f}")

    # 性能对比
    print("\n--- 性能对比 ---")
    speedup = cpu_avg / rga_avg if rga_avg > 0 else 0
    print(f"加速比: {speedup:.1f}x")
    if rga_avg < 0.005:
        print("✅ RGA性能优秀（<5ms）")
    elif rga_avg < 0.010:
        print("⚠️ RGA性能良好（5-10ms）")
    else:
        print("❌ RGA性能较差（>10ms）")

    # 正确性对比（RGA 与 CPU 输出应当相近，允许缩放插值导致的微小差异）
    print("\n--- 正确性对比（RGA vs CPU）---")
    if rgb_result.shape == rgb_cpu.shape:
        correctness_diff = np.abs(rgb_result.astype(np.int16) - rgb_cpu.astype(np.int16))
        mean_diff = correctness_diff.mean()
        max_diff = correctness_diff.max()
        print(f"平均像素差异: {mean_diff:.2f}")
        print(f"最大像素差异: {max_diff}")
        if max_diff <= 30:
            print("✅ 输出一致（差异在缩放插值允许范围内）")
        else:
            print("⚠️ 输出差异较大，请检查颜色空间模式或缩放算法")
    else:
        print(f"❌ 输出形状不匹配: RGA={rgb_result.shape} vs CPU={rgb_cpu.shape}")


def test_resource_leak():
    """资源泄漏压力测试：连续运行大量帧，验证 handle 是否正确释放"""
    print("\n" + "=" * 60)
    print("资源泄漏压力测试（连续 500 帧）")
    print("=" * 60)

    frame = _make_i420_frame(1920, 1080)
    region = (320, 180, 1600, 900)

    start = time.time()
    for i in range(500):
        _rga_preprocess_i420(frame, region, (640, 640), 'rgb')
        if (i + 1) % 100 == 0:
            elapsed = time.time() - start
            print(f"  已处理 {i + 1}/500 帧，累计耗时 {elapsed:.2f}s")
    total = time.time() - start
    print(f"✅ 500帧全部成功，总耗时 {total:.2f}s，平均 {total / 500 * 1000:.2f} ms/帧")
    print("（若无内核报错或 OOM，说明 handle 释放正常）")


def main():
    print("\nRK3588 RGA硬件加速测试工具（I420格式）")
    print()

    # 测试1：RGA可用性
    rga_available = test_rga_availability()

    if rga_available:
        # 测试2：RGA预处理性能与正确性
        try:
            test_rga_preprocess()
        except Exception as e:
            print(f"\n❌ RGA预处理测试失败: {e}")
            import traceback
            traceback.print_exc()
            return

        # 测试3：资源泄漏压力测试
        try:
            test_resource_leak()
        except Exception as e:
            print(f"\n❌ 资源泄漏测试失败: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("\n请先安装RGA库：")
        print("  scp libs/librga/Linux/aarch64/librga.so root@<盒子IP>:/usr/lib/")
        print("  ldconfig")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == '__main__':
    main()
