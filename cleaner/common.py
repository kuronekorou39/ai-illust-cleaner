"""共通処理：画像読み込み・ノイズ指標"""
import numpy as np
from PIL import Image

from . import noise

_CANDIDATES = ['Yu Gothic', 'Meiryo', 'Hiragino Sans', 'Noto Sans CJK JP']


def _pick_jp_font():
    """インストール済みの日本語フォントを1つ選ぶ（警告を出さないため）"""
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    return [n for n in _CANDIDATES if n in names][:1] + ['sans-serif']


def __getattr__(name):
    # matplotlib を使わない呼び出し元（GUI等）で読み込まないよう、参照時に解決する
    if name == 'JP_FONTS':
        return _pick_jp_font()
    raise AttributeError(name)


def load_rgb_on_white(path):
    """透過PNGは白背景に合成してfloatのRGB配列で返す"""
    im = Image.open(path).convert('RGBA')
    bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    return np.asarray(bg.convert('RGB'), dtype=float)


def fill_noise(rgb):
    """線を除外した「塗り面」の微小ノイズを返す（抽出はメディアン差）
    戻り値: (残差RGB, 線マスク, 塗り面のノイズ強度配列)
    """
    res, edge = noise.extract(rgb)
    return res, edge, noise.magnitude(res)[~edge]

def noise_score(rgb):
    """(ノイズ平均, 1階調超の画素率%) を返す"""
    _, _, m = fill_noise(rgb)
    return float(m.mean()), float((m > 1).mean() * 100)


# ---- 処理前後の近さ（scikit-image の既定と同じ計算。配布物を軽くするため自前で持つ）
SSIM_WINDOW = 7
SSIM_K1, SSIM_K2 = 0.01, 0.03


def ssim(a, b, data_range=255):
    """カラー画像の SSIM（チャンネルごとの平均）。7×7の一様な窓、標本共分散で計算する"""
    from scipy.ndimage import uniform_filter
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    n = SSIM_WINDOW ** 2
    cov_norm = n / (n - 1)
    c1, c2 = (SSIM_K1 * data_range) ** 2, (SSIM_K2 * data_range) ** 2
    pad = (SSIM_WINDOW - 1) // 2
    scores = []
    for ch in range(a.shape[2]):
        x, y = a[..., ch], b[..., ch]
        f = lambda v: uniform_filter(v, size=SSIM_WINDOW)
        ux, uy = f(x), f(y)
        vx = cov_norm * (f(x * x) - ux * ux)
        vy = cov_norm * (f(y * y) - uy * uy)
        vxy = cov_norm * (f(x * y) - ux * uy)
        s = ((2 * ux * uy + c1) * (2 * vxy + c2)) / ((ux ** 2 + uy ** 2 + c1) * (vx + vy + c2))
        if min(s.shape) > 2 * pad:
            s = s[pad:-pad, pad:-pad]   # 窓が画像からはみ出す縁は除く
        scores.append(s.mean())
    return float(np.mean(scores))


def psnr(a, b, data_range=255):
    """PSNR（dB）。完全に同じ画像では計算できないので None"""
    mse = np.mean((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2)
    return None if mse == 0 else float(10 * np.log10(data_range ** 2 / mse))
