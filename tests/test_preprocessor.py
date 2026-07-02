"""
预处理模块测试脚本

测试内容：
1. YUV420p → RGB 转换
2. NV12 → RGB 转换
3. 归一化验证
4. HWC → CHW 转换
5. 输出张量形状验证
6. 性能测试
"""

import sys
import time
import numpy as np
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.detectors.preprocessor import DetectionPreprocessor, PreprocessorConfig, create_preprocessor


def create_test_yuv420p(seq_num: int = 0):
    """创建测试 YUV420p 数据"""
    # 创建测试 Y 平面（渐变灰度）
    y = np.zeros((640, 640), dtype=np.uint8)
    for i in range(640):
        y[i, :] = (seq_num * 10 + i) % 256
    
    # 创建测试 U/V 平面（中性色）
    u = np.full((320, 320), 128, dtype=np.uint8)
    v = np.full((320, 320), 128, dtype=np.uint8)
    
    return y, u, v


def create_test_nv12(seq_num: int = 0):
    """创建测试 NV12 数据"""
    # 创建测试 Y 平面
    y = np.zeros((640, 640), dtype=np.uint8)
    for i in range(640):
        y[i, :] = (seq_num * 10 + i) % 256
    
    # 创建测试 UV 交错平面
    uv = np.zeros((320, 640), dtype=np.uint8)
    uv[:, :] = 128  # 中性色
    
    return y, uv


