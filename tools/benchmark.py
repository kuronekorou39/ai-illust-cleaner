"""他のノイズ除去手法との比較（docs/comparison.md の数値と図を作る）

使い方:
  uv run --with bm3d --with waifu2x-ncnn-py --with opencv-contrib-python python tools/benchmark.py [Geminiのアイコン.png] [--local input/sd_img2img.png]

BM3D と waifu2x は pyproject に入れていない。入っていなければその手法を飛ばす。
waifu2x-ncnn-py は contrib なしの opencv-python を連れてくるので、opencv-contrib-python も指定して上書きする。
すべての設定の結果を表で出し、図（docs/comparison_*.png）には PICK の設定だけを並べる。
"""
import argparse, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from cleaner.common import JP_FONTS, fill_noise, noise_score, psnr, ssim
from cleaner.denoise import clean, line_mask, load_image
from gui.imaging import composite_on_white

plt.rcParams['font.family'] = JP_FONTS
ROOT = Path(__file__).resolve().parent.parent


def _bm3d(sigma):
    import bm3d
    return lambda rgb: np.clip(bm3d.bm3d_rgb(rgb / 255.0, sigma / 255) * 255 + 0.5, 0, 255).astype(np.uint8)


def _waifu2x(noise):
    from PIL import Image
    from waifu2x_ncnn_py import Waifu2x
    w = Waifu2x(gpuid=-1, scale=1, noise=noise, model='models-cunet')   # gpuid=-1 で CPU
    return lambda rgb: np.asarray(w.process_pil(Image.fromarray(rgb)))


def _guided(r, eps):
    return lambda rgb: cv2.ximgproc.guidedFilter(rgb, rgb, r, eps)


# 手法 → [(設定, 処理を作る関数)]。処理を作る関数は import できなければ飛ばす
METHODS = {
    '本ツール': [('標準 lam=0.002', lambda: clean),
                 ('強め lam=0.005', lambda: lambda rgb: clean(rgb, lam=0.005))],
    'Non-Local Means': [(f'h={h}', lambda h=h: lambda rgb: cv2.fastNlMeansDenoisingColored(rgb, None, h, h, 7, 21))
                        for h in (3, 5, 10)],
    'バイラテラル': [(f'σcolor={s}', lambda s=s: lambda rgb: cv2.bilateralFilter(rgb, 7, s, 3))
                     for s in (10, 25, 50)],
    'ガイデッド': [(f'r={r} eps={e}', lambda r=r, e=e: _guided(r, e)) for r, e in ((2, 25), (2, 100), (4, 100))],
    'メディアン': [('3×3', lambda: lambda rgb: cv2.medianBlur(rgb, 3))],
    'BM3D': [(f'σ={s}', lambda s=s: _bm3d(s)) for s in (2, 5, 10)],
    'waifu2x': [(f'cunet noise={n}', lambda n=n: _waifu2x(n)) for n in (0, 1, 3)],
}

# 図に並べる設定（各手法でノイズと線の残り方の釣り合いがよいもの）
PICK = {'本ツール': ['標準 lam=0.002', '強め lam=0.005'], 'Non-Local Means': ['h=3'],
        'バイラテラル': ['σcolor=25'], 'ガイデッド': ['r=2 eps=100'], 'メディアン': ['3×3'],
        'BM3D': ['σ=5'], 'waifu2x': ['cunet noise=3']}


def edge_strength(rgb):
    """グレーの Sobel 勾配の大きさ"""
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    return cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))


def measure(orig, out, lines):
    """表示（白背景合成）どうしで測る。線の保持 = 線の画素の勾配の平均を元画像と比べた比"""
    nm, over = noise_score(out.astype(float))
    db = psnr(orig, out)
    keep = edge_strength(out)[lines].mean() / edge_strength(orig)[lines].mean()
    return dict(noise=nm, over=over, ssim=ssim(orig, out), psnr=db, keep=keep)


def run(path):
    _, rgb, alpha = load_image(path)
    orig = composite_on_white(rgb, alpha)
    lines = line_mask(orig) > 0.5
    nm, over = noise_score(orig.astype(float))
    rows = [('元画像', '', dict(noise=nm, over=over, ssim=1.0, psnr=None, keep=1.0, sec=0.0), orig)]
    for name, settings in METHODS.items():
        for label, make in settings:
            try:
                f = make()
            except ImportError:
                print(f'  {name}: 入っていないので飛ばす')
                break
            t = time.perf_counter()
            out = f(rgb)
            sec = time.perf_counter() - t
            disp = composite_on_white(out, alpha)
            rows.append((name, label, dict(measure(orig, disp, lines), sec=sec), disp))
    return rows


def print_table(title, rows):
    print(f'\n### {title}\n')
    print('| 手法 | 設定 | ノイズ平均 | 1階調超 | SSIM | PSNR | 線の保持 | 秒 |')
    print('|---|---|---|---|---|---|---|---|')
    for name, label, m, _ in rows:
        db = '-' if m['psnr'] is None else f"{m['psnr']:.1f}"
        print(f"| {name} | {label} | {m['noise']:.3f} | {m['over']:.2f}% | {m['ssim']:.3f} | {db} "
              f"| {m['keep']:.3f} | {m['sec']:.2f} |")


def figure(rows, crop, out):
    y0, x0, s = crop
    picked = [r for r in rows if r[0] == '元画像' or r[1] in PICK.get(r[0], [])]
    fig, ax = plt.subplots(2, len(picked), figsize=(2.3 * len(picked), 5.4), squeeze=False)
    for j, (name, label, m, img) in enumerate(picked):
        res, _, _ = fill_noise(img.astype(float))
        ax[0, j].imshow(img[y0:y0 + s, x0:x0 + s], interpolation='nearest')
        ax[0, j].set_title(f"{name}\n{label}\nノイズ {m['noise']:.3f} / 線 {m['keep']:.2f}", fontsize=9)
        ax[1, j].imshow(np.clip(128 + res[y0:y0 + s, x0:x0 + s] * 60, 0, 255).astype(np.uint8),
                        interpolation='nearest')
    ax[1, 0].text(0.5, -0.03, '下段：色ノイズ ×60', transform=ax[1, 0].transAxes, ha='center', va='top', fontsize=9)
    for x in ax.ravel(): x.axis('off')
    plt.tight_layout()
    plt.savefig(out, dpi=100)
    plt.close(fig)
    print('saved:', out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('gemini', nargs='?', help='Gemini の画像（JPEG 由来の強いノイズの例）')
    ap.add_argument('--local', help='数値だけ出す画像（図は作らない）')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    if not hasattr(cv2, 'ximgproc'):
        sys.exit('cv2.ximgproc がない。--with opencv-contrib-python を付けて実行する')

    targets = [(ROOT / 'docs/sample.png', (560, 880, 200), ROOT / 'docs/comparison_sample.png')]
    if a.gemini:
        targets.append((Path(a.gemini), (10, 10, 140), ROOT / 'docs/comparison_gemini.png'))
    if a.local:
        targets.append((Path(a.local), None, None))
    for path, crop, out in targets:
        print(f'{path.name} を処理中…')
        rows = run(path)
        print_table(path.name, rows)
        if out:
            figure(rows, crop, out)


if __name__ == '__main__':
    main()
