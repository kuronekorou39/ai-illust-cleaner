"""裏で走る処理（全体処理・表示範囲だけのプレビュー処理）"""
import threading
import traceback
from dataclasses import dataclass, field
from itertools import count

import numpy as np
from PySide6.QtCore import QThread, Signal

from common import psnr, ssim
from cutout import cutout, is_uniform_background, merge_alpha
from denoise import blend, line_mask, smooth
import noise
from .imaging import composite_on_checker, composite_on_white, noise_image, noise_view

PREVIEW_PAD = 24         # プレビュー領域の外側に足す余白（端の影響を表示範囲に入れないため）
PREVIEW_MAX_SIDE = 512   # プレビューで処理する領域の一辺の上限
_source_ids = count(1)


@dataclass(frozen=True)
class Params:
    mode: str
    lam: float
    dark: int
    grad: int
    cutout: bool = False   # 単色の背景を透明にする
    tolerance: int | None = None   # None なら自動
    holes: bool = False    # 囲まれた背景（腕と体の間など）も自動で透明にする
    toggles: tuple = ()    # クリックで透明にするかどうかを反転させた位置 (x, y)

    @property
    def cutout_key(self):
        return (self.cutout, self.tolerance, self.holes, self.toggles)

    @property
    def smooth_key(self):
        """平滑化の結果が同じになる条件（線の保護のしきい値は含まない）"""
        return (self.mode, self.lam if self.mode == 'l0' else None)


@dataclass
class Source:
    """読み込んだ画像"""
    path: str
    rgb: np.ndarray
    alpha: np.ndarray | None
    meta: dict
    metadata: object = None   # metadata.Metadata（種類ごとの項目と生成情報）
    display: np.ndarray = None
    id: int = field(default_factory=lambda: next(_source_ids))

    def __post_init__(self):
        self.display = composite_on_white(self.rgb, self.alpha)


class LatestWorker(QThread):
    """最新の依頼だけを処理するスレッド。処理中に届いた古い依頼は捨てる"""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cond = threading.Condition()
        self._job = None
        self._running = True
        self._busy = False

    def submit(self, job):
        with self._cond:
            self._job = job
            self._cond.notify()

    def is_busy(self):
        with self._cond:
            return self._busy or self._job is not None

    def stop(self):
        with self._cond:
            self._running = False
            self._cond.notify()
        self.wait()

    def run(self):
        while True:
            with self._cond:
                self._busy = False
                while self._running and self._job is None:
                    self._cond.wait()
                if not self._running:
                    return
                job, self._job = self._job, None
                self._busy = True
            try:
                self.done.emit(self._fn(job))
            except Exception:
                self.failed.emit(traceback.format_exc())


class FullProcessor:
    """画像全体の処理。平滑化の結果は条件が変わるまで使い回す"""

    def __init__(self):
        self._cache_key = None
        self._smoothed = None

    def __call__(self, job):
        src, params, noise_key = job
        key = (src.id, params.smooth_key)
        if key != self._cache_key:
            self._smoothed = smooth(src.rgb, params.mode, params.lam)
            self._cache_key = key
        mask = line_mask(src.rgb, params.dark, params.grad)
        result = blend(src.rgb, self._smoothed, mask)
        # 指標はノイズ除去の効果だけを測りたいので、透過の前の見た目（元の透明部分は白に合成）で計算する
        display = composite_on_white(result, src.alpha)
        alpha, view, tolerance = src.alpha, display, None
        if params.cutout:
            result, cut, tolerance = cutout(result, params.tolerance, params.holes, params.toggles)
            alpha = merge_alpha(cut, src.alpha)
            view = composite_on_checker(result, alpha)
        noise_rgb, mean, over = noise_view(display, noise_key)
        a, b = src.display.astype(float), display.astype(float)
        return {
            'source_id': src.id, 'params': params, 'noise_key': noise_key,
            'result': result, 'alpha': alpha, 'display': display, 'view': view, 'noise': noise_rgb, 'mask': mask,
            'tolerance': tolerance,
            'smoothed': self._smoothed,
            'metrics': {
                'mean': mean, 'over': over,
                'ssim': ssim(a, b),
                'psnr': psnr(a, b),   # 1画素も変わらなかった場合は None
            },
        }


class PreviewProcessor:
    """表示範囲だけを処理する。スライダー操作中の素早い反映用"""

    def __init__(self):
        self._cache_key = None
        self._smoothed = None

    def __call__(self, job):
        src, params, rect, smoothed_full, noise_key = job
        x0, y0, x1, y1 = rect
        h, w = src.rgb.shape[:2]
        px0, py0 = max(0, x0 - PREVIEW_PAD), max(0, y0 - PREVIEW_PAD)
        px1, py1 = min(w, x1 + PREVIEW_PAD), min(h, y1 + PREVIEW_PAD)
        crop = np.ascontiguousarray(src.rgb[py0:py1, px0:px1])

        if smoothed_full is not None:   # 全体の平滑化結果が使えるならそこから切り出す
            smoothed = smoothed_full[py0:py1, px0:px1]
        else:
            key = (src.id, params.smooth_key, (px0, py0, px1, py1))
            if key != self._cache_key:
                self._smoothed = smooth(crop, params.mode, params.lam)
                self._cache_key = key
            smoothed = self._smoothed

        mask = line_mask(crop, params.dark, params.grad)
        alpha = None if src.alpha is None else src.alpha[py0:py1, px0:px1]
        display = composite_on_white(blend(crop, smoothed, mask), alpha)
        inner = np.s_[y0 - py0:y1 - py0, x0 - px0:x1 - px0]
        # 周波数の表示は画像全体から計算するもので、切り出しでは作れない
        spatial = noise_key[1] in noise.SPATIAL_STYLES
        return {
            'source_id': src.id, 'params': params, 'rect': rect, 'noise_key': noise_key,
            'display': display[inner], 'noise': noise_image(display, noise_key)[inner] if spatial else None,
            'mask': mask[inner],
        }


def analyze_source(job):
    """元画像のノイズマップ・指標・背景が単色か"""
    src, noise_key = job
    noise_rgb, mean, over = noise_view(src.display, noise_key)
    return {'source_id': src.id, 'noise_key': noise_key, 'noise': noise_rgb, 'metrics': {'mean': mean, 'over': over},
            'uniform_bg': is_uniform_background(src.display)}


def render_noise(job):
    """表示方式を切り替えたときに、元画像と処理後のノイズマップを描き直す"""
    src, result, noise_key = job
    return {'source_id': src.id, 'noise_key': noise_key, 'result': result,
            'orig': noise_image(src.display, noise_key),
            'after': None if result is None else noise_image(result['display'], noise_key)}


def clamp_preview_rect(rect, center):
    """プレビュー領域が大きすぎる場合、表示の中心まわりに絞る"""
    x0, y0, x1, y1 = rect
    cx, cy = center
    half = PREVIEW_MAX_SIDE // 2
    if x1 - x0 > PREVIEW_MAX_SIDE:
        c = int(min(max(cx, x0 + half), x1 - half))
        x0, x1 = c - half, c + half
    if y1 - y0 > PREVIEW_MAX_SIDE:
        c = int(min(max(cy, y0 + half), y1 - half))
        y0, y1 = c - half, c + half
    return x0, y0, x1, y1


def render_algos(job):
    """抽出方式を4通り並べて比べるための、元画像のノイズマップ"""
    src, style = job
    return {'source_id': src.id, 'style': style,
            'layers': {k: noise_image(src.display, (k, style)) for k in noise.ALGOS}}
