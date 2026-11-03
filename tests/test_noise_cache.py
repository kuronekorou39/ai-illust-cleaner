"""ノイズの抽出・描画の保持（表示方式の切り替えで、抽出をやり直さないか）"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cleaner import noise  # noqa: E402
from gui import imaging  # noqa: E402


def _image():
    return np.random.default_rng(0).integers(0, 255, (64, 80, 3), dtype=np.uint8)


def test_switching_style_reuses_extraction(monkeypatch):
    calls = []
    real = noise.extract
    monkeypatch.setattr(noise, 'extract', lambda rgb, algo='median': calls.append(algo) or real(rgb, algo))
    cache = imaging.NoiseCache()
    img = _image()
    for style in noise.STYLES:
        cache.view(('source', 1), img, ('gauss', style))
    assert calls == ['gauss']   # 表示方式を5つ切り替えても、抽出は1回


def test_seen_view_is_returned_immediately():
    cache = imaging.NoiseCache()
    img = _image()
    first = cache.view(('source', 1), img, ('median', 'color'))
    assert cache.view(('source', 1), img, ('median', 'color')) is first


def test_images_are_kept_apart():
    cache = imaging.NoiseCache()
    a, b = _image(), 255 - _image()
    va = cache.view(('source', 1), a, ('median', 'strength'))
    vb = cache.view(('result', 1), b, ('median', 'strength'))
    assert va is not vb


def test_cache_respects_size_limit():
    lru = imaging._LRU(limit=1000)
    for i in range(10):
        lru.get(i, lambda: np.zeros(400, np.uint8), lambda v: v.nbytes)
    assert lru._size <= 1000 and len(lru._items) == 2   # 古いものから捨てる
    assert list(lru._items) == [8, 9]
