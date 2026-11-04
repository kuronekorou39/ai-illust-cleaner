"""AI生成イラストの塗り面ノイズ除去（線は元画像のまま保護）

使い方:
  python -m cleaner.denoise 入力.png 出力.png                  # 標準（L0スムージング）
  python -m cleaner.denoise 入力.png 出力.png --lam 0.001      # 弱め（グラデーション・頬染めを残したい時）
  python -m cleaner.denoise 入力.png 出力.png --lam 0.005      # 強め（ベタ塗り寄り）
  python -m cleaner.denoise 入力.png 出力.png --mode soft      # 控えめ（小さな揺らぎだけ除去）
  python -m cleaner.denoise 入力フォルダ 出力フォルダ          # フォルダ一括処理
  python -m cleaner.denoise 入力.png 出力.png --strip-meta     # PNGメタデータを引き継がない
  python -m cleaner.denoise 入力.png 出力.png --cutout         # 単色の背景を透明にする
"""
import argparse
from pathlib import Path

from .cutout import DEFAULT_TOLERANCE, cutout, merge_alpha
from .metadata import PNG_XMP_KEY, strip

import cv2
import numpy as np
from PIL import Image, PngImagePlugin


DEFAULT_LAM = 0.002    # L0の強さ
DEFAULT_DARK = 90      # 線とみなす暗さ（これより暗い細い部分）
DEFAULT_GRAD = 120     # 線とみなす勾配の強さ
LINE_MAX_WIDTH = 9     # 暗い部分のうち、これより太いものは線でなく塗りとみなす（画素）
GRAD_PRESMOOTH = 3     # 勾配を測る前にノイズをならすメディアンの大きさ（ノイズを輪郭と間違えない）


def line_mask(rgb, dark=DEFAULT_DARK, grad_th=DEFAULT_GRAD):
    """線を検出し、0〜1の重みで返す

    線とみなすのは、暗い部分のうち細いもの（広い暗い面は塗りとして処理する）と、輪郭の強いところ。
    輪郭の強さはノイズをならしてから測る（JPEG などの強いノイズを輪郭と間違えない）
    """
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    smooth_g = cv2.medianBlur(g, GRAD_PRESMOOTH).astype(np.float32)
    grad = cv2.magnitude(cv2.Sobel(smooth_g, cv2.CV_32F, 1, 0), cv2.Sobel(smooth_g, cv2.CV_32F, 0, 1))
    dark_px = (g < dark).astype(np.uint8)
    wide = cv2.morphologyEx(dark_px, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (LINE_MAX_WIDTH, LINE_MAX_WIDTH)))
    m = ((dark_px & (1 - wide)) | (grad > grad_th)).astype(np.uint8)
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


MAX_PIXELS = 40_000_000   # 開ける画像の大きさの上限（8K の約3300万画素まで）。処理の時間とメモリを抑える
MIN_SIDE = 16             # 短い辺がこれ未満の画像は、ノイズの計算が成り立たない
_GRAY16 = ('I;16', 'I;16B', 'I;16L', 'I')


def load_image(src):
    """(元画像のメタデータ, RGB配列, アルファ配列 or None) を返す
    大きすぎる・小さすぎる画像は、画素を読み込む前に ValueError にする
    """
    pil = Image.open(src)
    w, h = pil.size
    if w * h > MAX_PIXELS:
        raise ValueError(f'画像が大きすぎます（{w}×{h}）。{MAX_PIXELS // 10_000:,}万画素まで開けます。')
    if min(w, h) < MIN_SIDE:
        raise ValueError(f'画像が小さすぎます（{w}×{h}）。')
    info = dict(pil.info)
    bands = pil.getbands()
    if pil.mode in _GRAY16:   # 16bit のグレーは上位8bitにする（そのまま変換すると白く飛ぶ）
        pil = Image.fromarray((np.asarray(pil).astype(np.uint32) >> 8).clip(0, 255).astype(np.uint8), 'L')
    rgba = np.asarray(pil.convert('RGBA'))
    alpha = rgba[..., 3] if 'A' in bands else None
    return info, np.ascontiguousarray(rgba[..., :3]), alpha


def save_png(out, alpha, meta, dst, strip_meta=False):
    """処理結果をPNGで保存する。meta は load_image が返したメタデータ
    色の情報（ICCプロファイル）は見た目に関わるので、strip_meta でも残す
    """
    if alpha is not None:
        out = np.dstack([out, alpha])
    info = PngImagePlugin.PngInfo()
    extra = {}
    if meta.get('icc_profile'):
        extra['icc_profile'] = meta['icc_profile']
    if not strip_meta:
        for k, v in meta.items():
            if isinstance(v, str):
                info.add_text(k, v)
        if meta.get('exif'):
            extra['exif'] = meta['exif']
        xmp = meta.get('xmp')   # JPEG / WebP の XMP。PNG の XMP はテキストとして上で引き継がれる
        if isinstance(xmp, bytes) and PNG_XMP_KEY not in meta:
            info.add_itxt(PNG_XMP_KEY, xmp.decode('utf-8', 'replace'))
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(dst, pnginfo=info, **extra)


def save_clean(out, alpha, meta, dst, remove=()):
    """処理結果をPNGで保存し、remove（metadata.GROUPS のキー）の種類のメタデータを取り除く"""
    save_png(out, alpha, meta, dst)
    if remove:
        Path(dst).write_bytes(strip(Path(dst).read_bytes(), set(remove)))


def process_file(src, dst, mode, lam, strip_meta, dark=DEFAULT_DARK, grad_th=DEFAULT_GRAD,
                 transparent=False, tolerance=DEFAULT_TOLERANCE):
    meta, rgb, alpha = load_image(src)
    out = clean(rgb, mode, lam, dark, grad_th)
    if transparent:   # ノイズを取って背景が平坦になった後の方が、きれいに抜ける
        out, cut, _ = cutout(out, tolerance)
        alpha = merge_alpha(cut, alpha)
    save_png(out, alpha, meta, dst, strip_meta)
    print(f'{src} -> {dst}')


def main():
    ap = argparse.ArgumentParser(description='AI生成イラストの塗り面ノイズ除去')
    ap.add_argument('src', help='入力画像 or フォルダ')
    ap.add_argument('dst', help='出力画像 or フォルダ')
    ap.add_argument('--mode', choices=['l0', 'soft'], default='l0')
    ap.add_argument('--lam', type=float, default=DEFAULT_LAM, help='L0の強さ（大きいほどベタ寄り）')
    ap.add_argument('--strip-meta', action='store_true', help='PNGメタデータを引き継がない')
    ap.add_argument('--cutout', action='store_true', help='単色の背景を透明にする')
    ap.add_argument('--tolerance', type=int, default=DEFAULT_TOLERANCE,
                    help='背景色とみなす色の差（0〜255）。省略時は背景のばらつきから自動で決める')
    a = ap.parse_args()

    src, dst = Path(a.src), Path(a.dst)
    opts = dict(transparent=a.cutout, tolerance=a.tolerance)
    if src.is_dir():
        for p in sorted(src.glob('*.png')):
            process_file(p, dst / f'{p.stem}_clean.png', a.mode, a.lam, a.strip_meta, **opts)
    else:
        process_file(src, dst, a.mode, a.lam, a.strip_meta, **opts)


if __name__ == '__main__':
    main()