def test_yuv420p_to_rgb():
    """测试 YUV420p → RGB 转换"""
    print("\n" + "="*60)
    print("测试 1: YUV420p → RGB 转换")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试数据
        y, u, v = create_test_yuv420p(seq_num=1)
        
        print(f"\n输入数据:")
        print(f"  Y 平面：shape={y.shape}, dtype={y.dtype}, range=[{y.min()}, {y.max()}]")
        print(f"  U 平面：shape={u.shape}, dtype={u.dtype}, range=[{u.min()}, {u.max()}]")
        print(f"  V 平面：shape={v.shape}, dtype={v.dtype}, range=[{v.min()}, {v.max()}]")
        
        # 执行转换
        print("\n[测试] 执行 YUV420p → RGB 转换...")
        rgb_image = preprocessor._yuv420p_to_rgb(y, u, v)
        
        print(f"\n输出数据:")
        print(f"  RGB 图像：shape={rgb_image.shape}, dtype={rgb_image.dtype}")
        print(f"  通道 0(R) 范围：[{rgb_image[:,:,0].min()}, {rgb_image[:,:,0].max()}]")
        print(f"  通道 1(G) 范围：[{rgb_image[:,:,1].min()}, {rgb_image[:,:,1].max()}]")
        print(f"  通道 2(B) 范围：[{rgb_image[:,:,2].min()}, {rgb_image[:,:,2].max()}]")
        
        # 验证
        assert rgb_image.shape == (640, 640, 3), f"RGB 形状错误：{rgb_image.shape}"
        assert rgb_image.dtype == np.uint8, f"RGB 数据类型错误：{rgb_image.dtype}"
        print("\n[PASS] YUV420p → RGB 转换测试通过")
        
        return True
    
    except Exception as e:
        print(f"\n[FAIL] YUV420p → RGB 转换测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_nv12_to_rgb():
    """测试 NV12 → RGB 转换"""
    print("\n" + "="*60)
    print("测试 2: NV12 → RGB 转换")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试数据
        y, uv = create_test_nv12(seq_num=1)
        
        print(f"\n输入数据:")
        print(f"  Y 平面：shape={y.shape}, dtype={y.dtype}, range=[{y.min()}, {y.max()}]")
        print(f"  UV 平面：shape={uv.shape}, dtype={uv.dtype}, range=[{uv.min()}, {uv.max()}]")
        
        # 执行转换
        print("\n[测试] 执行 NV12 → RGB 转换...")
        rgb_image = preprocessor._nv12_to_rgb(y, uv)
        
        print(f"\n输出数据:")
        print(f"  RGB 图像：shape={rgb_image.shape}, dtype={rgb_image.dtype}")
        print(f"  通道 0(R) 范围：[{rgb_image[:,:,0].min()}, {rgb_image[:,:,0].max()}]")
        print(f"  通道 1(G) 范围：[{rgb_image[:,:,1].min()}, {rgb_image[:,:,1].max()}]")
        print(f"  通道 2(B) 范围：[{rgb_image[:,:,2].min()}, {rgb_image[:,:,2].max()}]")
        
        # 验证
        assert rgb_image.shape == (640, 640, 3), f"RGB 形状错误：{rgb_image.shape}"
        assert rgb_image.dtype == np.uint8, f"RGB 数据类型错误：{rgb_image.dtype}"
        print("\n[PASS] NV12 → RGB 转换测试通过")
        
        return True
    
    except Exception as e:
        print(f"\n[FAIL] NV12 → RGB 转换测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_normalize():
    """测试归一化"""
    print("\n" + "="*60)
    print("测试 3: 归一化 (0-255 → 0-1)")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试 RGB 图像
        rgb_image = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
        
        print(f"\n输入数据:")
        print(f"  RGB 图像：shape={rgb_image.shape}, dtype={rgb_image.dtype}")
        print(f"  值范围：[{rgb_image.min()}, {rgb_image.max()}]")
        
        # 执行归一化
        print("\n[测试] 执行归一化...")
        rgb_normalized = preprocessor._normalize(rgb_image)
        
        print(f"\n输出数据:")
        print(f"  归一化图像：shape={rgb_normalized.shape}, dtype={rgb_normalized.dtype}")
        print(f"  值范围：[{rgb_normalized.min():.4f}, {rgb_normalized.max():.4f}]")
        
        # 验证
        assert rgb_normalized.shape == (640, 640, 3), f"形状错误：{rgb_normalized.shape}"
        assert rgb_normalized.dtype == np.float32, f"数据类型错误：{rgb_normalized.dtype}"
        assert rgb_normalized.min() >= 0.0, f"最小值错误：{rgb_normalized.min()}"
        assert rgb_normalized.max() <= 1.0, f"最大值错误：{rgb_normalized.max()}"
        print("\n[PASS] 归一化测试通过")
        
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 归一化测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_hwc_to_chw():
    """测试 HWC → CHW 转换"""
    print("\n" + "="*60)
    print("测试 4: HWC → CHW 转换")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试 HWC 图像
        hwc_image = np.random.rand(640, 640, 3).astype(np.float32)
        
        print(f"\n输入数据:")
        print(f"  HWC 图像：shape={hwc_image.shape}, dtype={hwc_image.dtype}")
        
        # 执行转换
        print("\n[测试] 执行 HWC → CHW 转换...")
        chw_tensor = preprocessor._hwc_to_chw(hwc_image)
        
        print(f"\n输出数据:")
        print(f"  CHW 张量：shape={chw_tensor.shape}, dtype={chw_tensor.dtype}")
        
        # 验证
        assert chw_tensor.shape == (3, 640, 640), f"形状错误：{chw_tensor.shape}"
        assert chw_tensor.dtype == np.float32, f"数据类型错误：{chw_tensor.dtype}"
        assert chw_tensor.flags['C_CONTIGUOUS'], "内存不连续"
        
        # 验证数据正确性
        for c in range(3):
            assert np.array_equal(chw_tensor[c], hwc_image[:, :, c]), f"通道 {c} 数据错误"
        
        print("\n[PASS] HWC → CHW 转换测试通过")
        
        return True
    
    except Exception as e:
        print(f"\n[FAIL] HWC → CHW 转换测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_full_preprocess_yuv420p():
    """测试完整的 YUV420p 预处理流程"""
    print("\n" + "="*60)
    print("测试 5: 完整预处理流程 (YUV420p)")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试数据
        y, u, v = create_test_yuv420p(seq_num=1)
        
        print(f"\n输入数据:")
        print(f"  Y: {y.shape}, U: {u.shape}, V: {v.shape}")
        
        # 执行完整预处理
        print("\n[测试] 执行完整预处理...")
        start_time = time.time()
        tensor = preprocessor.preprocess(y, u, v)
        elapsed = time.time() - start_time
        
        print(f"\n输出数据:")
        print(f"  张量：shape={tensor.shape}, dtype={tensor.dtype}")
        print(f"  值范围：[{tensor.min():.4f}, {tensor.max():.4f}]")
        print(f"  处理时间：{elapsed*1000:.2f}ms")
        
        # 验证
        expected_shape = (1, 3, 640, 640)
        assert tensor.shape == expected_shape, f"形状错误：期望 {expected_shape}, 得到 {tensor.shape}"
        assert tensor.dtype == np.float32, f"数据类型错误：{tensor.dtype}"
        assert tensor.min() >= 0.0, f"最小值错误：{tensor.min()}"
        assert tensor.max() <= 1.0, f"最大值错误：{tensor.max()}"
        
        print(f"\n预期 FPS: {preprocessor.get_expected_fps():.0f}")
        print(f"实际 FPS: {1.0/elapsed:.0f} (基于单次处理)")
        
        print("\n[PASS] 完整预处理流程测试通过")
        
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 完整预处理流程测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_full_preprocess_nv12():
    """测试完整的 NV12 预处理流程"""
    print("\n" + "="*60)
    print("测试 6: 完整预处理流程 (NV12)")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试数据
        y, uv = create_test_nv12(seq_num=1)
        
        print(f"\n输入数据:")
        print(f"  Y: {y.shape}, UV: {uv.shape}")
        
        # 执行完整预处理
        print("\n[测试] 执行完整预处理...")
        start_time = time.time()
        tensor = preprocessor.preprocess_nv12(y, uv)
        elapsed = time.time() - start_time
        
        print(f"\n输出数据:")
        print(f"  张量：shape={tensor.shape}, dtype={tensor.dtype}")
        print(f"  值范围：[{tensor.min():.4f}, {tensor.max():.4f}]")
        print(f"  处理时间：{elapsed*1000:.2f}ms")
        
        # 验证
        expected_shape = (1, 3, 640, 640)
        assert tensor.shape == expected_shape, f"形状错误：期望 {expected_shape}, 得到 {tensor.shape}"
        assert tensor.dtype == np.float32, f"数据类型错误：{tensor.dtype}"
        assert tensor.min() >= 0.0, f"最小值错误：{tensor.min()}"
        assert tensor.max() <= 1.0, f"最大值错误：{tensor.max()}"
        
        print(f"\n预期 FPS: {preprocessor.get_expected_fps():.0f}")
        print(f"实际 FPS: {1.0/elapsed:.0f} (基于单次处理)")
        
        print("\n[PASS] 完整预处理流程 (NV12) 测试通过")
        
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 完整预处理流程 (NV12) 测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_performance():
    """性能测试"""
    print("\n" + "="*60)
    print("测试 7: 性能测试")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 创建测试数据
        y, u, v = create_test_yuv420p(seq_num=1)
        
        # 预热
        print("\n[测试] 预热...")
        for _ in range(10):
            preprocessor.preprocess(y, u, v)
        
        # 性能测试
        num_iterations = 100
        print(f"\n[测试] 运行 {num_iterations} 次预处理...")
        
        start_time = time.time()
        for i in range(num_iterations):
            tensor = preprocessor.preprocess(y, u, v)
        elapsed = time.time() - start_time
        
        avg_time_ms = (elapsed / num_iterations) * 1000
        fps = num_iterations / elapsed
        
        print(f"\n性能结果:")
        print(f"  平均处理时间：{avg_time_ms:.2f}ms/帧")
        print(f"  处理速度：{fps:.2f} FPS")
        print(f"  预期速度：{preprocessor.get_expected_fps():.0f} FPS")
        
        # 验证性能是否达标
        if fps >= 300:  # 目标 300+ FPS
            print(f"\n[OK] 性能达标（>{300} FPS）")
        else:
            print(f"\n[WARN] 性能低于预期（<{300} FPS）")
        
        print("\n[PASS] 性能测试完成")
        
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 性能测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_input_validation():
    """测试输入验证"""
    print("\n" + "="*60)
    print("测试 8: 输入验证")
    print("="*60)
    
    try:
        # 创建预处理器
        preprocessor = create_preprocessor(640, 640)
        
        # 测试错误的 Y 形状
        print("\n[测试] 测试错误的 Y 平面形状...")
        y_wrong = np.zeros((320, 320), dtype=np.uint8)
        u = np.zeros((320, 320), dtype=np.uint8)
        v = np.zeros((320, 320), dtype=np.uint8)
        
        try:
            preprocessor.preprocess(y_wrong, u, v)
            print("  [FAIL] 应该抛出异常")
            return False
        except ValueError as e:
            print(f"  [OK] 正确捕获异常：{e}")
        
        # 测试错误的数据类型
        print("\n[测试] 测试错误的数据类型...")
        y_float = np.zeros((640, 640), dtype=np.float32)
        
        try:
            preprocessor.preprocess(y_float, u, v)
            print("  [FAIL] 应该抛出异常")
            return False
        except ValueError as e:
            print(f"  [OK] 正确捕获异常：{e}")
        
        print("\n[PASS] 输入验证测试通过")
        
        return True
    
    except Exception as e:
        print(f"\n[FAIL] 输入验证测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有测试"""
    print("\n" + "="*60)
    print("预处理模块测试套件")
    print("="*60)
    print(f"项目根目录：{project_root}")
    print(f"NumPy 版本：{np.__version__}")
    
    tests = [
        ("YUV420p → RGB", test_yuv420p_to_rgb),
        ("NV12 → RGB", test_nv12_to_rgb),
        ("归一化", test_normalize),
        ("HWC → CHW", test_hwc_to_chw),
        ("完整流程 (YUV420p)", test_full_preprocess_yuv420p),
        ("完整流程 (NV12)", test_full_preprocess_nv12),
        ("性能测试", test_performance),
        ("输入验证", test_input_validation),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, "PASS" if success else "FAIL"))
        except Exception as e:
            print(f"\n[ERROR] {test_name} 测试失败：{e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, f"ERROR: {e}"))
    
    # 打印总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    for test_name, result in results:
        print(f"{test_name:25s}: [{result}]")
    
    all_passed = all(r[1] == "PASS" for r in results)
    if all_passed:
        print("\n[SUCCESS] 所有测试通过！")
    else:
        print("\n[FAILED] 部分测试失败")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
