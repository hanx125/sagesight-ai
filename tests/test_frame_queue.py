"""
帧队列管理模块测试

测试内容：
1. 基本入队/出队功能
2. 队列满时的丢帧行为
3. 线程安全测试
4. 性能统计验证
"""

import sys
import time
import threading
import numpy as np
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.frame_queue import FrameQueue, FrameQueueMonitor, FrameQueueStats
from src.utils.shared_memory import FrameHeader


def create_test_frame(seq_num: int, timestamp_ns: int = None) -> tuple:
    """创建测试帧"""
    if timestamp_ns is None:
        timestamp_ns = time.time_ns()
    
    header = FrameHeader(
        sequence_num=seq_num,
        timestamp_ns=timestamp_ns,
        write_time_ns=time.time_ns(),
        width=640,
        height=640
    )
    
    # 创建测试 YUV 数据
    y_plane = np.full((640, 640), seq_num % 256, dtype=np.uint8)
    u_plane = np.full((320, 320), 128, dtype=np.uint8)
    v_plane = np.full((320, 320), 128, dtype=np.uint8)
    
    return header, y_plane, u_plane, v_plane


def test_basic_enqueue_dequeue():
    """测试基本入队/出队功能"""
    print("\n" + "="*60)
    print("测试 1: 基本入队/出队功能")
    print("="*60)
    
    queue = FrameQueue(max_size=5)
    
    # 测试 1: 空队列 dequeue 返回 None
    print("\n[测试] 空队列 dequeue...")
    frame = queue.dequeue()
    assert frame is None, "空队列 dequeue 应该返回 None"
    print("  [OK] 空队列 dequeue 返回 None")
    
    # 测试 2: 入队一帧
    print("\n[测试] 入队 1 帧...")
    header, y, u, v = create_test_frame(seq_num=1)
    success = queue.enqueue(header, y, u, v)
    assert success, "入队应该成功"
    assert queue.size() == 1, "队列大小应该为 1"
    print("  [OK] 入队成功，队列大小=1")
    
    # 测试 3: dequeue 取出帧
    print("\n[测试] dequeue 取出帧...")
    frame = queue.dequeue()
    assert frame is not None, "dequeue 应该返回帧"
    assert frame.header.sequence_num == 1, "序列号应该为 1"
    assert queue.size() == 0, "队列应该为空"
    print(f"  [OK] dequeue 成功，序列号={frame.header.sequence_num}")
    
    # 测试 4: 入队多帧
    print("\n[测试] 连续入队 5 帧...")
    for i in range(2, 7):
        header, y, u, v = create_test_frame(seq_num=i)
        queue.enqueue(header, y, u, v)
    
    assert queue.size() == 5, "队列大小应该为 5"
    print(f"  [OK] 队列大小={queue.size()}")
    
    # 测试 5: 按顺序 dequeue
    print("\n[测试] 按顺序 dequeue...")
    for expected_seq in range(2, 7):
        frame = queue.dequeue()
        assert frame.header.sequence_num == expected_seq, f"序列号应该为 {expected_seq}"
        print(f"    帧 {expected_seq}: OK")
    
    assert queue.size() == 0, "队列应该为空"
    print("\n[PASS] 基本功能测试通过")
    
    return True


def test_queue_full_behavior():
    """测试队列满时的行为"""
    print("\n" + "="*60)
    print("测试 2: 队列满时的丢帧行为")
    print("="*60)
    
    queue = FrameQueue(max_size=3)
    
    # 入队 3 帧（队列满）
    print("\n[测试] 入队 3 帧（队列满）...")
    for i in range(1, 4):
        header, y, u, v = create_test_frame(seq_num=i)
        success = queue.enqueue(header, y, u, v)
        print(f"  帧 {i}: 入队{'成功' if success else '失败'}")
    
    assert queue.is_full(), "队列应该已满"
    print(f"  [OK] 队列已满 ({queue.size()}/{queue.max_size})")
    
    # 再入队 1 帧（应该触发丢帧）
    print("\n[测试] 再入队 1 帧（触发丢帧）...")
    header, y, u, v = create_test_frame(seq_num=4)
    success = queue.enqueue(header, y, u, v)
    assert not success, "队列满时入队应该返回 False"
    assert queue.size() == 3, "队列大小应该保持 3"
    print(f"  [OK] 入队失败（返回 False），队列大小保持={queue.size()}")
    
    # 验证最旧的帧被丢弃
    print("\n[测试] 验证最旧的帧被丢弃...")
    frame = queue.dequeue()
    assert frame.header.sequence_num == 2, "最旧的帧（帧 1）应该被丢弃，现在应该是帧 2"
    print(f"  [OK] 最旧的帧已丢弃，当前最旧帧序列号={frame.header.sequence_num}")
    
    # 检查统计信息
    stats = queue.get_stats()
    assert stats.dropped_frames == 1, "应该有 1 帧被丢弃"
    print(f"  [OK] 统计信息：丢弃帧数={stats.dropped_frames}")
    
    print("\n[PASS] 队列满行为测试通过")
    return True


