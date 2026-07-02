#!/usr/bin/env python3
"""找到并分析get_host_addresses函数"""

import aioice.ice
import inspect

# 查找get_host_addresses函数
print("=" * 80)
print("查找get_host_addresses函数:")
print("=" * 80)

# 在aioice模块中查找
import aioice

if hasattr(aioice, 'get_host_addresses'):
    func = aioice.get_host_addresses
    print(f"找到get_host_addresses函数")
    print(f"源码文件: {inspect.getfile(func)}")
    print(f"源码行号: {inspect.getsourcelines(func)[1]}")
    
    # 获取完整源码
    try:
        source_lines = inspect.getsourcelines(func)[0]
        print(f"\n完整源码:")
        for i, line in enumerate(source_lines):
            print(f"  {i+1}: {line.rstrip()}")
    except Exception as e:
        print(f"无法获取源码: {e}")
    
    # 测试调用这个函数
    print("\n测试调用get_host_addresses:")
    try:
        addresses_ipv4 = func(use_ipv4=True, use_ipv6=False)
        print(f"IPv4地址列表: {addresses_ipv4}")
        
        addresses_ipv6 = func(use_ipv4=False, use_ipv6=True)
        print(f"IPv6地址列表: {addresses_ipv6}")
        
        addresses_all = func(use_ipv4=True, use_ipv6=True)
        print(f"所有地址列表: {addresses_all}")
    except Exception as e:
        print(f"调用失败: {e}")
        
elif hasattr(aioice.ice, 'get_host_addresses'):
    func = aioice.ice.get_host_addresses
    print(f"在aioice.ice模块中找到get_host_addresses函数")
    print(f"源码文件: {inspect.getfile(func)}")
    print(f"源码行号: {inspect.getsourcelines(func)[1]}")
    
    # 获取完整源码
    try:
        source_lines = inspect.getsourcelines(func)[0]
        print(f"\n完整源码:")
        for i, line in enumerate(source_lines):
            print(f"  {i+1}: {line.rstrip()}")
    except Exception as e:
        print(f"无法获取源码: {e}")
    
    # 测试调用这个函数
    print("\n测试调用get_host_addresses:")
    try:
        addresses_ipv4 = func(use_ipv4=True, use_ipv6=False)
        print(f"IPv4地址列表: {addresses_ipv4}")
        
        addresses_ipv6 = func(use_ipv4=False, use_ipv6=True)
        print(f"IPv6地址列表: {addresses_ipv6}")
        
        addresses_all = func(use_ipv4=True, use_ipv6=True)
        print(f"所有地址列表: {addresses_all}")
    except Exception as e:
        print(f"调用失败: {e}")

else:
    print("未找到get_host_addresses函数")
    
    # 尝试查看aioice的所有模块内容
    print("\n查看aioice模块的所有内容:")
    for attr_name in dir(aioice):
        if not attr_name.startswith('_'):
            attr = getattr(aioice, attr_name)
            if callable(attr):
                print(f"  {attr_name}: {type(attr)}")
    
    print("\n查看aioice.ice模块的所有内容:")
    for attr_name in dir(aioice.ice):
        if not attr_name.startswith('_'):
            attr = getattr(aioice.ice, attr_name)
            if callable(attr):
                print(f"  {attr_name}: {type(attr)}")

print("\n诊断完成!")