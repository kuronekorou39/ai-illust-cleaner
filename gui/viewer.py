"""比較ビュー（スライド比較・4分割・抽出方式の4通り比較、拡大と移動）"""
import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from cleaner import noise
from . import icons, theme
from .widgets import Segmented, ZoomBar

ZOOM_STEP = 1.25
ZOOM_MIN, ZOOM_MAX = 0.05, 32.0
FIT_MARGIN = 24          # 全体表示のときに画像の周囲に空ける余白
SPLIT_GRAB = 14          # 比較線をつかめる左右の幅
HANDLE_RADIUS = 20
CELL_GAP = 2
MINIMAP_SIDE = 120
OVERLAY_MARGIN = 16
OVERLAY_TOP = 12        # 上部の切り替えボタンの位置
OVERLAY_GAP = 8
CHIP_ROOM = 150         # スライド比較の左右上のラベルに空けておく幅
NOISE_BG = '#101010'
CLICK_SLOP = 4          # これ以下の移動ならドラッグでなくクリックとみなす（画面上の画素）

# 複数の画面を並べるモードで、各画面に出すレイヤー
ALGO_LAYERS = {f'algo_{k}': k for k in noise.ALGOS}   # 抽出方式ごとの、元画像のノイズ
GRID_LAYERS = {
    'quad': ('orig', 'result', 'noise_orig', 'noise_result'),
    'algos': tuple(ALGO_LAYERS),
}
LABELS = {'orig': '元画像', 'result': '処理後', 'noise_orig': 'ノイズ（元画像）', 'noise_result': 'ノイズ（処理後）',
          **{layer: noise.ALGOS[k][0] for layer, k in ALGO_LAYERS.items()}}
AFTER_LAYERS = ('result', 'noise_result')


def _is_noise(name):
    return name.startswith(('noise', 'algo_'))


