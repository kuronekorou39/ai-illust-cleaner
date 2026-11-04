"""線の判定：細い暗い線は守り、広い暗い面とノイズは線とみなさない"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cleaner.denoise import clean, line_mask  # noqa: E402


def _canvas():
    return np.full((120, 120, 3), 230, np.uint8)


def test_thin_dark_line_is_protected():
    img = _canvas()
    img[:, 58:61] = 20   # 3画素幅の黒い線
    m = line_mask(img)
    assert m[60, 59] > 0.9


def test_wide_dark_fill_is_not_a_line():
    img = _canvas()
    img[20:100, 20:100] = (22, 33, 62)   # 紺色のベタ塗り
    m = line_mask(img)
    assert m[60, 60] < 0.1                     # 面の中は塗りとして処理する
    assert m[20, 60] > 0.5                     # 面の縁は輪郭として守る


def test_noise_is_not_mistaken_for_lines():
    """JPEG のような強いノイズのある平らな面を、線とみなさない"""
    rng = np.random.default_rng(0)
    img = np.clip(_canvas().astype(int) + rng.normal(0, 6, (120, 120, 3)), 0, 255).astype(np.uint8)
    assert (line_mask(img) > 0.5).mean() < 0.05


def test_noisy_dark_fill_gets_cleaned():
    rng = np.random.default_rng(1)
    base = np.zeros((120, 120, 3), np.uint8) + np.array([22, 33, 62], np.uint8)
    img = np.clip(base.astype(int) + rng.normal(0, 3, base.shape), 0, 255).astype(np.uint8)
    out = clean(img)
    spread = lambda a: a[20:100, 20:100].reshape(-1, 3).std(0).max()   # 色ごとのばらつき
    assert spread(out) < spread(img) / 3
