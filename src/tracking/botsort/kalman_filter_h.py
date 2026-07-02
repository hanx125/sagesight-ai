"""
改进的卡尔曼滤波器（BoT-SORT）

关键改进：状态向量使用 h_ratio（高度/宽度比）而不是 h（高度）
原因：行人/车辆姿态变化导致宽高比波动，传统 KF 预测框会形变失真

状态向量：[x, y, w, h_ratio, vx, vy, vw, vh_ratio]
观测向量：[x, y, w, h_ratio]

参考：BoT-SORT 论文
"""

import numpy as np


class KalmanFilterH:
    """改进的卡尔曼滤波器（使用 h_ratio）"""
    
    def __init__(self, bbox: np.ndarray):
        """
        初始化卡尔曼滤波
        
        Args:
            bbox: 初始边界框 [cx, cy, w, h]（中心坐标 + 宽高）
        """
        # 状态向量：[x, y, w, h_ratio, vx, vy, vw, vh_ratio]
        # 前 4 个是位置（x, y, w, h_ratio），后 4 个是速度
        self.state = np.zeros(8)
        
        # 初始化位置
        cx, cy, w, h = bbox
        h_ratio = h / w if w > 0 else 1.0  # 高度/宽度比
        
        self.state[0] = cx          # x
        self.state[1] = cy          # y
        self.state[2] = w           # w
        self.state[3] = h_ratio     # h_ratio
        self.state[4:8] = 0         # 速度初始化为 0
        
        # 状态转移矩阵（dt=1）
        self.F = np.eye(8)
        self.F[0:4, 4:8] = np.eye(4)  # 位置 += 速度
        
        # 观测矩阵（只观测位置，不观测速度）
        self.H = np.zeros((4, 8))
        self.H[0:4, 0:4] = np.eye(4)
        
        # 过程噪声协方差 Q
        # 位置噪声小，速度噪声大
        self.Q = np.eye(8)
        self.Q[0:4, 0:4] *= 1.0      # 位置噪声
        self.Q[4:8, 4:8] *= 10.0     # 速度噪声
        
        # 观测噪声协方差 R
        self.R = np.eye(4) * 1.0
        
        # 状态协方差矩阵 P
        self.P = np.eye(8) * 10.0
        self.P[4:8, 4:8] *= 1000.0   # 速度不确定性大
    
    def predict(self) -> np.ndarray:
        """
        预测下一帧状态
        
        Returns:
            预测的边界框 [cx, cy, w, h]
        """
        # 状态预测：x' = F * x
        self.state = self.F @ self.state
        
        # 协方差预测：P' = F * P * F^T + Q
        self.P = self.F @ self.P @ self.F.T + self.Q
        
        # 返回预测的边界框（从 h_ratio 计算 h）
        cx, cy, w, h_ratio = self.state[0:4]
        h = w * h_ratio
        
        return np.array([cx, cy, w, h])
    
    def update(self, bbox: np.ndarray) -> np.ndarray:
        """
        更新状态（使用观测值）
        
        Args:
            bbox: 观测边界框 [cx, cy, w, h]
        
        Returns:
            更新后的边界框 [cx, cy, w, h]
        """
        # 从 bbox 构造观测向量 [cx, cy, w, h_ratio]
        cx, cy, w, h = bbox
        h_ratio = h / w if w > 0 else 1.0
        z = np.array([cx, cy, w, h_ratio])
        
        # 卡尔曼增益：K = P * H^T * (H * P * H^T + R)^-1
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        
        # 状态更新：x = x' + K * (z - H * x')
        y = z - self.H @ self.state  # 观测残差
        self.state = self.state + K @ y
        
        # 协方差更新：P = (I - K * H) * P'
        I = np.eye(8)
        self.P = (I - K @ self.H) @ self.P
        
        # 返回更新后的边界框
        cx, cy, w, h_ratio = self.state[0:4]
        h = w * h_ratio
        
        return np.array([cx, cy, w, h])
    
    def get_state(self) -> np.ndarray:
        """
        获取当前状态
        
        Returns:
            当前边界框 [cx, cy, w, h]
        """
        cx, cy, w, h_ratio = self.state[0:4]
        h = w * h_ratio
        
        return np.array([cx, cy, w, h])
    
    def get_velocity(self) -> np.ndarray:
        """
        获取速度
        
        Returns:
            速度 [vx, vy, vw, vh_ratio]
        """
        return self.state[4:8].copy()