"""表示用の画像変換（白背景合成・ノイズマップ・QImage化）"""
import numpy as np
from PySide6.QtGui import QColor, QImage

from cleaner import noise
from . import theme

DEFAULT_NOISE = ('median', 'strength')   # ノイズの (抽出方式, 表示方式)
MASK_OVERLAY_ALPHA = 150  # 線マスク表示の最大不透明度
CHECKER_CELL = 12         # 透明部分を示す市松模様の1マス（画像の画素数）
CHECKER_COLORS = (72, 96)   # 白い縁の残りが分かるよう暗めにする


def composite_on_white(rgb, alpha):
    """透過画像を白背景に合成する（表示と指標計算はこの見た目で行う）"""
    if alpha is None:
        return rgb
    a = alpha.astype(np.float32)[..., None] / 255
    return np.round(rgb.astype(np.float32) * a + 255 * (1 - a)).astype(np.uint8)


def composite_on_checker(rgb, alpha, origin=(0, 0)):
    """透明部分を市松模様の上に合成する。origin は切り出し位置（模様を全体とそろえるため）"""
    h, w = alpha.shape
    yy, xx = np.mgrid[origin[1]:origin[1] + h, origin[0]:origin[0] + w]
    dark, light = CHECKER_COLORS
    board = np.where(((yy // CHECKER_CELL + xx // CHECKER_CELL) % 2)[..., None].astype(bool), dark, light)
    a = alpha.astype(np.float32)[..., None] / 255
    return np.round(rgb.astype(np.float32) * a + board * (1 - a)).astype(np.uint8)


def noise_view(display, key=DEFAULT_NOISE):
    """(ノイズ表示RGB, ノイズ平均, 1階調超の画素率%) を返す。指標は表示方式によらずメディアン差で計算する"""
    algo, style = key
    res, edge = noise.extract(display)
    mean, over = noise.metrics(res, edge)
    if algo != 'median':
        res, _ = noise.extract(display, algo)
    return noise.render(display, res, style), mean, over


def noise_image(display, key=DEFAULT_NOISE):
    """ノイズ表示RGBだけを返す"""
    algo, style = key
    res, _ = noise.extract(display, algo)
    return noise.render(display, res, style)

def to_qimage(rgb):
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def mask_to_qimage(mask):
    """線マスク（0〜1）をアクセント色の半透明画像にする"""
    c = QColor(theme.ACCENT)
    h, w = mask.shape
    a = mask * (MASK_OVERLAY_ALPHA / 255)
    bgra = np.empty((h, w, 4), np.uint8)   # 乗算済みアルファ
    bgra[..., 0] = c.blue() * a
    bgra[..., 1] = c.green() * a
    bgra[..., 2] = c.red() * a
    bgra[..., 3] = a * 255
    return QImage(bgra.data, w, h, 4 * w, QImage.Format_ARGB32_Premultiplied).copy()
