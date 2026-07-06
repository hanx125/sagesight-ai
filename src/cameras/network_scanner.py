"""
网络端口扫描器

用于扫描指定IP范围和端口的ONVIF摄像机。
解决跨网段的ONVIF设备发现问题。
"""

import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional
from ipaddress import ip_address, ip_network

logger = logging.getLogger(__name__)

# 尝试导入ONVIF库
try:
    from onvif import ONVIFCamera, ONVIFError
    ONVIF_AVAILABLE = True
except ImportError:
    ONVIF_AVAILABLE = False
    logger.warning("onvif库未安装，ONVIF功能不可用。请安装: pip install onvif")


class NetworkScanner:
    """网络端口扫描器"""
    
    # ONVIF常用端口
    DEFAULT_ONVIF_PORTS = [80, 8080, 443, 8443]
    
    def __init__(self, max_workers=10, timeout=3):
        """
        初始化网络扫描器

        Args:
            max_workers: 最大并发线程数（建议10-15，避免扫描时占用过多CPU）
            timeout: 连接超时时间（秒）
        """
        self.max_workers = max_workers
        self.timeout = timeout
        self._stop_flag = False
    
    def stop(self):
        """停止扫描"""
        self._stop_flag = True
    
    def parse_ip_range(self, ip_range: str) -> List[str]:
        """
        解析IP范围
        
        Args:
            ip_range: IP范围字符串，支持多种格式：
                - 单个IP: "192.168.1.100"
                - IP范围: "192.168.1.1-192.168.1.255"
                - CIDR格式: "192.168.1.0/24"
                - 网段简写: "192.168.1" (会自动扫描192.168.1.1-192.168.1.255)
        
        Returns:
            IP地址列表
        """
        try:
            # 单个IP
            if '-' not in ip_range and '/' not in ip_range and ip_range.count('.') == 3:
                return [ip_range]
            
            # CIDR格式
            if '/' in ip_range:
                network = ip_network(ip_range, strict=False)
                return [str(host) for host in network.hosts()]
            
            # IP范围 (例如: 192.168.1.1-192.168.1.255)
            if '-' in ip_range:
                start_ip, end_ip = ip_range.split('-')
                start = int(ip_address(start_ip))
                end = int(ip_address(end_ip))
                return [str(ip_address(i)) for i in range(start, end + 1)]
            
            # 网段简写 (例如: 192.168.1)
            if ip_range.count('.') < 3:
                # 补全为网段
                if ip_range.count('.') == 2:
                    base = ip_range
                    return [f"{base}.{i}" for i in range(1, 256)]
                elif ip_range.count('.') == 1:
                    base = ip_range
                    return [f"{base}.{j}.{i}" for j in range(0, 256) for i in range(1, 256)]
            
            return [ip_range]
            
        except Exception as e:
            logger.error(f"解析IP范围失败: {e}")
            return []
    
    def parse_ports(self, ports_str: str) -> List[int]:
        """
        解析端口列表
        
        Args:
            ports_str: 端口字符串，支持多种格式：
                - 单个端口: "80"
                - 端口列表: "80,8080,443"
                - 端口范围: "80-100"
        
        Returns:
            端口列表
        """
        try:
            # 处理空字符串
            if not ports_str or not ports_str.strip():
                return self.DEFAULT_ONVIF_PORTS
            
            ports = []
            
            # 端口列表
            if ',' in ports_str:
                for port in ports_str.split(','):
                    port = port.strip()
                    # 跳过空字符串
                    if not port:
                        continue
                    
                    if '-' in port:
                        # 端口范围
                        start, end = port.split('-')
                        start = start.strip()
                        end = end.strip()
                        if start and end:
                            ports.extend(range(int(start), int(end) + 1))
                    else:
                        ports.append(int(port))
            
            # 端口范围
            elif '-' in ports_str:
                start, end = ports_str.split('-')
                start = start.strip()
                end = end.strip()
                if start and end:
                    ports = list(range(int(start), int(end) + 1))
            
            # 单个端口
            else:
                port = ports_str.strip()
                if port:
                    ports = [int(port)]
            
            # 如果解析结果为空，返回默认端口
            if not ports:
                return self.DEFAULT_ONVIF_PORTS
            
            return sorted(set(ports))
            
        except Exception as e:
            logger.error(f"解析端口失败: {e}")
            return self.DEFAULT_ONVIF_PORTS
    
    def check_port(self, ip: str, port: int) -> bool:
        """
        检查端口是否开放
        
        Args:
            ip: IP地址
            port: 端口号
        
        Returns:
            端口是否开放
        """
        if self._stop_flag:
            return False
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"端口检查失败 {ip}:{port} - {e}")
            return False
    
    def check_onvif_port(self, ip: str, port: int, username: str = '', password: str = '') -> Optional[Dict]:
        """
        检查端口是否为ONVIF设备
        
        采用两步检测策略：
        1. 先检测端口是否开放
        2. 如果端口开放，尝试无认证ONVIF连接
        3. 如果ONVIF连接失败（需要认证），仍然返回端口开放信息
        
        Args:
            ip: IP地址
            port: 端口号
            username: 用户名（可选，扫描阶段通常为空）
            password: 密码（可选，扫描阶段通常为空）
        
        Returns:
            设备信息字典，如果不是ONVIF设备则返回None
        """
        if self._stop_flag:
            return None
        
        # 第一步：检测端口是否开放
        if not self.check_port(ip, port):
            return None
        
        # 第二步：尝试ONVIF连接（无认证）
        if not ONVIF_AVAILABLE:
            # ONVIF库未安装，但端口开放，返回基本信息
            return {
                'ip': ip,
                'port': port,
                'name': f'Camera {ip}:{port}',
                'onvif': False,
                'port_open': True
            }
        
        try:
            # 尝试连接ONVIF设备（无认证）
            camera = ONVIFCamera(ip, port, username, password, adjust_time=False, no_cache=True)
            
            # 获取设备信息
            dev_info = camera.devicemgmt.GetDeviceInformation()
            
            # 获取媒体服务
            media_service = camera.create_media_service()
            profiles = media_service.GetProfiles()
            
            if profiles:
                return {
                    'ip': ip,
                    'port': port,
                    'name': dev_info.Model or f"ONVIF Camera {ip}",
                    'manufacturer': dev_info.Manufacturer,
                    'model': dev_info.Model,
                    'firmware_version': dev_info.FirmwareVersion,
                    'serial_number': dev_info.SerialNumber,
                    'onvif': True,
                    'need_auth': False  # 不需要认证（或无认证成功）
                }
            
        except Exception as e:
            logger.debug(f"ONVIF无认证连接失败 {ip}:{port} - {e}")
            
            # ONVIF连接失败，但端口开放，可能是需要认证的ONVIF设备
            return {
                'ip': ip,
                'port': port,
                'name': f'ONVIF Camera {ip}:{port}',
                'onvif': True,
                'need_auth': True,  # 需要认证
                'port_open': True
            }
        
        return None

    def scan_network(
        self,
        ip_range: str,
        ports: str = "80,8080",
        username: str = '',
        password: str = '',
        check_onvif: bool = True,
        progress_callback=None
    ) -> List[Dict]:
        """
        扫描网络
        
        Args:
            ip_range: IP范围
            ports: 端口列表
            username: 用户名（可选）
            password: 密码（可选）
            check_onvif: 是否检查ONVIF协议
            progress_callback: 进度回调函数
        
        Returns:
            发现的设备列表
        """
        self._stop_flag = False
        
        # 解析IP列表
        ip_list = self.parse_ip_range(ip_range)
        if not ip_list:
            logger.error("无效的IP范围")
            return []
        
        # 解析端口列表
        port_list = self.parse_ports(ports)
        
        logger.info(f"开始扫描 {len(ip_list)} 个IP地址，端口: {port_list}")
        
        # 扫描结果
        devices = []
        scanned_count = 0
        total_count = len(ip_list) * len(port_list)
        
        # 使用线程池并发扫描
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 创建扫描任务
            futures = {}
            for ip in ip_list:
                for port in port_list:
                    if check_onvif:
                        future = executor.submit(
                            self.check_onvif_port, ip, port, username, password
                        )
                    else:
                        future = executor.submit(self.check_port, ip, port)
                    futures[future] = (ip, port)
            
            # 处理扫描结果
            for future in as_completed(futures):
                if self._stop_flag:
                    executor.shutdown(wait=False)
                    break
                
                ip, port = futures[future]
                try:
                    result = future.result()
                    
                    if result:
                        if check_onvif and isinstance(result, dict):
                            # ONVIF设备
                            devices.append(result)
                            logger.info(f"发现ONVIF设备: {ip}:{port} - {result.get('manufacturer', '')} {result.get('model', '')}")
                        elif not check_onvif and result:
                            # 开放端口
                            devices.append({
                                'ip': ip,
                                'port': port,
                                'onvif': False
                            })
                    
                except Exception as e:
                    logger.debug(f"扫描失败 {ip}:{port} - {e}")
                
                # 更新进度
                scanned_count += 1
                if progress_callback:
                    progress_callback(scanned_count, total_count)
        
        logger.info(f"扫描完成，共发现 {len(devices)} 个设备")
        return devices


def scan_network_ports(
    ip_range: str,
    ports: str = "80,8080",
    username: str = '',
    password: str = '',
    check_onvif: bool = True,
    timeout: int = 3,
    progress_callback=None
) -> List[Dict]:
    """
    扫描网络端口（便捷函数）
    
    Args:
        ip_range: IP范围
        ports: 端口列表
        username: 用户名（可选）
        password: 密码（可选）
        check_onvif: 是否检查ONVIF协议
        timeout: 超时时间
        progress_callback: 进度回调函数
    
    Returns:
        发现的设备列表
    """
    scanner = NetworkScanner(timeout=timeout)
    return scanner.scan_network(
        ip_range, ports, username, password, check_onvif, progress_callback
    )