"""処理前後の比較図（ノイズ指標・PSNR/SSIM・拡大）

使い方: python tools/compare.py 元画像.png 処理後1.png [処理後2.png ...] [--ref 手描き.png] [--out 出力.png]
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from common import JP_FONTS, load_rgb_on_white, fill_noise, psnr, ssim

plt.rcParams['font.family'] = JP_FONTS

ap = argparse.ArgumentParser()
ap.add_argument('orig'); ap.add_argument('results', nargs='+')
ap.add_argument('--ref', help='参考用（手描きなど）。サイズ違いでもOK')
ap.add_argument('--crop', default='300,140,110')
ap.add_argument('--out', default='output/compare.png')
a = ap.parse_args()

orig = load_rgb_on_white(a.orig)
items = [(a.orig, 'orig')] + [(p, 'result') for p in a.results] + ([(a.ref, 'ref')] if a.ref else [])
y0, x0, s = map(int, a.crop.split(','))
fig, ax = plt.subplots(2, len(items), figsize=(4.4 * len(items), 9.5), squeeze=False)
for j, (p, kind) in enumerate(items):
    rgb = load_rgb_on_white(p)
    res, _, m = fill_noise(rgb)
    t = f'{Path(p).name}\nノイズ平均 {m.mean():.3f} / 1階調超 {100*(m>1).mean():.2f}%'
    if kind == 'result' and rgb.shape == orig.shape:
        db = psnr(orig, rgb)
        t += f'\nPSNR {"変化なし" if db is None else f"{db:.1f}dB"} / SSIM {ssim(orig, rgb):.3f}'
    ax[0, j].imshow(rgb[y0:y0+s, x0:x0+s].astype(np.uint8), interpolation='nearest'); ax[0, j].set_title(t, fontsize=10)
    ax[1, j].imshow(np.clip(128 + res[y0:y0+s, x0:x0+s] * 60, 0, 255).astype(np.uint8), interpolation='nearest')
    ax[1, j].set_title('色ノイズ ×60')
for x in ax.ravel(): x.axis('off')
plt.tight_layout()
Path(a.out).parent.mkdir(parents=True, exist_ok=True)
plt.savefig(a.out, dpi=90)
print('saved:', a.out)
