"""表示用の画像変換（白背景合成・ノイズマップ・QImage化）"""
import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtGui import QColor, QImage

from cleaner import noise
from . import theme

DEFAULT_NOISE = ('median', 'strength')   # ノイズの (抽出方式, 表示方式)
RESIDUAL_CACHE_BYTES = 512 * 2 ** 20   # ノイズの抽出結果を保持する量の上限
VIEW_CACHE_BYTES = 256 * 2 ** 20       # ノイズの描画結果を保持する量の上限
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


class _LRU:
    """大きさの上限つきで、最近使ったものから残す"""

    def __init__(self, limit):
        self._limit = limit
        self._items = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()   # 裏の処理から同時に使われる

    def get(self, key, make, size_of):
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = make()   # 計算は鍵を持たずに行う（ほかの処理を待たせない）
        with self._lock:
            if key not in self._items:
                self._items[key] = value
                self._size += size_of(value)
                while self._size > self._limit and len(self._items) > 1:
                    _, old = self._items.popitem(last=False)
                    self._size -= size_of(old)
        return value

    def clear(self):
        with self._lock:
            self._items.clear()
            self._size = 0


class NoiseCache:
    """ノイズの抽出結果と描画結果を、画像ごとに保持する

    表示方式を切り替えても抽出はやり直さず、一度見た表示はすぐ出せる。
    image_key は画像を見分ける鍵（元画像なら ('source', id)、処理結果なら ('result', id)）
    """

    def __init__(self):
        self._residuals = _LRU(RESIDUAL_CACHE_BYTES)
        self._views = _LRU(VIEW_CACHE_BYTES)

    def residual(self, image_key, display, algo):
        return self._residuals.get((image_key, algo), lambda: noise.extract(display, algo),
                                   lambda r: r[0].nbytes + r[1].nbytes)

    def view(self, image_key, display, key):
        algo, style = key
        make = lambda: noise.render(display, self.residual(image_key, display, algo)[0], style)
        return self._views.get((image_key, algo, style), make, lambda v: v.nbytes)

    def clear(self):
        self._residuals.clear()
        self._views.clear()


NOISE_CACHE = NoiseCache()


def noise_view(display, key=DEFAULT_NOISE, image_key=None):
    """(ノイズ表示RGB, ノイズ平均, 1階調超の画素率%) を返す。指標は表示方式によらずメディアン差で計算する
    image_key を渡すと結果を保持する（表示範囲だけの切り出しなど、使い捨ての画像では渡さない）
    """
    if image_key is None:
        res, edge = noise.extract(display)
        mean, over = noise.metrics(res, edge)
        return noise_image(display, key), mean, over
    mean, over = noise.metrics(*NOISE_CACHE.residual(image_key, display, 'median'))
    return NOISE_CACHE.view(image_key, display, key), mean, over


def noise_image(display, key=DEFAULT_NOISE, image_key=None):
    """ノイズ表示RGBだけを返す"""
    if image_key is not None:
        return NOISE_CACHE.view(image_key, display, key)
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
