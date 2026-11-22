"""単色の背景を透明にする（線画＋塗りのイラスト向け）

背景色は画像の縁から推定し、縁とつながっている背景色の領域だけを透明にする。
キャラクターの白い模様など、縁とつながっていない同色の部分は残る。
輪郭の半透明の画素からは背景色の混ざりを取り除き、暗い背景に置いても白く浮かないようにする。
"""
import cv2
import numpy as np
from scipy import ndimage

DEFAULT_TOLERANCE = None  # 背景色とみなす色の差（0〜255）。None なら背景のばらつきから自動で決める
AUTO_MIN, AUTO_MAX = 4, 24   # 自動で決める許容差の範囲
AUTO_MARGIN = 3           # 自動で決めるとき、背景のばらつきに足す余裕
RING_WIDTH = 8            # 背景のばらつきを測る外周の幅（画素）
COLOR_STEP = 8            # 背景色を推定するときの色の丸め幅
UNIFORM_SHARE = 0.6      # 縁の画素のうち背景色がこの割合以上なら「単色の背景」とみなす
OPAQUE_MIN = 128         # 元の透明度がこれ以上の画素だけを、見えている色として扱う
HOLE_MIN_AREA = 0.001     # 穴とみなす囲まれた領域の最小面積（画像全体に対する割合）
HOLE_COLOR_MARGIN = 1.5   # 穴とみなす領域の色が、外周のばらつきからずれてよい幅
EDGE_WINDOW = 5          # 輪郭の不透明度を決めるとき、近くの前景の色を探す範囲（画素）
MIN_CONTRAST = 32        # 前景と背景の色差がこれ未満の場所では、不透明度の推定をしない
SEAL_GAP = 1             # 背景が通り抜けられない隙間の幅（片側の画素数）。線の切れ目から塗りの中へ流れ込むのを防ぐ
THIN_CREEP_STEPS = 12    # 毛先の間などの細い入り江を奥へ辿る回数の上限


def _edge(a):
    return np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])


