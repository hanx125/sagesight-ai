"""
项目名称清理验证脚本

验证项目中是否还有外部项目名称引用。
"""

import sys
import subprocess
from pathlib import Path
import io

# 设置输出编码（Windows 兼容）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def check_frigate_references():
    """检查源代码中的 Frigate 引用"""
    print("\n" + "=" * 60)
    print("检查源代码中的外部项目名称引用")
    print("=" * 60)
    
    src_path = Path("d:/sagesight-ai/src")
    
    if not src_path.exists():
        print(f"[错误] 源代码目录不存在: {src_path}")
        return False
    
    # 使用 grep 搜索（不区分大小写）
    try:
        result = subprocess.run(
            ["grep", "-ri", "frigate", str(src_path)],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            # 找到引用
            print("\n[警告] 发现以下外部项目名称引用：")
            print(result.stdout)
            return False
        else:
            # 没有找到引用
            print("\n[✓] 未发现外部项目名称引用")
            return True
            
    except FileNotFoundError:
        # grep 命令不可用（Windows），使用 Python 搜索
        print("\n[信息] 使用 Python 搜索...")
        return check_with_python(src_path)


def check_with_python(src_path):
    """使用 Python 进行搜索"""
    found_references = []
    
    for file_path in src_path.rglob("*.py"):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # 搜索 frigate（不区分大小写）
                import re
                matches = re.finditer(r'frigate', content, re.IGNORECASE)
                
                for match in matches:
                    line_num = content[:match.start()].count('\n') + 1
                    found_references.append(f"{file_path}:{line_num}")
                    
        except Exception as e:
            print(f"[警告] 无法读取文件 {file_path}: {e}")
    
    if found_references:
        print("\n[警告] 发现以下外部项目名称引用：")
        for ref in found_references:
            print(f"  - {ref}")
        return False
    else:
        print("\n[✓] 未发现外部项目名称引用")
        return True


def check_project_identity():
    """检查项目标识"""
    print("\n" + "=" * 60)
    print("检查项目标识")
    print("=" * 60)
    
    # 检查版本字符串
    ffmpeg_presets = Path("d:/sagesight-ai/src/utils/ffmpeg_presets.py")
    
    if ffmpeg_presets.exists():
        try:
            with open(ffmpeg_presets, 'r', encoding='utf-8') as f:
                content = f.read()
                
                if "SageSight" in content:
                    print("[✓] 版本字符串使用正确：SageSight")
                else:
                    print("[警告] 版本字符串可能不正确")
                    
        except Exception as e:
            print(f"[错误] 无法检查版本字符串: {e}")


def main():
    """运行所有检查"""
    print("\n" + "=" * 60)
    print("项目名称清理验证")
    print("=" * 60)
    
    # 检查源代码
    code_clean = check_frigate_references()
    
    # 检查项目标识
    check_project_identity()
    
    # 总结
    print("\n" + "=" * 60)
    if code_clean:
        print("✓ 项目名称清理验证通过")
        print("\n项目已完全独立，无外部项目名称引用")
    else:
        print("✗ 项目名称清理验证失败")
        print("\n请清理上述外部项目名称引用")
    print("=" * 60)


if __name__ == '__main__':
    main()