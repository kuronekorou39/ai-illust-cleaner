"""共通処理：画像読み込み・ノイズ指標"""
import numpy as np
from PIL import Image
from scipy import ndimage

_CANDIDATES = ['Yu Gothic', 'Meiryo', 'Hiragino Sans', 'Noto Sans CJK JP']


def _pick_jp_font():
    """インストール済みの日本語フォントを1つ選ぶ（警告を出さないため）"""
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    return [n for n in _CANDIDATES if n in names][:1] + ['sans-serif']


JP_FONTS = _pick_jp_font()


def load_rgb_on_white(path):
    """透過PNGは白背景に合成してfloatのRGB配列で返す"""
    im = Image.open(path).convert('RGBA')
    bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    return np.asarray(bg.convert('RGB'), dtype=float)


def fill_noise(rgb):
    """線を除外した「塗り面」の微小ノイズを返す
    戻り値: (残差RGB, 線マスク, 塗り面のノイズ強度配列)
    """
    g = rgb.mean(2)
    edge = ndimage.binary_dilation(ndimage.gaussian_gradient_magnitude(g, 1.2) > 8, iterations=3)
    res = rgb - ndimage.median_filter(rgb, size=(7, 7, 1))
    res[edge] = 0
    mag = np.sqrt((res ** 2).sum(2))
    return res, edge, mag[~edge]


def noise_score(rgb):
    """(ノイズ平均, 1階調超の画素率%) を返す"""
    _, _, m = fill_noise(rgb)
    return float(m.mean()), float((m > 1).mean() * 100)