def background_color(rgb, existing=None):
    """(縁の最頻色, 縁に占めるその色の割合) を返す。existing があれば、透明で見えていない色は数えない"""
    edge = _edge(rgb).astype(int)
    if existing is not None:
        visible = _edge(existing) >= OPAQUE_MIN
        if visible.any():
            edge = edge[visible]
    q = (edge // COLOR_STEP) * COLOR_STEP + COLOR_STEP // 2
    colors, counts = np.unique(q, axis=0, return_counts=True)
    return colors[counts.argmax()].astype(np.uint8), float(counts.max() / len(edge))


def is_uniform_background(rgb):
    return background_color(rgb)[1] >= UNIFORM_SHARE


def _ring_spread(dist):
    ring = np.concatenate([dist[:RING_WIDTH].ravel(), dist[-RING_WIDTH:].ravel(),
                           dist[:, :RING_WIDTH].ravel(), dist[:, -RING_WIDTH:].ravel()])
    # 絵が外周にかかっていると、その画素がばらつきを押し上げて許容差が上限に張り付く。
    # 背景としてあり得る範囲の画素だけで測る
    ring = ring[ring <= AUTO_MAX]
    return np.percentile(ring, 99.5) if ring.size else AUTO_MAX


def _outer_region(near):
    """背景色の領域のうち、画像の縁からつながっている部分を返す

    背景色の領域を少し痩せさせてから縁とのつながりを調べ、届いた範囲を同じだけ太らせて戻す。
    痩せさせた時点で消える細い通り道（輪郭線の切れ目）は越えられないので、
    線が1〜2画素途切れているだけで白い紙や服の中まで透明になる、ということが起きない
    """
    kernel = np.ones((2 * SEAL_GAP + 1,) * 2, np.uint8)
    gate = cv2.erode(near, kernel)
    _, labels = cv2.connectedComponents(gate, connectivity=8)
    border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    reached = np.isin(labels, border[border != 0]).astype(np.uint8)
    reached = cv2.dilate(reached, kernel) & near

    # 毛先の間のような細い入り江は痩せさせた時点で消えるので、細い部分だけを伝って奥まで広げる。
    # 太い部分へは出られないので、線の切れ目を抜けた先には広がらない
    thin = near & ~cv2.dilate(gate, kernel)
    for _ in range(THIN_CREEP_STEPS):
        spread = reached | (cv2.dilate(reached, kernel) & thin)
        if np.array_equal(spread, reached):
            break
        reached = spread
    return reached.astype(bool)


def _soft_band(remove, dist):
    """半透明にしてよい輪郭の画素。背景から前景へ色差が増えていく範囲だけで、線の頂点を越えた内側は含まない

    細い線のすぐ内側の淡い塗りは、線より色差が小さいので、ここで止めないと半透明になって欠ける
    """
    cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    d = dist.astype(np.float32)
    front = (cv2.dilate(remove.astype(np.uint8), cross) > 0) & ~remove
    band = front.copy()
    for _ in range(EDGE_WINDOW // 2):
        prev = cv2.dilate(np.where(front, d, -1).astype(np.float32), cross)   # 隣の前線の、最大の色差
        front = (prev >= 0) & ~band & ~remove & (d >= prev)
        if not front.any():
            break
        band |= front
    return band


def cutout(rgb, tolerance=DEFAULT_TOLERANCE, holes=False, toggles=(), existing=None):
    """(背景色の混ざりを除いたRGB, アルファ 0〜255, 使った許容差) を返す

    tolerance: 背景色とみなす色の差。None なら自動で決める
    holes: 縁とつながっていない背景色の領域のうち、背景色と同じ色で大きいもの（腕と体の間など）も透明にする。
           白い胸など、背景色と同じ色の大きな塗りも消えるので、既定では使わない
    toggles: 透明にするかどうかを反転させる位置 (x, y) の並び。クリックで指定した例外
    existing: 元画像のアルファ。透明な画素の RGB は見えていないので、背景色の判定に使わない
    """
    opaque = None if existing is None else existing >= OPAQUE_MIN
    if opaque is not None and _edge(opaque).mean() < UNIFORM_SHARE:   # 背景はすでに透明で、消すものがない
        return rgb.copy(), np.full(rgb.shape[:2], 255, np.uint8), AUTO_MIN if tolerance is None else tolerance
    bg, _ = background_color(rgb, existing)
    dist = np.abs(rgb.astype(int) - bg.astype(int)).max(2)
    spread = _ring_spread(dist)
    if tolerance is None:
        tolerance = int(min(AUTO_MAX, max(AUTO_MIN, spread * 2 + AUTO_MARGIN)))
    if opaque is not None:   # 透明な部分は背景とつながる
        dist = np.where(opaque, dist, 0)
    near = (dist <= tolerance).astype(np.uint8)

    # 背景色の領域のうち、画像の縁からつながっている部分を背景とする。
    # 残り（囲まれた領域）と分けて番号を振り、それぞれクリックで反転できるようにする
    outer = _outer_region(near)
    n_outer, outer_labels = cv2.connectedComponents(outer.astype(np.uint8), connectivity=8)
    outer_removed = np.ones(n_outer, dtype=bool)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(near & ~outer.astype(np.uint8), connectivity=8)
    removed = np.zeros(n, dtype=bool)
    if holes:
        # 囲まれた領域は、背景色とほぼ同じ色で、十分に大きいものだけを穴とみなす
        # （目のハイライトや毛の白い部分は小さいか、背景色と少し違う）
        mean_dist = np.bincount(labels.ravel(), weights=dist.ravel(), minlength=n) / np.maximum(stats[:, cv2.CC_STAT_AREA], 1)
        big = stats[:, cv2.CC_STAT_AREA] >= HOLE_MIN_AREA * dist.size
        removed |= big & (mean_dist <= spread + HOLE_COLOR_MARGIN)
    for x, y in toggles:
        if not (0 <= y < labels.shape[0] and 0 <= x < labels.shape[1]):
            continue
        if outer_labels[y, x]:
            outer_removed[outer_labels[y, x]] = not outer_removed[outer_labels[y, x]]
        elif labels[y, x]:
            removed[labels[y, x]] = not removed[labels[y, x]]
    removed[0] = outer_removed[0] = False
    remove = removed[labels] | outer_removed[outer_labels]

    # 背景に接する輪郭の画素は、近くの前景との色差に対する割合で不透明度を決める
    # （線と背景の中間色の画素なら、線の色が何割混ざっているかがそのまま不透明度になる）
    band = _soft_band(remove, dist)
    contrast = ndimage.maximum_filter(dist, size=EDGE_WINDOW)
    ratio = (dist - tolerance) / np.maximum(contrast - tolerance, 1)
    alpha = np.ones_like(dist, dtype=np.float32)
    alpha[remove] = 0
    soft = band & (contrast >= MIN_CONTRAST)
    alpha[soft] = np.clip(ratio[soft], 0, 1)

    # 半透明の画素から背景色の混ざりを取り除く（前景色 = (混色 − 背景色×(1−α)) / α）
    out = rgb.astype(np.float32)
    a = alpha[soft][:, None]
    out[soft] = np.clip((out[soft] - bg * (1 - a)) / np.maximum(a, 0.05), 0, 255)
    return np.round(out).astype(np.uint8), np.round(alpha * 255).astype(np.uint8), tolerance


def merge_alpha(alpha, existing):
    """元画像にあった透明部分を保つ"""
    if existing is None:
        return alpha
    return (alpha.astype(np.uint16) * existing // 255).astype(np.uint8)