def test_dequeue_latest():
    """测试 dequeue_latest 功能"""
    print("\n" + "="*60)
    print("测试 3: dequeue_latest（获取最新帧）")
    print("="*60)
    
    queue = FrameQueue(max_size=5)
    
    # 快速入队 5 帧
    print("\n[测试] 快速入队 5 帧...")
    for i in range(1, 6):
        header, y, u, v = create_test_frame(seq_num=i)
        queue.enqueue(header, y, u, v)
    
    print(f"  队列大小：{queue.size()}")
    
    # 使用 dequeue_latest 获取最新帧
    print("\n[测试] dequeue_latest 获取最新帧...")
    frame = queue.dequeue_latest()
    assert frame is not None, "应该返回帧"
    assert frame.header.sequence_num == 5, "应该返回最新的帧（序列号 5）"
    assert queue.size() == 0, "队列应该被清空"
    print(f"  [OK] 获取到最新帧，序列号={frame.header.sequence_num}")
    print(f"  [OK] 队列已清空，大小={queue.size()}")
    
    print("\n[PASS] dequeue_latest 测试通过")
    return True


def test_thread_safety():
    """测试线程安全性"""
    print("\n" + "="*60)
    print("测试 4: 线程安全测试")
    print("="*60)
    
    queue = FrameQueue(max_size=10)
    enqueue_count = 0
    dequeue_count = 0
    stop_flag = False
    
    def producer_thread():
        """生产者线程"""
        nonlocal enqueue_count
        seq = 1
        while not stop_flag:
            header, y, u, v = create_test_frame(seq_num=seq)
            queue.enqueue(header, y, u, v)
            enqueue_count += 1
            seq += 1
            time.sleep(0.01)  # 10ms 间隔
    
    def consumer_thread():
        """消费者线程"""
        nonlocal dequeue_count
        while not stop_flag:
            frame = queue.dequeue()
            if frame is not None:
                dequeue_count += 1
            time.sleep(0.015)  # 15ms 间隔
    
    # 启动生产者和消费者
    print("\n[测试] 启动生产者 + 消费者线程（运行 3 秒）...")
    producer = threading.Thread(target=producer_thread)
    consumer = threading.Thread(target=consumer_thread)
    
    producer.start()
    consumer.start()
    
    # 运行 3 秒
    time.sleep(3)
    stop_flag = True
    
    producer.join(timeout=1)
    consumer.join(timeout=1)
    
    print(f"  入队总数：{enqueue_count}")
    print(f"  出队总数：{dequeue_count}")
    print(f"  丢弃帧数：{queue.get_stats().dropped_frames}")
    print(f"  当前队列大小：{queue.size()}")
    
    # 验证
    stats = queue.get_stats()
    assert stats.total_frames == enqueue_count, "总入队帧数应该匹配"
    assert dequeue_count + queue.size() + stats.dropped_frames == enqueue_count, "帧数应该平衡"
    print(f"  [OK] 帧数平衡验证通过")
    
    print("\n[PASS] 线程安全测试通过")
    return True


def test_performance_stats():
    """测试性能统计功能"""
    print("\n" + "="*60)
    print("测试 5: 性能统计功能")
    print("="*60)
    
    queue = FrameQueue(max_size=5)
    
    # 入队 + 出队 10 帧
    print("\n[测试] 入队 + 出队 10 帧...")
    for i in range(1, 11):
        header, y, u, v = create_test_frame(seq_num=i)
        queue.enqueue(header, y, u, v)
        time.sleep(0.01)  # 10ms 间隔
        frame = queue.dequeue()
        time.sleep(0.005)  # 5ms 间隔
    
    # 获取统计信息
    stats = queue.get_stats()
    
    print(f"\n统计信息:")
    print(f"  总入队帧数：{stats.total_frames}")
    print(f"  丢弃帧数：{stats.dropped_frames}")
    print(f"  平均延迟：{stats.avg_queue_delay_ms:.2f} ms")
    print(f"  最小延迟：{stats.min_queue_delay_ms:.2f} ms")
    print(f"  最大延迟：{stats.max_queue_delay_ms:.2f} ms")
    
    assert stats.total_frames == 10, "总入队帧数应该为 10"
    assert stats.avg_queue_delay_ms > 0, "平均延迟应该大于 0"
    print(f"\n  [OK] 统计信息合理")
    
    # 打印详细统计
    print("\n[测试] 打印详细统计...")
    queue.print_stats()
    
    print("\n[PASS] 性能统计测试通过")
    return True


def test_monitor():
    """测试监控器功能"""
    print("\n" + "="*60)
    print("测试 6: 帧队列监控器")
    print("="*60)
    
    queue = FrameQueue(max_size=3)
    monitor = FrameQueueMonitor(queue, check_interval_ms=500)
    
    # 启动监控
    print("\n[测试] 启动监控器...")
    monitor.start()
    
    # 模拟队列持续满
    print("\n[测试] 模拟队列持续满（触发告警）...")
    for i in range(1, 4):
        header, y, u, v = create_test_frame(seq_num=i)
        queue.enqueue(header, y, u, v)
    
    # 等待监控器检测
    time.sleep(2)
    
    # 停止监控
    print("\n[测试] 停止监控器...")
    monitor.stop()
    
    print("\n[PASS] 监控器测试通过")
    return True


def main():
    """运行所有测试"""
    print("="*60)
    print("帧队列管理模块测试套件")
    print("="*60)
    
    tests = [
        ("基本入队/出队", test_basic_enqueue_dequeue),
        ("队列满行为", test_queue_full_behavior),
        ("dequeue_latest", test_dequeue_latest),
        ("线程安全", test_thread_safety),
        ("性能统计", test_performance_stats),
        ("监控器", test_monitor),
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
        print(f"{test_name:20s}: [{result}]")
    
    all_passed = all(r[1] == "PASS" for r in results)
    if all_passed:
        print("\n[SUCCESS] 所有测试通过！")
    else:
        print("\n[FAILED] 部分测试失败")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
