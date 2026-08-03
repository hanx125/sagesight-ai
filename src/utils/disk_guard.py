"""磁盘空间保护工具

本地存储（事件图片、GIF 等）写入前检查磁盘可用空间，不足时按最旧文件优先删除，
实现"滚动新的替换旧的"，避免磁盘满导致写入失败影响事件产生。

设计原则：
- 只在可用空间低于阈值时触发清理，正常情况下零开销
- 按文件修改时间排序，最旧优先删除
- 保留最少文件数（min_keep_count），避免把目录清空
- 删除失败不抛异常，只记日志（不阻塞主流程）
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 默认最小保留空闲空间 500MB，低于此值触发滚动清理
DEFAULT_MIN_FREE_MB = 500
# 默认最少保留文件数，避免把目录清空
DEFAULT_MIN_KEEP_COUNT = 10


def get_free_space_mb(path: str) -> float:
    """获取 path 所在磁盘的可用空间（MB）"""
    try:
        usage = os.statvfs(path)
        return (usage.f_bavail * usage.f_frsize) / (1024 * 1024)
    except AttributeError:
        # Windows 无 os.statvfs，用 shutil.disk_usage
        import shutil
        return shutil.disk_usage(path).free / (1024 * 1024)
    except Exception as e:
        logger.debug(f"[disk_guard] 获取磁盘空间失败: {e}")
        return float('inf')  # 获取失败时不触发清理


def ensure_disk_space(
    dir_path: str,
    needed_bytes: int,
    min_free_mb: float = DEFAULT_MIN_FREE_MB,
    min_keep_count: int = DEFAULT_MIN_KEEP_COUNT,
) -> bool:
    """确保目录所在磁盘有足够可用空间

    当可用空间低于 min_free_mb 时，按最旧文件优先删除目录中的文件，
    直到可用空间恢复到 min_free_mb 以上或剩余文件数 <= min_keep_count。

    Args:
        dir_path: 要保护的目录
        needed_bytes: 本次写入需要的字节数（用于日志）
        min_free_mb: 最小保留空闲空间（MB），低于此值触发清理
        min_keep_count: 最少保留文件数

    Returns:
        True 表示空间充足或已清理出空间，False 表示清理后仍不足（调用方自行处理）
    """
    if not os.path.exists(dir_path):
        return True  # 目录不存在，由调用方创建

    current_free_mb = get_free_space_mb(dir_path)
    if current_free_mb >= min_free_mb:
        return True  # 空间充足，无需清理

    # 收集目录下所有文件（按修改时间升序：最旧优先删除）
    files = []
    try:
        for name in os.listdir(dir_path):
            filepath = os.path.join(dir_path, name)
            if os.path.isfile(filepath):
                try:
                    mtime = os.path.getmtime(filepath)
                    size = os.path.getsize(filepath)
                    files.append((mtime, filepath, size))
                except OSError:
                    continue
    except OSError as e:
        logger.warning(f"[disk_guard] 列目录失败 {dir_path}: {e}")
        return False

    # 按修改时间升序排序（最旧在前）
    files.sort(key=lambda x: x[0])

    deleted_count = 0
    freed_mb = 0.0
    target_free_mb = min_free_mb

    for mtime, filepath, size in files:
        if get_free_space_mb(dir_path) >= target_free_mb:
            break
        if len(files) - deleted_count <= min_keep_count:
            break
        try:
            os.remove(filepath)
            deleted_count += 1
            freed_mb += size / (1024 * 1024)
        except OSError as e:
            logger.debug(f"[disk_guard] 删除失败 {filepath}: {e}")
            continue

    if deleted_count > 0:
        logger.info(
            f"[disk_guard] 磁盘空间不足({current_free_mb:.0f}MB < {min_free_mb}MB)，"
            f"已滚动删除 {deleted_count} 个旧文件，释放 {freed_mb:.1f}MB"
        )

    return get_free_space_mb(dir_path) >= min_free_mb
