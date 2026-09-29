"""塗り面ノイズの可視化（強度マップ・色ノイズ・拡大・FFT）

使い方: python tools/visualize_noise.py 画像.png [--out 出力.png] [--crop y,x,size] [--gain 60]
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from common import JP_FONTS, load_rgb_on_white, fill_noise

plt.rcParams['font.family'] = JP_FONTS

ap = argparse.ArgumentParser()
ap.add_argument('src')
ap.add_argument('--out')
ap.add_argument('--crop', default='300,140,110', help='拡大範囲 y,x,size')
ap.add_argument('--gain', type=float, default=60)
a = ap.parse_args()

rgb = load_rgb_on_white(a.src)
res, edge, m = fill_noise(rgb)
mag = np.zeros(edge.shape); mag[~edge] = m
y0, x0, s = map(int, a.crop.split(','))
view = lambda r: np.clip(128 + r * a.gain, 0, 255).astype(np.uint8)

fig, ax = plt.subplots(2, 3, figsize=(15, 10.4)); ax = ax.ravel()
ax[0].imshow(rgb.astype(np.uint8))
ax[0].add_patch(plt.Rectangle((x0, y0), s, s, fill=False, ec='cyan', lw=2))
ax[0].set_title('元画像（水色枠=拡大範囲）')
ax[1].imshow(mag, cmap='inferno', vmin=0, vmax=4); ax[1].set_title('塗り面ノイズ強度（線を除外）')
ax[2].imshow(view(res)); ax[2].set_title(f'塗り面の色ノイズ ×{a.gain:g}')
ax[3].imshow(rgb[y0:y0+s, x0:x0+s].astype(np.uint8), interpolation='nearest'); ax[3].set_title('拡大：元画像')
ax[4].imshow(view(res[y0:y0+s, x0:x0+s]), interpolation='nearest'); ax[4].set_title(f'拡大：色ノイズ ×{a.gain:g}')
F = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(res.mean(2)))))
ax[5].imshow(F, cmap='gray', vmin=np.percentile(F, 1), vmax=np.percentile(F, 99.8)); ax[5].set_title('塗り面ノイズのFFT')
for x in ax: x.axis('off')
fig.suptitle(f'{Path(a.src).name}   ノイズ平均={m.mean():.3f}  1階調超={100*(m>1).mean():.2f}%', fontsize=15)
plt.tight_layout()
out = a.out or f'output/{Path(a.src).stem}_noise.png'
Path(out).parent.mkdir(parents=True, exist_ok=True)
plt.savefig(out, dpi=100)
print('saved:', out)
