"""AI生成イラストの塗り面ノイズ除去（線は元画像のまま保護）

使い方:
  python denoise.py 入力.png 出力.png                  # 標準（L0スムージング）
  python denoise.py 入力.png 出力.png --lam 0.001      # 弱め（グラデーション・頬染めを残したい時）
  python denoise.py 入力.png 出力.png --lam 0.005      # 強め（ベタ塗り寄り）
  python denoise.py 入力.png 出力.png --mode soft      # 控えめ（小さな揺らぎだけ除去）
  python denoise.py 入力フォルダ 出力フォルダ          # フォルダ一括処理
  python denoise.py 入力.png 出力.png --strip-meta     # PNGメタデータを引き継がない
"""
import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, PngImagePlugin


DEFAULT_LAM = 0.002    # L0の強さ
DEFAULT_DARK = 90      # 線とみなす暗さ（これより暗い画素）
DEFAULT_GRAD = 120     # 線とみなす勾配の強さ


def line_mask(rgb, dark=DEFAULT_DARK, grad_th=DEFAULT_GRAD):
    """線（暗い画素＋強い勾配）を検出し、0〜1の重みで返す"""
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    grad = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    m = ((g < dark) | (grad > grad_th)).astype(np.uint8)
    m = cv2.dilate(m, np.ones((3, 3), np.uint8))               # アンチエイリアス部も保護
    return cv2.GaussianBlur(m.astype(np.float32), (3, 3), 0)   # 境目をなじませる


def smooth(rgb, mode='l0', lam=DEFAULT_LAM):
    """画像全体を平滑化する（線の保護は blend で行う）"""
    if mode == 'l0':   # 面を区分的に平坦化：ノイズ・色ムラに強い
        return cv2.ximgproc.l0Smooth(rgb, None, lam, 2.0)
    # 控えめ：小さな揺らぎだけ除去
    return cv2.ximgproc.rollingGuidanceFilter(rgb, d=-1, sigmaColor=10, sigmaSpace=3, numOfIter=4)


def blend(rgb, smoothed, mask):
    """線マスクの重みで、線は元画像・それ以外は平滑化結果を採用する"""
    w = mask[..., None]
    out = smoothed.astype(np.float32) * (1 - w) + rgb.astype(np.float32) * w
    return np.clip(out, 0, 255).astype(np.uint8)


def clean(rgb, mode='l0', lam=DEFAULT_LAM, dark=DEFAULT_DARK, grad_th=DEFAULT_GRAD):
    return blend(rgb, smooth(rgb, mode, lam), line_mask(rgb, dark, grad_th))


def load_image(src):
    """(元画像のメタデータ, RGB配列, アルファ配列 or None) を返す"""
    pil = Image.open(src)
    rgba = np.asarray(pil.convert('RGBA'))
    alpha = rgba[..., 3] if 'A' in pil.getbands() else None
    return dict(pil.info), np.ascontiguousarray(rgba[..., :3]), alpha


def save_png(out, alpha, meta, dst, strip_meta=False):
    """処理結果をPNGで保存する。meta は load_image が返したメタデータ"""
    if alpha is not None:
        out = np.dstack([out, alpha])
    info = PngImagePlugin.PngInfo()
    if not strip_meta:
        for k, v in meta.items():
            if isinstance(v, str):
                info.add_text(k, v)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(dst, pnginfo=info)


def process_file(src, dst, mode, lam, strip_meta, dark=DEFAULT_DARK, grad_th=DEFAULT_GRAD):
    meta, rgb, alpha = load_image(src)
    save_png(clean(rgb, mode, lam, dark, grad_th), alpha, meta, dst, strip_meta)
    print(f'{src} -> {dst}')


def main():
    ap = argparse.ArgumentParser(description='AI生成イラストの塗り面ノイズ除去')
    ap.add_argument('src', help='入力画像 or フォルダ')
    ap.add_argument('dst', help='出力画像 or フォルダ')
    ap.add_argument('--mode', choices=['l0', 'soft'], default='l0')
    ap.add_argument('--lam', type=float, default=DEFAULT_LAM, help='L0の強さ（大きいほどベタ寄り）')
    ap.add_argument('--strip-meta', action='store_true', help='PNGメタデータを引き継がない')
    a = ap.parse_args()

    src, dst = Path(a.src), Path(a.dst)
    if src.is_dir():
        for p in sorted(src.glob('*.png')):
            process_file(p, dst / f'{p.stem}_clean.png', a.mode, a.lam, a.strip_meta)
    else:
        process_file(src, dst, a.mode, a.lam, a.strip_meta)


if __name__ == '__main__':
    main()
