"""
FTP 输出器

负责将图片上传到 FTP 服务器
"""

import ftplib
import io
from typing import Dict
from urllib.parse import urlparse, unquote
import logging

logger = logging.getLogger(__name__)


class FTPOutputter:
    """FTP 图片输出器"""
    
    def __init__(self, config: Dict):
        """
        初始化 FTP 输出器

        Args:
            config: FTP 配置字典
        """
        self.host = config.get('host', 'localhost')
        self.port = config.get('port', 21)
        self.username = config.get('username', 'ftpuser')
        self.password = config.get('password', 'ftppassword')
        self.remote_dir = config.get('remote_dir', '/sagesight/images')
        self.passive_mode = config.get('passive_mode', True)
        # 连接/读写超时（秒）：ftplib 默认无超时，服务器不可达时会阻塞调用线程
        # （事件消费主循环）数十秒至数分钟，导致事件队列积压、检测帧被丢弃。
        # 显式设短超时，失败即抛异常跳过，保障主循环持续运转。
        self.timeout = config.get('timeout', 10)

        logger.debug(f"[FTPOutputter] 配置完成 - 服务器: {self.host}:{self.port}, 超时: {self.timeout}s")
    
    def _ensure_directory(self, ftp: ftplib.FTP):
        """
        确保远程目录存在
        
        Args:
            ftp: FTP 连接对象
        """
        dirs = self.remote_dir.strip('/').split('/')
        current_dir = ''
        
        for dir_name in dirs:
            current_dir += '/' + dir_name
            try:
                ftp.cwd(current_dir)
            except ftplib.error_perm:
                # 目录不存在，创建目录
                ftp.mkd(current_dir)
                ftp.cwd(current_dir)
    
    def upload(self, event_id: str, image_bytes: bytes, content_type: str = 'image/jpeg') -> str:
        """
        上传图片到 FTP 服务器

        Args:
            event_id: 事件 ID（或文件名，如 "event_123.jpg" 或 "event_456.gif"）
            image_bytes: 图片字节数据
            content_type: 内容类型（默认 'image/jpeg'，FTP 不使用此参数，仅为接口统一）

        Returns:
            FTP 访问路径
        """
        # 如果 event_id 已包含扩展名，直接使用；否则添加 .jpg
        if '.' not in event_id:
            filename = f"{event_id}.jpg"
        else:
            filename = event_id
        
        with ftplib.FTP() as ftp:
            # 连接到服务器（带超时，避免不可达时长时间阻塞调用线程）
            ftp.connect(self.host, self.port, timeout=self.timeout)

            # 登录
            ftp.login(self.username, self.password)
            
            # 设置被动模式
            if self.passive_mode:
                ftp.set_pasv(True)
            
            # 确保目录存在
            self._ensure_directory(ftp)
            
            # 上传文件（使用 BytesIO）
            file_obj = io.BytesIO(image_bytes)
            ftp.storbinary(f"STOR {filename}", file_obj)
        
        # 构建 FTP 访问路径
        ftp_url = f"ftp://{self.username}:{self.password}@{self.host}:{self.port}{self.remote_dir}/{filename}"
        logger.debug(f"[FTPOutputter] 图片已上传: {filename}")
        
        return ftp_url
    
    def delete(self, url: str) -> bool:
        """
        从 FTP 服务器删除文件

        支持两种输入：
        - 完整 FTP URL（ftp://user:pass@host:port/{path}/{filename}）
        - 纯 filename（使用配置的 remote_dir 拼接路径）

        Args:
            url: FTP URL 或 filename

        Returns:
            bool: 是否删除成功（文件不存在视为已删除）
        """
        try:
            # 解析 URL 提取文件路径
            if url.startswith('ftp://'):
                parsed = urlparse(url)
                file_path = unquote(parsed.path)
            else:
                # 纯 filename，拼接 remote_dir
                file_path = f"{self.remote_dir}/{url}"

            if not file_path:
                logger.warning(f"[FTPOutputter] 无法从URL解析文件路径: {url}")
                return False

            with ftplib.FTP() as ftp:
                ftp.connect(self.host, self.port, timeout=self.timeout)
                ftp.login(self.username, self.password)
                if self.passive_mode:
                    ftp.set_pasv(True)
                ftp.delete(file_path)

            logger.debug(f"[FTPOutputter] 已删除文件: {file_path}")
            return True
        except ftplib.error_perm as e:
            # 550: 文件不存在，视为已删除
            if '550' in str(e):
                logger.debug(f"[FTPOutputter] 文件不存在，视为已删除: {url}")
                return True
            logger.warning(f"[FTPOutputter] 删除文件失败 url={url}: {e}")
            return False
        except Exception as e:
            logger.warning(f"[FTPOutputter] 删除文件失败 url={url}: {e}")
            return False

    def close(self):
        """关闭连接（FTP 在 with 语句中自动关闭）"""
        logger.debug(f"[FTPOutputter] 已关闭")
