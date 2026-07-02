#!/usr/bin/env python3
"""诊断aioice内部结构，找到绑定逻辑的位置"""

import aioice.ice
import inspect

# 查看Connection类的关键方法
print("=" * 80)
print("aioice.ice.Connection类的方法:")
print("=" * 80)

for method_name in ['__init__', 'gather_candidates', 'connect', 'get_default_candidate']:
    if hasattr(aioice.ice.Connection, method_name):
        method = getattr(aioice.ice.Connection, method_name)
        print(f"\n方法: {method_name}")
        print(f"类型: {type(method)}")
        
        if hasattr(method, '__code__'):
            print(f"源码文件: {inspect.getfile(method)}")
            print(f"源码行号: {inspect.getsourcelines(method)[1]}")
            
            # 获取源码片段
            try:
                source_lines = inspect.getsourcelines(method)[0]
                print(f"源码片段（前20行）:")
                for i, line in enumerate(source_lines[:20]):
                    print(f"  {i+1}: {line.rstrip()}")
            except Exception as e:
                print(f"无法获取源码: {e}")

# 查看是否有ifaddr模块被使用
print("\n" + "=" * 80)
print("检查aioice是否使用ifaddr模块来获取网络接口:")
print("=" * 80)

try:
    import ifaddr
    print(f"✓ ifaddr模块已安装，位置: {ifaddr.__file__}")
    
    # 查看ifaddr获取的网络接口
    adapters = ifaddr.get_adapters()
    print(f"\n系统网络接口列表:")
    for adapter in adapters:
        print(f"  适配器: {adapter.nice_name}")
        for ip in adapter.ips:
            print(f"    IP: {ip.ip}, version: {ip.version}")
            if hasattr(ip, 'network_prefix'):
                print(f"    网络前缀: {ip.network_prefix}")
except ImportError:
    print("⚠️  ifaddr模块未安装")

# 检查aioice源码中的关键导入
print("\n" + "=" * 80)
print("检查aioice.ice模块的导入:")
print("=" * 80)

import aioice.ice as ice_module
print(f"aioice.ice模块源码文件: {inspect.getfile(ice_module)}")

# 查看源码文件的关键导入和函数
try:
    with open(inspect.getfile(ice_module), 'r') as f:
        content = f.read()
        print(f"\n文件大小: {len(content)} bytes")
        
        # 查找关键的导入和函数定义
        lines = content.split('\n')
        print("\n关键导入语句:")
        for line in lines[:50]:  # 查看前50行
            if line.startswith('import ') or line.startswith('from ') or 'ifaddr' in line or 'get_adapters' in line:
                print(f"  {line}")
                
        print("\n查找gather相关的函数/方法:")
        for i, line in enumerate(lines):
            if 'def gather' in line or 'async def gather' in line or 'bind' in line:
                print(f"  行 {i+1}: {line.rstrip()}")
                # 显示上下文5行
                for j in range(max(0, i-2), min(len(lines), i+3)):
                    print(f"    {j+1}: {lines[j].rstrip()}")
                print()
                
except Exception as e:
    print(f"无法读取源码文件: {e}")

print("\n诊断完成!")