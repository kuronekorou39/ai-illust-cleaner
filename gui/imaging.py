"""表示用の画像変換（白背景合成・ノイズマップ・QImage化）"""
import cv2
import numpy as np
from PySide6.QtGui import QColor, QImage

from common import fill_noise
from . import theme

NOISE_FULL_SCALE = 3.0    # ノイズマップで最大輝度になるノイズ強度（階調）
LINE_HINT_BASE = 16       # ノイズマップの地の明るさ
LINE_HINT_GAIN = 0.10     # ノイズマップにうっすら重ねる線画の濃さ
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


def noise_view(display):
    """(ノイズマップRGB, ノイズ平均, 1階調超の画素率%) を返す"""
    rgb = display.astype(float)
    res, _, m = fill_noise(rgb)
    mag = np.sqrt((res ** 2).sum(2))
    level = np.clip(mag / NOISE_FULL_SCALE * 255, 0, 255).astype(np.uint8)
    heat = cv2.cvtColor(cv2.applyColorMap(level, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)
    base = LINE_HINT_BASE + (255 - rgb.mean(2)) * LINE_HINT_GAIN
    view = np.maximum(heat, base[..., None].astype(np.uint8))
    if m.size == 0:   # 全面が線と判定された場合
        return view, 0.0, 0.0
    return view, float(m.mean()), float((m > 1).mean() * 100)


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
