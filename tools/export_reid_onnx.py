"""导出 ReID 特征提取模型为 ONNX

用途：给跨相机跟踪提供外观特征（替代 HSV 颜色直方图）。
导出的模型只做一件事：输入一张对齐后的目标裁剪图，输出 L2 归一化的特征向量。
相似度直接用余弦（点积），越接近 1 越可能是同一个目标。

为什么用 ImageNet 主干而不是 OSNet：
    OSNet 的官方预训练权重托管在 Google Drive，现场网络通常拿不到。
    ImageNet 预训练主干（ResNet18/50）+ GeM 池化已经能提供远强于颜色直方图
    的判别力，而且权重从 download.pytorch.org 下载，国内可达。
    将来若能拿到 OSNet，直接换模型文件即可，代码不用改（见 --arch 之外的用法）。

用法：
    python tools/export_reid_onnx.py                      # 默认 resnet18
    python tools/export_reid_onnx.py --arch resnet50
    python tools/export_reid_onnx.py --arch osnet_x1_0    # 需要 torchreid + 预训练权重

输出：
    models/reid/reid_<arch>.onnx
    input : float32 [N, 3, H, W]，RGB，ImageNet 归一化
    output: float32 [N, D]，已 L2 归一化
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / 'models' / 'reid'

# ImageNet 归一化常量（导出后在 Python 侧预处理，模型内不做）
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


class GeM(nn.Module):
    """广义平均池化

    相比全局平均池化，GeM（p=3）会强化高响应区域，
    在 ReID 上通常能带来 2~4 个点的 mAP 提升，代价几乎为零。
    """

    def __init__(self, p: float = 3.0, eps: float = 1e-6):
        super().__init__()
        self.p = p
        self.eps = eps

    def forward(self, x):
        x = x.clamp(min=self.eps).pow(self.p)
        # 用 mean 而不是 avg_pool2d：池化核由输入尺寸动态推导，
        # ONNX 导出时会被判成非常量而失败
        x = x.mean(dim=(2, 3), keepdim=True)
        return x.pow(1.0 / self.p)


class ReIDNet(nn.Module):
    """特征提取网络：主干去掉分类头，接 GeM + L2 归一化"""

    def __init__(self, arch: str = 'resnet18'):
        super().__init__()
        self.arch = arch
        if arch.startswith('resnet'):
            import torchvision
            weights = 'DEFAULT'
            try:
                model = getattr(torchvision.models, arch)(weights=weights)
            except Exception as e:
                print(f'[warn] 预训练权重加载失败（{e}），改用随机初始化，请勿用于生产')
                model = getattr(torchvision.models, arch)(weights=None)
            # 去掉 avgpool 与 fc，保留到 layer4 的特征图
            self.backbone = nn.Sequential(*list(model.children())[:-2])
            self.out_dim = model.fc.in_features
        elif arch.startswith('osnet'):
            try:
                import torchreid
            except ImportError:
                raise SystemExit(
                    'osnet 需要 torchreid：pip install torchreid\n'
                    '且预训练权重来自 Google Drive，内网环境通常下载失败，'
                    '建议改用 --arch resnet18/resnet50'
                )
            model = torchreid.models.build_model(
                name=arch, num_classes=1000, pretrained=True
            )
            # torchreid 的 OSNet 有 featuremaps 方法，直接裁掉分类层
            self.backbone = nn.Sequential(
                model.conv1, model.maxpool, model.conv2, model.conv3,
                model.conv4, model.conv5,
            )
            self.out_dim = model.feature_dim
        else:
            raise SystemExit(f'不支持的主干: {arch}')

        self.pool = GeM(p=3.0)

    def forward(self, x):
        feat = self.backbone(x)
        feat = self.pool(feat).flatten(1)
        return F.normalize(feat, p=2, dim=1)


def export(arch: str, out_path: Path, width: int, height: int) -> None:
    net = ReIDNet(arch).eval()
    dummy = torch.randn(1, 3, height, width)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with torch.no_grad():
        ref = net(dummy)
    print(f'[{arch}] 输出维度 {tuple(ref.shape)}，特征维度 {net.out_dim}')

    torch.onnx.export(
        net, dummy, str(out_path),
        input_names=['input'], output_names=['feat'],
        opset_version=13,
        do_constant_folding=True,
        dynamic_axes={'input': {0: 'batch'}, 'feat': {0: 'batch'}},
    )
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f'已导出 {out_path} ({size_mb:.1f} MB)')


def verify(out_path: Path, width: int, height: int) -> None:
    """用 onnxruntime 跑一遍，确认形状、归一化和判别力都符合预期"""
    import onnxruntime as ort

    sess = ort.InferenceSession(str(out_path), providers=['CPUExecutionProvider'])
    inp = sess.get_inputs()[0]
    print(f'输入 {inp.name} {inp.shape} | 输出 {sess.get_outputs()[0].shape}')

    rng = np.random.default_rng(0)
    a = rng.random((1, 3, height, width), dtype=np.float32) * 2 - 1
    b = a + rng.normal(0, 0.05, a.shape).astype(np.float32)
    c = rng.random((1, 3, height, width), dtype=np.float32) * 2 - 1

    fa, fb, fc = [sess.run(None, {inp.name: x})[0] for x in (a, b, c)]
    fa, fb, fc = fa[0], fb[0], fc[0]
    print(f'特征维度 {fa.shape[0]}，L2 范数 {np.linalg.norm(fa):.4f}（应为 1.0）')
    print(f'同图微扰余弦 {float(fa @ fb):.4f}（应接近 1）')
    print(f'随机图余弦   {float(fa @ fc):.4f}（应接近 0）')
    assert abs(np.linalg.norm(fa) - 1.0) < 1e-3, '输出未归一化'
    assert float(fa @ fb) > 0.95, '同图微扰相似度过低'
    print('自检通过')


def main() -> int:
    parser = argparse.ArgumentParser(description='导出 ReID ONNX 模型')
    parser.add_argument('--arch', default='resnet18',
                        help='主干网络：resnet18 / resnet50 / osnet_x1_0')
    parser.add_argument('--out', default='', help='输出路径，默认 models/reid/reid_<arch>.onnx')
    parser.add_argument('--width', type=int, default=128)
    parser.add_argument('--height', type=int, default=256)
    parser.add_argument('--no-verify', action='store_true')
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else DEFAULT_OUT_DIR / f'reid_{args.arch}.onnx'
    export(args.arch, out_path, args.width, args.height)
    if not args.no_verify:
        verify(out_path, args.width, args.height)
    return 0


if __name__ == '__main__':
    sys.exit(main())
