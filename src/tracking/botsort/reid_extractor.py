"""
ReID 特征提取器

支持多种 ReID 模型：
- TorchReID（推荐，支持 torch 2.0+）
- OSNet（轻量级）

如果没有 ReID 模型，可以禁用外观特征，只使用运动特征（IoU）
"""

import numpy as np
import cv2
from typing import Optional, List
import logging

logger = logging.getLogger('DetectorWorker')


class ReIDExtractor:
    """ReID 特征提取器"""
    
    def __init__(self,
                 model_type: str = 'none',
                 model_path: Optional[str] = None,
                 device: str = 'cpu',
                 half: bool = False):
        """
        初始化 ReID 特征提取器
        
        Args:
            model_type: 模型类型 ('none', 'torchreid', 'osnet')
            model_path: 模型路径（如果为空，自动下载预训练模型）
            device: 设备 ('cpu', 'cuda')
            half: 是否使用半精度（FP16）
        """
        self.model_type = model_type
        self.model_path = model_path
        self.device = device
        self.half = half
        
        # ReID 模型
        self.model = None
        self.transform = None
        
        # 如果启用 ReID，加载模型
        if model_type != 'none':
            self._load_model()
    
    def _load_model(self):
        """加载 ReID 模型"""
        try:
            if self.model_type in ['torchreid', 'osnet']:
                # 使用 TorchReID（支持 torch 2.0+）
                import torch
                
                # 强制使用 CPU，避免加载 CUDA DLL
                if self.device == 'cuda':
                    logger.warning("检测到 CUDA 配置，但强制使用 CPU（避免 DLL 加载错误）")
                    self.device = 'cpu'
                
                import torchreid
                
                logger.info(f"加载 TorchReID 模型: {self.model_type} (device={self.device})")
                
                # 选择模型架构
                if self.model_type == 'osnet':
                    model_name = 'osnet_x0_75'  # 轻量级 OSNet
                else:
                    model_name = 'resnet50'     # ResNet50
                
                # 加载预训练模型（自动下载）
                self.model = torchreid.models.build_model(
                    name=model_name,
                    num_classes=1000,
                    pretrained=True  # 自动下载预训练权重
                )
                
                # 移动到指定设备（强制 CPU）
                self.model = self.model.to(self.device)
                
                # 设置为评估模式
                self.model.eval()
                
                # 创建数据预处理
                self.transform = torchreid.utils.transforms.build_transforms(
                    height=256,
                    width=128,
                    is_train=False
                )
                
                logger.info(f"[OK] TorchReID 模型已加载: {model_name} (device={self.device})")
                
            else:
                logger.warning(f"未知的 ReID 模型类型: {self.model_type}")
                
        except ImportError as e:
            logger.error(f"缺少依赖库: {e}")
            logger.error(f"请安装: pip install torchreid")
            self.model = None
            
        except Exception as e:
            logger.error(f"加载 ReID 模型失败: {e}")
            logger.error(f"建议: 设置 use_reid=false 或检查 CUDA 安装")
            self.model = None
    
    def extract(self,
                image: np.ndarray,
                bbox: np.ndarray) -> Optional[np.ndarray]:
        """
        提取外观特征
        
        Args:
            image: 图像（BGR 格式）
            bbox: 边界框 [x1, y1, x2, y2]
        
        Returns:
            外观特征向量（512 维），如果禁用 ReID 则返回 None
        """
        # 如果禁用 ReID，返回 None
        if self.model_type == 'none' or self.model is None:
            return None
        
        try:
            import torch
            
            # 提取 ROI
            x1, y1, x2, y2 = bbox
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            
            # 确保边界框在图像范围内
            h, w = image.shape[:2]
            x1 = max(0, min(x1, w - 1))
            y1 = max(0, min(y1, h - 1))
            x2 = max(0, min(x2, w - 1))
            y2 = max(0, min(y2, h - 1))
            
            # 提取 ROI
            roi = image[y1:y2, x1:x2]
            
            if roi.size == 0:
                return None
            
            # BGR -> RGB
            roi = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
            
            # 应用预处理
            roi_tensor = self.transform(roi)
            
            # 添加 batch 维度
            roi_tensor = roi_tensor.unsqueeze(0)
            
            # 移动到指定设备
            roi_tensor = roi_tensor.to(self.device)
            
            # 如果使用半精度
            if self.half and self.device == 'cuda':
                roi_tensor = roi_tensor.half()
            
            # 提取特征
            with torch.no_grad():
                # 提取特征向量
                features = self.model(roi_tensor)
                
                # L2 归一化
                features = torch.nn.functional.normalize(features, p=2, dim=1)
                
                # 转换为 numpy
                features = features.cpu().numpy().flatten()
            
            return features
            
        except Exception as e:
            logger.error(f"提取外观特征失败: {e}")
            return None
    
    def extract_batch(self,
                      image: np.ndarray,
                      bboxes: List[np.ndarray]) -> List[Optional[np.ndarray]]:
        """
        批量提取外观特征
        
        Args:
            image: 图像（BGR 格式）
            bboxes: 边界框列表 [[x1, y1, x2, y2], ...]
        
        Returns:
            外观特征向量列表
        """
        embeddings = []
        
        for bbox in bboxes:
            embedding = self.extract(image, bbox)
            embeddings.append(embedding)
        
        return embeddings
    
    def is_enabled(self) -> bool:
        """是否启用 ReID"""
        return self.model_type != 'none' and self.model is not None