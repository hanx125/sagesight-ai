"""
本地文件清理脚本

定期清理过期的图片和GIF文件，释放磁盘空间。
可独立运行或通过定时任务（cron）执行。
"""

import os
import sys
import time
import argparse
from datetime import datetime, timedelta


def cleanup_files(directory: str, retention_days: int = 7, 
                  file_extensions: list = None, verbose: bool = True):
    """
    清理指定目录中的过期文件
    
    Args:
        directory: 目录路径
        retention_days: 保留天数（默认7天）
        file_extensions: 文件扩展名列表（如 ['.gif', '.jpg', '.png']）
        verbose: 是否输出详细信息
    
    Returns:
        (deleted_count, total_size) - 删除文件数和释放空间大小
    """
    if not os.path.exists(directory):
        if verbose:
            print(f"[Cleanup] 目录不存在: {directory}")
        return 0, 0
    
    retention_seconds = retention_days * 24 * 3600
    current_time = time.time()
    
    deleted_count = 0
    total_size = 0
    
    try:
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            
            if os.path.isfile(filepath):
                # 检查文件扩展名
                if file_extensions:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext not in file_extensions:
                        continue
                
                file_mtime = os.path.getmtime(filepath)
                age_seconds = current_time - file_mtime
                
                # 删除过期文件
                if age_seconds > retention_seconds:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    deleted_count += 1
                    total_size += file_size
                    
                    if verbose:
                        age_days = age_seconds / (24 * 3600)
                        print(f"[Cleanup] 删除: {filename} (年龄: {age_days:.1f} 天)")
    
    except Exception as e:
        print(f"[Cleanup] 清理失败: {e}")
    
    return deleted_count, total_size


def cleanup_all(retention_days: int = 7, verbose: bool = True):
    """
    清理所有本地存储目录
    
    Args:
        retention_days: 保留天数
        verbose: 是否输出详细信息
    """
    # 获取项目根目录
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # 定义清理目录
    cleanup_dirs = [
        {
            'path': os.path.join(project_root, 'data', 'gifs'),
            'extensions': ['.gif'],
            'name': 'GIF 动画'
        },
        {
            'path': os.path.join(project_root, 'data', 'images'),
            'extensions': ['.jpg', '.jpeg', '.png'],
            'name': '事件图片'
        }
    ]
    
    print(f"[Cleanup] 开始清理本地文件")
    print(f"[Cleanup] 保留天数: {retention_days}")
    print(f"[Cleanup] 清理时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[Cleanup] {'='*50}")
    
    total_deleted = 0
    total_size = 0
    
    for dir_info in cleanup_dirs:
        dir_path = dir_info['path']
        extensions = dir_info['extensions']
        name = dir_info['name']
        
        if os.path.exists(dir_path):
            print(f"\n[Cleanup] 清理目录: {name}")
            print(f"[Cleanup]   路径: {dir_path}")
            print(f"[Cleanup]   扩展名: {extensions}")
            
            deleted, size = cleanup_files(
                directory=dir_path,
                retention_days=retention_days,
                file_extensions=extensions,
                verbose=verbose
            )
            
            total_deleted += deleted
            total_size += size
            
            print(f"[Cleanup]   已清理: {deleted} 个文件")
            print(f"[Cleanup]   释放空间: {size / 1024 / 1024:.2f} MB")
        else:
            print(f"\n[Cleanup] 目录不存在: {dir_path}")
    
    print(f"\n[Cleanup] {'='*50}")
    print(f"[Cleanup] 清理完成")
    print(f"[Cleanup]   总删除文件: {total_deleted}")
    print(f"[Cleanup]   总释放空间: {total_size / 1024 / 1024:.2f} MB")
    
    return total_deleted, total_size


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='清理本地过期文件')
    
    parser.add_argument(
        '--days', 
        type=int, 
        default=7,
        help='保留天数（默认7天）'
    )
    
    parser.add_argument(
        '--dir', 
        type=str, 
        default=None,
        help='指定清理目录（默认清理所有）'
    )
    
    parser.add_argument(
        '--ext', 
        type=str, 
        nargs='+',
        default=None,
        help='指定文件扩展名（如 .gif .jpg）'
    )
    
    parser.add_argument(
        '--quiet', 
        action='store_true',
        help='静默模式（不输出详细信息）'
    )
    
    args = parser.parse_args()
    
    verbose = not args.quiet
    
    if args.dir:
        # 清理指定目录
        deleted, size = cleanup_files(
            directory=args.dir,
            retention_days=args.days,
            file_extensions=args.ext,
            verbose=verbose
        )
        
        if verbose:
            print(f"[Cleanup] 已清理 {deleted} 个文件")
            print(f"[Cleanup] 释放空间: {size / 1024 / 1024:.2f} MB")
    else:
        # 清理所有目录
        cleanup_all(retention_days=args.days, verbose=verbose)


if __name__ == '__main__':
    main()