class Viewer(QWidget):
    zoomChanged = Signal(float)
    cursorMoved = Signal(int, int)   # 画像上の座標。画像の外では (-1, -1)
    openRequested = Signal()
    picked = Signal(int, int)        # 選択モードで画像上をクリックした座標
    algoPicked = Signal(str)         # 4通りの比較でクリックされた抽出方式

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(480, 360)
        self.setAccessibleName('比較ビュー')

        self._layers = {}
        self._patch = None
        self._size = None
        self._thumb = None
        self.mode = 'quad'
        self.compare_mode = 'quad'   # 4通りの比較を閉じたときに戻る表示
        self.noise_style = 'strength'
        self.current_algo = next(iter(noise.ALGOS))
        self._press_at = None        # クリックとドラッグを見分けるための、押した位置
        self.view = 'image'
        self.show_mask = False
        self.split = 0.5
        self.zoom = 1.0
        self.cx = self.cy = 0.0
        self._fitted = True
        self._drag = None
        self._drag_from = None
        self._cursor = None   # 画像上のカーソル位置
        self.pick_mode = False   # クリックした場所を通知するモード（透過の切り替え用）

        self.mode_seg = Segmented([('slide', 'スライド比較'), ('quad', '4分割')], floating=True)
        self.view_seg = Segmented([('image', '画像'), ('noise', 'ノイズ')], floating=True)
        for seg, width in ((self.mode_seg, 220), (self.view_seg, 160)):
            seg.setParent(self)
            seg.setFixedSize(width, 40)
            seg.setCursor(Qt.ArrowCursor)
        self.zoom_bar = ZoomBar(self)
        self.zoom_bar.setParent(self)
        self.zoom_bar.setFixedHeight(40)
        self.zoom_bar.setCursor(Qt.ArrowCursor)
        self.mode_seg.set_value(self.compare_mode)
        self.mode_seg.changed.connect(self.set_mode)
        self.view_seg.changed.connect(self.set_view)
        self._overlay_bottom = 0   # 上部の切り替えボタンの下端と左端（ラベルと重ならないようにするため）
        self._overlay_left = 0

        self._busy = False
        self._phase = 0.0
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(30)
        self._busy_timer.timeout.connect(self._tick)
        self._sync_overlays()

    # ---- 画像の設定 ----

    def set_source(self, qimage):
        """新しい画像を開く"""
        self._layers = {'orig': qimage}
        self._patch = None
        self._size = (qimage.width(), qimage.height())
        self._update_thumb()
        self._sync_overlays()
        self.fit()

    def set_layer(self, name, qimage):
        self._layers[name] = qimage
        if name == 'result':
            self._update_thumb()
        self.update()

    def set_patch(self, rect, display, noise, mask):
        """表示範囲だけ先に処理した結果を、全体の結果の上に重ねる。noise が None ならノイズは全体の結果のまま"""
        self._patch = {'rect': rect, 'result': display, 'noise_result': noise, 'mask': mask}
        self.update()

    def clear_patch(self):
        self._patch = None
        self.update()

    def has_image(self):
        return self._size is not None

    def _update_thumb(self):
        img = self._layers.get('result') or self._layers.get('orig')
        self._thumb = img.scaled(MINIMAP_SIDE, MINIMAP_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # ---- 表示の切り替え ----

    def set_mode(self, mode):
        self.mode = mode
        if mode != 'algos':
            self.compare_mode = mode
            self.mode_seg.set_value(mode)
        self._sync_overlays()
        if self._fitted:
            self.fit()
        self.update()

    def set_view(self, view):
        self.view = view
        self.view_seg.set_value(view)
        self._sync_overlays()
        self.update()

    def set_noise(self, algo, style):
        self.current_algo, self.noise_style = algo, style
        self.update()

    def set_show_mask(self, on):
        self.show_mask = on
        self.update()

    def set_pick_mode(self, on):
        self.pick_mode = on
        self.setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)

    def set_busy(self, busy):
        if busy == self._busy:
            return
        self._busy = busy
        if busy:
            self._busy_timer.start()
        else:
            self._busy_timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.02) % 1.0
        self.update(0, 0, self.width(), 3)

    def _sync_overlays(self):
        self.mode_seg.setVisible(self.has_image() and self.mode != 'algos')
        self.view_seg.setVisible(self.has_image() and self.mode == 'slide')
        self.zoom_bar.adjustSize()
        self.zoom_bar.setVisible(self.has_image())
        self._place_overlays()

    def _place_overlays(self):
        """上部中央に横一列で並べる。入りきらなければ2段にする"""
        widgets = [w for w in (self.mode_seg, self.view_seg, self.zoom_bar) if w.isVisibleTo(self)]
        span = lambda ws: sum(w.width() for w in ws) + OVERLAY_GAP * max(0, len(ws) - 1)
        rows = [widgets] if span(widgets) <= self.width() - 2 * OVERLAY_MARGIN else [[w] for w in widgets]
        y = OVERLAY_TOP
        self._overlay_bottom = 0
        self._overlay_left = self.width()
        for row in rows:
            if not row:
                continue
            x = (self.width() - span(row)) // 2
            self._overlay_left = min(self._overlay_left, x)
            for w in row:
                w.move(x, y)
                x += w.width() + OVERLAY_GAP
            self._overlay_bottom = y + max(w.height() for w in row)
            y = self._overlay_bottom + OVERLAY_GAP

    # ---- 拡大と移動 ----

    def _cells(self):
        w, h = self.width(), self.height()
        if self.mode == 'slide':
            return [QRectF(0, 0, w, h)]
        cw, ch = (w - CELL_GAP) / 2, (h - CELL_GAP) / 2
        return [QRectF(x, y, cw, ch) for y in (0, ch + CELL_GAP) for x in (0, cw + CELL_GAP)]

    def _cell_at(self, pos):
        for cell in self._cells():
            if cell.contains(pos):
                return cell
        return self._cells()[0]

    def _origin(self, cell):
        """セル内で画像の左上が来る位置"""
        c = cell.center()
        return QPointF(c.x() - self.cx * self.zoom, c.y() - self.cy * self.zoom)

    def _to_image(self, pos):
        o = self._origin(self._cell_at(pos))
        return (pos.x() - o.x()) / self.zoom, (pos.y() - o.y()) / self.zoom

    def fit(self):
        if not self.has_image():
            return
        cell = self._cells()[0]
        w, h = self._size
        self.zoom = max(ZOOM_MIN, min((cell.width() - FIT_MARGIN * 2) / w, (cell.height() - FIT_MARGIN * 2) / h))
        self.cx, self.cy = w / 2, h / 2
        self._fitted = True
        self.zoomChanged.emit(self.zoom)
        self.update()

    def zoom_by(self, factor, anchor=None):
        """anchor（ウィジェット上の位置）の下にある画素を動かさずに拡大・縮小する"""
        if not self.has_image():
            return
        zoom = min(ZOOM_MAX, max(ZOOM_MIN, self.zoom * factor))
        if anchor is not None:
            cell = self._cell_at(anchor)
            ix, iy = self._to_image(anchor)
            self.cx = ix - (anchor.x() - cell.center().x()) / zoom
            self.cy = iy - (anchor.y() - cell.center().y()) / zoom
        self.zoom = zoom
        self._fitted = False
        self._clamp_center()
        self.zoomChanged.emit(self.zoom)
        self.update()

    def zoom_in(self):
        self.zoom_by(ZOOM_STEP)

    def zoom_out(self):
        self.zoom_by(1 / ZOOM_STEP)

    def _clamp_center(self):
        w, h = self._size
        self.cx = min(max(self.cx, 0), w)
        self.cy = min(max(self.cy, 0), h)

    def visible_image_rect(self):
        """表示中の画像範囲 (x0, y0, x1, y1)。画像が見えていなければ None"""
        if not self.has_image():
            return None
        cell = self._cells()[0]
        w, h = self._size
        hw, hh = cell.width() / 2 / self.zoom, cell.height() / 2 / self.zoom
        x0, y0 = max(0, math.floor(self.cx - hw)), max(0, math.floor(self.cy - hh))
        x1, y1 = min(w, math.ceil(self.cx + hw)), min(h, math.ceil(self.cy + hh))
        if x1 <= x0 or y1 <= y0:
            return None
        return x0, y0, x1, y1

    def view_center(self):
        return self.cx, self.cy

    def _minimap_rect(self):
        if self.mode != 'slide' or self._thumb is None:
            return None
        rect = self.visible_image_rect()
        if rect is None or (rect[2] - rect[0], rect[3] - rect[1]) == self._size:
            return None
        return QRectF(OVERLAY_MARGIN, self.height() - OVERLAY_MARGIN - self._thumb.height(),
                      self._thumb.width(), self._thumb.height())

    # ---- マウス操作 ----

    def _split_x(self):
        return self.width() * self.split

    def _near_split(self, pos):
        return self.mode == 'slide' and abs(pos.x() - self._split_x()) <= SPLIT_GRAB

    def _center_from_minimap(self, pos, mini):
        w, h = self._size
        self.cx = (pos.x() - mini.left()) / mini.width() * w
        self.cy = (pos.y() - mini.top()) / mini.height() * h
        self._clamp_center()
        self._fitted = False
        self.update()

    def mousePressEvent(self, e):
        if not self.has_image():
            if e.button() == Qt.LeftButton:
                self.openRequested.emit()
            return
        if e.button() not in (Qt.LeftButton, Qt.MiddleButton):
            return
        pos = e.position()
        mini = self._minimap_rect()
        if e.button() == Qt.LeftButton and self.pick_mode:
            ix, iy = self._to_image(pos)
            w, h = self._size
            if 0 <= ix < w and 0 <= iy < h:
                self.picked.emit(int(ix), int(iy))
            return
        if e.button() == Qt.LeftButton and mini is not None and mini.contains(pos):
            self._drag = 'minimap'
            self._center_from_minimap(pos, mini)
        elif e.button() == Qt.LeftButton and self._near_split(pos):
            self._drag = 'split'
        else:
            self._drag = 'pan'
            self.setCursor(Qt.ClosedHandCursor)
        self._drag_from = pos
        self._press_at = pos

    def mouseMoveEvent(self, e):
        if not self.has_image():
            self.setCursor(Qt.PointingHandCursor)
            return
        pos = e.position()
        if self._drag == 'split':
            self.split = min(1.0, max(0.0, pos.x() / self.width()))
        elif self._drag == 'pan':
            d = pos - self._drag_from
            self._drag_from = pos
            self.cx -= d.x() / self.zoom
            self.cy -= d.y() / self.zoom
            self._clamp_center()
            self._fitted = False
        elif self._drag == 'minimap':
            self._center_from_minimap(pos, self._minimap_rect() or QRectF(0, 0, 1, 1))
        elif self.pick_mode:
            self.setCursor(Qt.CrossCursor)
        else:
            mini = self._minimap_rect()
            if mini is not None and mini.contains(pos):
                self.setCursor(Qt.PointingHandCursor)
            elif self._near_split(pos):
                self.setCursor(Qt.SplitHCursor)
            else:
                self.setCursor(Qt.OpenHandCursor)

        ix, iy = self._to_image(pos)
        w, h = self._size
        inside = 0 <= ix < w and 0 <= iy < h
        self._cursor = (ix, iy) if inside else None
        self.cursorMoved.emit(int(ix) if inside else -1, int(iy) if inside else -1)
        self.update()

    def mouseReleaseEvent(self, e):
        pos, start = e.position(), self._press_at
        self._press_at = None
        if (self.mode == 'algos' and self._drag == 'pan' and start is not None
                and (pos - start).manhattanLength() <= CLICK_SLOP):
            cells = self._cells()
            for cell, name in zip(cells, GRID_LAYERS['algos']):
                if cell.contains(pos):
                    self.algoPicked.emit(ALGO_LAYERS[name])
                    break
        self._drag = None
        if self.has_image():
            self.setCursor(Qt.CrossCursor if self.pick_mode else Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, e):
        if self.has_image() and not self._near_split(e.position()):
            self.fit()

    def leaveEvent(self, e):
        self._cursor = None
        self.cursorMoved.emit(-1, -1)
        self.update()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120
        if steps:
            self.zoom_by(ZOOM_STEP ** steps, e.position())

    def resizeEvent(self, e):
        self._place_overlays()
        if self._fitted:
            self.fit()

    # ---- 描画 ----

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.CANVAS))
        if not self.has_image():
            self._paint_empty(p)
        elif self.mode == 'slide':
            self._paint_slide(p)
        else:
            self._paint_grid(p)
        if self._patch is not None:
            # 切り出し処理は全体処理と結果が少し異なるため、仮の表示であることを示す
            p.setRenderHint(QPainter.Antialiasing)
            self._chip(p, 'プレビュー（全体を処理中）', QPointF(self.width() - OVERLAY_MARGIN, self.height() - OVERLAY_MARGIN),
                       theme.MUTED, right_aligned=True, bottom=True)
        if self._busy:
            self._paint_busy(p)
        p.end()

    def _draw_layer(self, p, cell, clip, name):
        """cell を基準の座標として、clip の範囲にレイヤーを描く"""
        img = self._layers.get(name)
        if img is None and name == 'result':
            img = self._layers.get('orig')
        p.save()
        p.setClipRect(clip)
        if _is_noise(name):
            p.fillRect(clip, QColor(NOISE_BG))
        if img is not None:
            z = self.zoom
            o = self._origin(cell)
            p.setRenderHint(QPainter.SmoothPixmapTransform, z < 1.0)
            w, h = self._size
            vis = QRectF(o.x(), o.y(), w * z, h * z).intersected(clip)
            if not vis.isEmpty():
                sx0 = max(0, math.floor((vis.left() - o.x()) / z))
                sy0 = max(0, math.floor((vis.top() - o.y()) / z))
                sx1 = min(w, math.ceil((vis.right() - o.x()) / z))
                sy1 = min(h, math.ceil((vis.bottom() - o.y()) / z))
                src = QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0)
                p.drawImage(QRectF(o.x() + sx0 * z, o.y() + sy0 * z, src.width() * z, src.height() * z), img, src)
                self._draw_patch(p, o, name)
                if self.show_mask and not _is_noise(name):
                    self._draw_mask(p, o)
        p.restore()

    def _patch_target(self, o):
        x0, y0, x1, y1 = self._patch['rect']
        z = self.zoom
        return QRectF(o.x() + x0 * z, o.y() + y0 * z, (x1 - x0) * z, (y1 - y0) * z)

    def _draw_patch(self, p, o, name):
        if self._patch is not None and name in AFTER_LAYERS and self._patch[name] is not None:
            p.drawImage(self._patch_target(o), self._patch[name])

    def _draw_mask(self, p, o):
        mask = self._layers.get('mask')
        w, h = self._size
        z = self.zoom
        if self._patch is None and mask is not None:
            p.drawImage(QRectF(o.x(), o.y(), w * z, h * z), mask)
        elif self._patch is not None:
            p.drawImage(self._patch_target(o), self._patch['mask'])

    def _paint_slide(self, p):
        cell = self._cells()[0]
        sx = self._split_x()
        left, right = ('noise_orig', 'noise_result') if self.view == 'noise' else ('orig', 'result')
        self._draw_layer(p, cell, QRectF(0, 0, sx, cell.height()), left)
        self._draw_layer(p, cell, QRectF(sx, 0, cell.width() - sx, cell.height()), right)

        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor(theme.ACCENT)
        p.fillRect(QRectF(sx - 1, 0, 2, cell.height()), accent)
        c = QPointF(sx, cell.height() / 2)
        p.setPen(QPen(accent, 2))
        p.setBrush(QColor(theme.SURFACE))
        p.drawEllipse(c, HANDLE_RADIUS - 1, HANDLE_RADIUS - 1)
        arrows = QPainterPath()
        for s in (-1, 1):
            arrows.moveTo(c.x() + s * 3, c.y() - 5)
            arrows.lineTo(c.x() + s * 8, c.y())
            arrows.lineTo(c.x() + s * 3, c.y() + 5)
        p.setPen(QPen(accent, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(arrows)

        # 幅が狭いときは、上部中央の切り替えボタンと重ならないよう、その下に置く
        top = OVERLAY_MARGIN if self._overlay_left >= CHIP_ROOM else self._overlay_bottom + OVERLAY_GAP
        self._chip(p, LABELS[left], QPointF(OVERLAY_MARGIN, top), theme.TEXT)
        self._chip(p, LABELS[right], QPointF(cell.width() - OVERLAY_MARGIN, top), theme.ACCENT, right_aligned=True)
        self._paint_minimap(p)

    def _paint_grid(self, p):
        p.fillRect(self.rect(), QColor(theme.BORDER))
        for cell, name in zip(self._cells(), GRID_LAYERS[self.mode]):
            p.fillRect(cell, QColor(theme.CANVAS))
            self._draw_layer(p, cell, cell, name)
            spectrum = _is_noise(name) and self.noise_style == 'spectrum'
            if self._cursor is not None and not spectrum:   # 同じ位置を見比べるための十字線
                o = self._origin(cell)
                x = o.x() + self._cursor[0] * self.zoom
                y = o.y() + self._cursor[1] * self.zoom
                p.save()
                p.setClipRect(cell)
                p.setPen(QPen(QColor(236, 232, 225, 80) if _is_noise(name) else QColor(18, 17, 16, 90), 1))
                p.drawLine(QPointF(x, cell.top()), QPointF(x, cell.bottom()))
                p.drawLine(QPointF(cell.left(), y), QPointF(cell.right(), y))
                p.restore()
            p.setRenderHint(QPainter.Antialiasing)
            chosen = ALGO_LAYERS.get(name) == self.current_algo
            color = theme.ACCENT if name in AFTER_LAYERS or chosen else theme.TEXT
            text = LABELS[name] + ('（選択中）' if chosen else '')
            self._chip(p, text, QPointF(cell.left() + 12, cell.bottom() - 12), color, bottom=True)
            p.setRenderHint(QPainter.Antialiasing, False)
        if self.mode == 'algos':
            p.setRenderHint(QPainter.Antialiasing)
            self._chip(p, 'クリックで抽出方式を選ぶ', QPointF(OVERLAY_MARGIN, OVERLAY_MARGIN), theme.ACCENT)

    def _chip(self, p, text, anchor, color, right_aligned=False, bottom=False):
        font = QFont(theme.FONT_UI, 9)
        font.setWeight(QFont.Medium)
        p.setFont(font)
        tw = p.fontMetrics().horizontalAdvance(text)
        w, h = tw + 20, 26
        x = anchor.x() - w if right_aligned else anchor.x()
        y = anchor.y() - h if bottom else anchor.y()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*theme.CHIP_BG))
        p.drawRoundedRect(QRectF(x, y, w, h), 4, 4)
        p.setPen(QColor(color))
        p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)

    def _paint_minimap(self, p):
        mini = self._minimap_rect()
        if mini is None:
            return
        p.setRenderHint(QPainter.Antialiasing, False)
        p.drawImage(mini.topLeft(), self._thumb)
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(mini.adjusted(-0.5, -0.5, 0.5, 0.5))
        x0, y0, x1, y1 = self.visible_image_rect()
        w, h = self._size
        p.setPen(QPen(QColor(theme.ACCENT), 1.5))
        p.drawRect(QRectF(mini.left() + x0 / w * mini.width(), mini.top() + y0 / h * mini.height(),
                          (x1 - x0) / w * mini.width(), (y1 - y0) / h * mini.height()))

    def _paint_empty(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(0, 0, 440, 240)
        box.moveCenter(QPointF(self.rect().center()))
        pen = QPen(QColor(theme.BORDER_STRONG), 1.5, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 12, 12)
        c = box.center()
        p.drawPixmap(QPointF(c.x() - 18, c.y() - 66), icons.pixmap('image', theme.ACCENT, 36))
        title = QFont(theme.FONT_UI, 13)
        title.setWeight(QFont.Medium)
        p.setFont(title)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(box.left(), c.y() - 10, box.width(), 30), Qt.AlignCenter, '画像をここにドロップ')
        p.setFont(QFont(theme.FONT_UI, 10))
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(box.left(), c.y() + 24, box.width(), 24), Qt.AlignCenter, 'またはクリックしてファイルを選択')

    def _paint_busy(self, p):
        w = self.width()
        seg = w * 0.3
        x = -seg + (w + seg) * self._phase
        p.fillRect(QRectF(0, 0, w, 2), QColor(theme.BORDER))
        p.fillRect(QRectF(x, 0, seg, 2), QColor(theme.ACCENT))
