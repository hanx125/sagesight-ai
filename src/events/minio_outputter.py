"""
MinIO 输出器

负责将图片上传到 MinIO 存储服务器
"""

from typing import Dict
from minio import Minio
from datetime import timedelta
import io


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
    
    def upload(self, event_id: str, image_bytes: bytes, content_type: str = 'image/jpeg') -> str:
        """
        上传图片到 MinIO

        Args:
            event_id: 事件 ID（或文件名，如 "event_123.jpg" 或 "event_456.gif"）
            image_bytes: 图片字节数据
            content_type: 内容类型（默认 'image/jpeg'，GIF 为 'image/gif'）

        Returns:
            预签名访问 URL
        """
        # 如果 event_id 已包含扩展名，直接使用；否则添加 .jpg
        if '.' not in event_id:
            object_name = f"{event_id}.jpg"
        else:
            object_name = event_id
        
        # 上传对象（MinIO put_object 需要文件类对象，用 BytesIO 包装 bytes）
        self.client.put_object(
            self.bucket,
            object_name,
            io.BytesIO(image_bytes),
            len(image_bytes),
            content_type=content_type
        )
        
        # 生成预签名 URL（expires 需要 timedelta 类型）
        url = self.client.presigned_get_object(
            self.bucket,
            object_name,
            expires=timedelta(seconds=self.presigned_expires)
        )
        
        print(f"[MinIOOutputter] 图片已上传: {object_name}")
        return url
    
    def close(self):
        """关闭连接（MinIO 客户端不需要显式关闭）"""
        print(f"[MinIOOutputter] 已关闭")
