"""
FTP 输出器

负责将图片上传到 FTP 服务器
"""

import ftplib
import io
from typing import Dict


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
        
        print(f"[FTPOutputter] 配置完成 - 服务器: {self.host}:{self.port}")
    
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
    
    def upload(self, event_id: str, image_bytes: bytes) -> str:
        """
        上传图片到 FTP 服务器
        
        Args:
            event_id: 事件 ID
            image_bytes: 图片字节数据
        
        Returns:
            FTP 访问路径
        """
        filename = f"{event_id}.jpg"
        
        with ftplib.FTP() as ftp:
            # 连接到服务器
            ftp.connect(self.host, self.port)
            
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
        print(f"[FTPOutputter] 图片已上传: {filename}")
        
        return ftp_url
    
    def close(self):
        """关闭连接（FTP 在 with 语句中自动关闭）"""
        print(f"[FTPOutputter] 已关闭")
