"""
MinIO 输出器

负责将图片上传到 MinIO 存储服务器
"""

from typing import Dict
from minio import Minio


class MinIOOutputter:
    """MinIO 图片输出器"""
    
    def __init__(self, config: Dict):
        """
        初始化 MinIO 输出器
        
        Args:
            config: MinIO 配置字典
        """
        self.endpoint = config.get('endpoint', 'localhost:9000')
        self.access_key = config.get('access_key', 'minioadmin')
        self.secret_key = config.get('secret_key', 'minioadmin')
        self.bucket = config.get('bucket', 'sagesight-images')
        self.secure = config.get('secure', False)
        self.presigned_expires = config.get('presigned_expires', 3600)
        
        # 创建 MinIO 客户端
        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure
        )
        
        # 确保 bucket 存在
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                print(f"[MinIOOutputter] 创建 Bucket: {self.bucket}")
            print(f"[MinIOOutputter] 已连接到 {self.endpoint}")
        except Exception as e:
            print(f"[MinIOOutputter] 初始化失败: {e}")
            raise
    
    def upload(self, event_id: str, image_bytes: bytes) -> str:
        """
        上传图片到 MinIO
        
        Args:
            event_id: 事件 ID
            image_bytes: 图片字节数据
        
        Returns:
            预签名访问 URL
        """
        object_name = f"{event_id}.jpg"
        
        # 上传对象
        self.client.put_object(
            self.bucket,
            object_name,
            image_bytes,
            len(image_bytes),
            content_type='image/jpeg'
        )
        
        # 生成预签名 URL
        url = self.client.presigned_get_object(
            self.bucket,
            object_name,
            expires=self.presigned_expires
        )
        
        print(f"[MinIOOutputter] 图片已上传: {object_name}")
        return url
    
    def close(self):
        """关闭连接（MinIO 客户端不需要显式关闭）"""
        print(f"[MinIOOutputter] 已关闭")
