"""ノイズの測り方：グラデーションの段差はノイズに数えず、ランダムな粒は数える"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cleaner import noise  # noqa: E402


def _gradient():
    """横方向に1階調ずつ上がる、緩いグラデーション（段差の幅は8画素）"""
    ramp = np.repeat(np.arange(100, 116), 8)[None, :].repeat(96, 0)
    return np.dstack([ramp, ramp + 10, ramp + 30]).astype(np.uint8)


def test_gradient_steps_are_not_noise():
    for algo in noise.ALGOS:
        res, edge = noise.extract(_gradient(), algo)
        mean, over = noise.metrics(res, edge)
        assert mean < 0.01 and over == 0, algo


def test_random_grain_is_still_measured():
    rng = np.random.default_rng(0)
    flat = np.full((96, 128, 3), 120, np.int16)
    grain = np.clip(flat + rng.integers(-1, 2, flat.shape), 0, 255).astype(np.uint8)   # ±1階調の粒
    res, edge = noise.extract(grain)
    hit = (noise.magnitude(res) > 0.5)[~edge].mean()
    assert hit > 0.5   # 粒の大半はノイズとして数える
