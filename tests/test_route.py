import sys
sys.path.insert(0, 'e:/sagesight-AI')

from web_app import app

# 测试路由
with app.test_client() as client:
    print("测试路由: /")
    resp = client.get('/')
    print(f"状态码: {resp.status_code}")
    if resp.status_code == 200:
        print(f"内容预览: {resp.data[:200]}")
    else:
        print(f"错误: {resp.data[:200]}")
    
    print("\n测试路由: /api/stats")
    resp = client.get('/api/stats')
    print(f"状态码: {resp.status_code}")
    if resp.status_code == 200:
        print(f"内容: {resp.data}")
    else:
        print(f"错误: {resp.data[:200]}")