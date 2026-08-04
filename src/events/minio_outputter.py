"""
MinIO 输出器

负责将图片上传到 MinIO 存储服务器
"""

from typing import Dict
from minio import Minio
from datetime import timedelta
from urllib.parse import urlparse, unquote
import io
import logging
import urllib3

logger = logging.getLogger(__name__)


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
        # 连接/读取超时（秒）：MinIO 客户端默认 connect timeout=300s，服务不可达时
        # 会长时间阻塞调用线程（事件消费主循环），导致事件队列积压、检测帧被丢弃。
        # 显式设短超时 + 少量重试，失败即跳过，保障主循环持续运转。
        self.connect_timeout = config.get('connect_timeout', 3)
        self.read_timeout = config.get('read_timeout', 10)
        max_retries = config.get('max_retries', 2)
        # 连接池大小：多相机并发上传 GIF/图片时需要足够连接槽位
        # urllib3 默认 maxsize=1，并发请求会导致连接被频繁丢弃重建
        pool_maxsize = config.get('pool_maxsize', 20)

        # 构造带超时的 urllib3 连接池交给 MinIO 客户端
        http_client = urllib3.PoolManager(
            maxsize=pool_maxsize,
            timeout=urllib3.Timeout(
                connect=self.connect_timeout,
                read=self.read_timeout,
            ),
            retries=urllib3.Retry(
                total=max_retries,
                backoff_factor=0.1,
                status_forcelist=[500, 502, 503, 504],
            ),
        )

        # 创建 MinIO 客户端
        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
            http_client=http_client,
        )
        
        # 确保 bucket 存在
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.debug(f"[MinIOOutputter] 创建 Bucket: {self.bucket}")
            logger.debug(f"[MinIOOutputter] 已连接到 {self.endpoint}")
        except Exception as e:
            logger.debug(f"[MinIOOutputter] 初始化失败: {e}")
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
        
        logger.debug(f"[MinIOOutputter] 图片已上传: {object_name}")
        return url
    
    def delete(self, url: str) -> bool:
        """
        从 MinIO 删除对象

        支持两种输入：
        - 完整 presigned URL（http://endpoint/{bucket}/{object_name}?X-Amz-...）
        - 纯 object_name（如 "event_id.jpg"，使用配置的 bucket）

        Args:
            url: presigned URL 或 object_name

        Returns:
            bool: 是否删除成功（对象不存在视为已删除）
        """
        try:
            # 解析 URL 提取 bucket 和 object_name
            if url.startswith('http'):
                parsed = urlparse(url)
                # path 格式: /{bucket}/{object_name}
                path = unquote(parsed.path).lstrip('/')
                parts = path.split('/', 1)
                if len(parts) < 2 or not parts[0] or not parts[1]:
                    logger.warning(f"[MinIOOutputter] 无法从URL解析bucket和object_name: {url}")
                    return False
                bucket = parts[0]
                object_name = parts[1]
            else:
                # 纯 object_name，使用配置的 bucket
                bucket = self.bucket
                object_name = url

            self.client.remove_object(bucket, object_name)
            logger.debug(f"[MinIOOutputter] 已删除对象: {bucket}/{object_name}")
            return True
        except Exception as e:
            logger.warning(f"[MinIOOutputter] 删除对象失败 url={url}: {e}")
            return False

    def close(self):
        """关闭连接（MinIO 客户端不需要显式关闭）"""
        logger.debug(f"[MinIOOutputter] 已关闭")
