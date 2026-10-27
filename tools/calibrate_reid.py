"""校准 ReID 特征中心化均值

为什么需要这一步：
    ImageNet 主干输出的特征里有一大块"所有画面共享"的共模成分 ——
    不管画面里是人是车，特征都指向同一个方向。实测未中心化时，
    16 张互不相关的监控画面余弦相似度均值 0.849、最低还有 0.728，
    等于完全无法区分目标；减去均值后不同画面掉到 -0.037，
    可分间距从 0.15 拉到 1.04。

    均值必须在**目标裁剪图**的分布上估计（不是整帧），因为推理时喂给
    模型的是按检测框裁出来的目标小图，两者的色彩/构图统计差别很大。

用法：
    python tools/calibrate_reid.py                      # 用最近 300 条事件图校准
    python tools/calibrate_reid.py --limit 600
    python tools/calibrate_reid.py --images some/dir    # 无数据库时退化为中心裁剪

输出：
    models/reid/reid_resnet18.mu.npz（与本项目模型同名的 .mu.npz）
    含 mu / hash / count 三个键，reid.py 会自动加载。

注意：
    均值变化会让新特征与旧特征不可比。校准完成后必须重建一次
    （系统配置 → 跨相机跟踪 → 重建，勾选"重算外观特征"），
    否则新旧特征混在一起时，匹配器会判定不可比并退化为纯时空关联。
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tracking.cross_camera import features as feat_mod  # noqa: E402
from src.tracking.cross_camera.reid import get_backend, _hash_mean  # noqa: E402

_DB_PATH = PROJECT_ROOT / 'data' / 'tasks.db'
_IMAGE_DIR = PROJECT_ROOT / 'output' / 'images'


def _load_image(path: Path):
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if img is not None else None
    except Exception:
        return None


def _parse_bbox(value):
    if not value:
        return None
    try:
        data = json.loads(value) if isinstance(value, str) else list(value)
        return [float(v) for v in data[:4]] if len(data) >= 4 else None
    except Exception:
        return None


def collect_from_db(limit: int):
    """从 events 表取 (图片, bbox, 原图宽, 原图高)"""
    if not _DB_PATH.exists():
        return []
    samples = []
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cols = {r[1] for r in conn.execute('PRAGMA table_info(events)')}
        has_size = 'image_width' in cols and 'image_height' in cols
        sql = ("SELECT event_id, bbox" +
               (", image_width, image_height" if has_size else ", 0, 0") +
               " FROM events WHERE status = 'final' ORDER BY id DESC LIMIT ?")
        rows = conn.execute(sql, (limit * 4,)).fetchall()
        conn.close()
    except Exception as e:
        print(f'[warn] 读数据库失败: {e}')
        return []

    for row in rows:
        event_id, bbox = row[0], row[1]
        orig_w, orig_h = (row[2] or 0), (row[3] or 0)
        img_path = _IMAGE_DIR / f'{event_id}.jpg'
        if not img_path.exists():
            continue
        box = _parse_bbox(bbox)
        if box is None:
            continue
        samples.append((img_path, box, int(orig_w), int(orig_h)))
        if len(samples) >= limit * 2:
            break
    return samples


def collect_from_dir(image_dir: Path, limit: int):
    """没有 bbox 时用画面中心区域近似（略差，但比用整帧好）"""
    samples = []
    for path in sorted(image_dir.glob('*.jpg'))[-limit:]:
        samples.append((path, None, 0, 0))
    return samples


def build_crops(samples, min_px: int):
    """按与线上完全相同的换算规则裁剪，保证均值分布和推理输入一致"""
    crops = []
    for path, box, orig_w, orig_h in samples:
        img = _load_image(path)
        if img is None:
            continue
        if box is None:
            h, w = img.shape[:2]
            ch, cw = int(h * 0.5), int(w * 0.35)
            y, x = (h - ch) // 2, (w - cw) // 2
            crop = img[y:y + ch, x:x + cw]
        else:
            fitted, inset = feat_mod.fit_bbox_to_image(box, img.shape, orig_w, orig_h)
            crop = feat_mod.crop_target(img, fitted, inset=inset)
        if crop is None:
            continue
        h, w = crop.shape[:2]
        if min(h, w) < min_px:
            continue
        crops.append(crop)
    return crops


def evaluate(backend, crops, mu):
    """对比校准前后的分离度：不同图之间的余弦应该显著下降"""
    feats = backend.embed(crops)
    if feats is None:
        return None
    sims = feats @ feats.T
    n = len(feats)
    off_raw = sims[~np.eye(n, dtype=bool)]

    if mu is None:
        return {'raw_mean': float(off_raw.mean()), 'raw_max': float(off_raw.max())}

    centered = feats - mu
    norms = np.linalg.norm(centered, axis=1, keepdims=True)
    norms[norms < 1e-6] = 1.0
    centered = centered / norms
    csims = centered @ centered.T
    off_c = csims[~np.eye(n, dtype=bool)]
    return {
        'raw_mean': float(off_raw.mean()), 'raw_max': float(off_raw.max()),
        'cen_mean': float(off_c.mean()), 'cen_max': float(off_c.max()),
        'cen_p95': float(np.percentile(off_c, 95)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='校准 ReID 特征中心化均值')
    parser.add_argument('--limit', type=int, default=300)
    parser.add_argument('--images', default='', help='图片目录（无数据库时的兜底来源）')
    parser.add_argument('--model', default='models/reid/reid_resnet18.onnx')
    parser.add_argument('--min-crop', type=int, default=32)
    args = parser.parse_args()

    model_path = PROJECT_ROOT / args.model
    if not model_path.exists():
        print(f'模型不存在: {model_path}\n先跑 tools/export_reid_onnx.py')
        return 1

    samples = collect_from_db(args.limit)
    source = f'events 表 + {_IMAGE_DIR.name}/'
    if not samples and args.images:
        samples = collect_from_dir(Path(args.images), args.limit)
        source = f'目录 {args.images}（中心裁剪近似）'
    if not samples:
        print('没有可用样本：数据库里没有带 bbox 的最终事件，也没有指定 --images')
        return 1

    print(f'样本来源: {source}，候选 {len(samples)} 条')
    crops = build_crops(samples, args.min_crop)
    if len(crops) < 10:
        print(f'可用裁剪图只有 {len(crops)} 张，太少（至少 10 张）')
        return 1
    print(f'有效裁剪图 {len(crops)} 张')

    backend = get_backend()
    backend.configure(str(model_path), mean_path='__none__')  # 用原始特征算均值
    if not backend.ready:
        print('模型加载失败')
        return 1

    before = evaluate(backend, crops, None)
    print(f'校准前: 不同图余弦 均值 {before["raw_mean"]:.3f} 最大 {before["raw_max"]:.3f}')

    feats = backend.embed(crops)
    if feats is None:
        print('特征提取失败')
        return 1
    mu = feats.mean(axis=0, keepdims=True).astype(np.float32)

    after = evaluate(backend, crops, mu)
    print(f'校准后: 不同图余弦 均值 {after["cen_mean"]:.3f} 最大 {after["cen_max"]:.3f} '
          f'P95 {after["cen_p95"]:.3f}')

    drop = before['raw_mean'] - after['cen_mean']
    if drop < 0.2:
        print(f'[warn] 分离度提升只有 {drop:.3f}，样本可能过于单一（同一场景/同一目标）')

    out_path = model_path.with_suffix('.mu.npz')
    np.savez(out_path, mu=mu, hash=np.array(_hash_mean(mu), dtype=np.int64),
             count=np.array(len(crops), dtype=np.int64),
             created_at=np.array(time.strftime('%Y-%m-%d %H:%M:%S')))
    print(f'已写入 {out_path}')
    print('提示：均值变了，旧特征与新特征不可比。请在系统配置里执行一次'
          '「重建」并勾选"重算外观特征"，让历史事件用新均值重新提取。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
