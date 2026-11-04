"""塗り面ノイズの抽出と描画

抽出（ALGOS）：画像から「なめらかな成分」を引いた残りをノイズとみなす。何を引くかで拾うノイズが変わる
描画（STYLES）：取り出したノイズを、強さ・色・明暗・色味・周波数のどれで見せるか
指標（ノイズ平均・1階調超の画素率）は比較できるよう、常にメディアン差で計算する
"""
import cv2
import numpy as np
from scipy import ndimage

EDGE_SIGMA = 1.2       # 線の検出に使う勾配のぼかし
EDGE_THRESHOLD = 8     # これより強い勾配を線とみなす
EDGE_GROW = 3          # 線の周りの、ノイズの計算から外す幅（画素）
MEDIAN_SIZE = 7
GAUSS_SIGMA = 1.5
BILATERAL = dict(d=7, sigmaColor=8, sigmaSpace=3)   # 塗りの揺らぎ（数階調）だけをならし、線はまたがない
LAPLACIAN_NORM = np.sqrt(20)   # 4近傍ラプラシアンで、白色ノイズの振幅が元の大きさにそろう係数
# 緩いグラデーションは1階調ずつの段差になり、段差の境目は周りとの差が1になる。
# 周りの8画素のうちこの数以上と3色とも同じ値なら、平らな面の一部（段差の境目）とみなしてノイズに数えない。
# ランダムな粒は周りとそろわないので、ほとんど数え落とさない
STEP_SAME_NEIGHBORS = 4
_NEIGHBORS = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)]

FULL_SCALE = 3.0       # 強さ表示で最大輝度になるノイズ強度（階調）
SPLIT_SCALE = 1.5      # 明暗・色味の表示で最大輝度になる値。RGB3色の合計より小さく出るため低めにし、両者は同じ尺度にそろえる
SPECTRUM_MAX = 3.0     # 周波数表示で最大輝度になる振幅（その周波数だけのノイズの大きさ、階調）
COLOR_GAIN = 40        # 色表示でノイズを強調する倍率
LINE_HINT_BASE = 16    # ノイズ表示の地の明るさ
LINE_HINT_GAIN = 0.10  # ノイズ表示にうっすら重ねる線画の濃さ

ALGOS = {
    'median': ('メディアン差', '周囲の中央値との差。粒状のノイズを広く拾う（数値もこの方式）'),
    'gauss': ('ガウシアン差', '近くの平均との差。1〜2画素の細かい粒を強く拾う'),
    'bilateral': ('バイラテラル差', '似た色だけの平均との差。色の境目の誤検出が少ない'),
    'laplacian': ('ラプラシアン', '上下左右との差（2階微分）。画素単位のざらつきを拾う'),
}

STYLES = {
    'strength': ('強さ', '明るいほどノイズが大きい'),
    'color': ('色', 'ノイズの色の偏りを見る（灰色が0）'),
    'luma': ('明暗', '明るさのムラだけを見る'),
    'chroma': ('色味', '色味のムラだけを見る'),
    'spectrum': ('周波数', '周波数ごとの強さ。点や格子が出たら規則的なノイズ'),
}
SPATIAL_STYLES = tuple(k for k in STYLES if k != 'spectrum')   # 画像の位置と対応する表示

# BT.601 の YCbCr（Cb・Cr は明るさを除いた色の成分）
_YCBCR = np.array([[0.299, 0.587, 0.114],
                   [-0.168736, -0.331264, 0.5],
                   [0.5, -0.418688, -0.081312]], np.float32)


def line_mask(rgb):
    """線とその周りを True とするマスク"""
    g = rgb.mean(2)
    return ndimage.binary_dilation(ndimage.gaussian_gradient_magnitude(g, EDGE_SIGMA) > EDGE_THRESHOLD,
                                   iterations=EDGE_GROW)


def _smooth(rgb, algo):
    if algo == 'median':
        u8 = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
        return np.dstack([cv2.medianBlur(np.ascontiguousarray(u8[..., c]), MEDIAN_SIZE)
                          for c in range(3)]).astype(np.float32)
    if algo == 'gauss':
        return cv2.GaussianBlur(rgb, (0, 0), GAUSS_SIGMA)
    if algo == 'bilateral':
        return cv2.bilateralFilter(rgb, **BILATERAL)
    raise ValueError(algo)


def extract(rgb, algo='median'):
    """(残差RGB, 線マスク) を返す。残差は線の部分を0にしてある"""
    rgb = np.asarray(rgb, np.float32)
    if algo == 'laplacian':
        res = -cv2.Laplacian(rgb, cv2.CV_32F) / LAPLACIAN_NORM
    else:
        res = rgb - _smooth(rgb, algo)
    edge = line_mask(rgb)
    res[edge] = 0
    res[_gradient_steps(rgb, res)] = 0
    return res, edge


def _gradient_steps(rgb, res):
    """緩いグラデーションの段差の境目（差が1階調以内で、周りに同じ値の画素が並んでいる）"""
    q = np.rint(rgb).astype(np.int16)
    h, w = q.shape[:2]
    p = np.pad(q, ((1, 1), (1, 1), (0, 0)), mode='edge')
    same = sum((p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] == q).all(2).astype(np.uint8) for dy, dx in _NEIGHBORS)
    return (np.abs(res) <= 1).all(2) & (same >= STEP_SAME_NEIGHBORS)


def magnitude(res):
    return np.sqrt((res ** 2).sum(2))


def metrics(res, edge):
    """(ノイズ平均, 1階調超の画素率%)。全面が線と判定された場合は 0"""
    m = magnitude(res)[~edge]
    if m.size == 0:
        return 0.0, 0.0
    return float(m.mean()), float((m > 1).mean() * 100)


def _heat(values, full_scale=FULL_SCALE):
    level = np.clip(values / full_scale * 255, 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.applyColorMap(level, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)


def _with_lines(view, rgb):
    """暗い地に線画をうっすら重ね、どこのノイズかわかるようにする"""
    base = (LINE_HINT_BASE + (255 - rgb.mean(2)) * LINE_HINT_GAIN).astype(np.uint8)
    return np.maximum(view, base[..., None])


def _spectrum(res):
    """残差の明るさ成分の振幅スペクトルを、画像と同じ大きさの図にする
    画像どうし（処理前後など）を比べられるよう、明るさは画像ごとに合わせず固定の尺度で描く
    """
    y = res @ _YCBCR[0]
    h, w = y.shape
    window = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)   # 画像の端が線として出ないようにする
    # 窓の大きさで割り、ノイズの標準偏差と同じ単位（階調）にそろえる
    amp = np.abs(np.fft.fftshift(np.fft.fft2(y * window))) / np.sqrt((window ** 2).sum())
    return _heat(np.log1p(amp) / np.log1p(SPECTRUM_MAX) * FULL_SCALE)


def render(rgb, res, style='strength'):
    """ノイズの表示用RGB（uint8）"""
    if style == 'spectrum':
        return _spectrum(res)
    if style == 'color':
        return np.clip(128 + res * COLOR_GAIN, 0, 255).astype(np.uint8)
    if style == 'strength':
        heat = _heat(magnitude(res))
    else:
        ycc = res @ _YCBCR.T
        values = np.abs(ycc[..., 0]) if style == 'luma' else np.sqrt((ycc[..., 1:] ** 2).sum(2))
        heat = _heat(values, SPLIT_SCALE)
    return _with_lines(heat, np.asarray(rgb, np.float32))
