"""共通の部品"""
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QSlider, QToolTip, QVBoxLayout, QWidget)

from . import icons, theme


def label(text='', role=None):
    lb = QLabel(text)
    if role:
        lb.setProperty('role', role)
    return lb


def button(text, kind=None, checkable=False):
    b = QPushButton(text)
    if kind:
        b.setProperty('kind', kind)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    return b


def icon_button(name, tooltip):
    b = button('', 'icon')
    b.setIcon(icons.icon(name, 16))
    b.setIconSize(QSize(16, 16))
    b.setToolTip(tooltip)
    b.setAccessibleName(tooltip)
    return b


def help_button(text, name):
    """説明を出す「？」ボタン。マウスを乗せるか、押すと表示する"""
    b = button('?', 'help')
    b.setFixedSize(18, 18)
    b.setToolTip(text)
    b.setAccessibleName(name)
    b.setAccessibleDescription(text)
    b.clicked.connect(lambda: QToolTip.showText(QCursor.pos(), text, b))
    return b


class ChoiceGrid(QWidget):
    """排他選択のボタンを格子状に並べる。options は (キー, 表示名, 説明) の並び"""
    changed = Signal(str)

    def __init__(self, options, columns):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self._group = QButtonGroup(self)
        self._buttons = {}
        for i, (key, text, help_text) in enumerate(options):
            b = button(text, 'preset', checkable=True)
            b.setToolTip(help_text)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._group.addButton(b)
            self._buttons[key] = b
            grid.addWidget(b, i // columns, i % columns)
        self._value = options[0][0]
        self._buttons[self._value].setChecked(True)

    def value(self):
        return self._value

    def set_value(self, key):
        self._value = key
        self._buttons[key].setChecked(True)

    def _pick(self, key):
        if key != self._value:
            self.set_value(key)
            self.changed.emit(key)


class Segmented(QFrame):
    """排他選択の切り替えボタン"""
    changed = Signal(str)

    def __init__(self, options, floating=False):
        super().__init__()
        self.setObjectName('segmented')
        self.setProperty('floating', floating)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._buttons = {}
        for key, text in options:
            b = button(text, 'seg', checkable=True)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(b)
            self._buttons[key] = b
            lay.addWidget(b)
        next(iter(self._buttons.values())).setChecked(True)

    def value(self):
        return next(k for k, b in self._buttons.items() if b.isChecked())

    def set_value(self, key):
        self._buttons[key].setChecked(True)



class SliderRow(QWidget):
    """名前・現在値・スライダーの組"""
    changed = Signal(int)
    released = Signal()

    def __init__(self, text, minimum, maximum, value, fmt=str, big_value=False):
        super().__init__()
        self._fmt = fmt
        self.name = label(text, 'section' if big_value else None)
        self.value_label = label(fmt(value), 'bigValue' if big_value else 'mono')
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.slider.setAccessibleName(text)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.valueChanged.connect(self._on_change)
        self.slider.sliderReleased.connect(self.released)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.name, 0, Qt.AlignBottom)
        head.addStretch(1)
        head.addWidget(self.value_label, 0, Qt.AlignBottom)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8 if big_value else 6)
        lay.addLayout(head)
        lay.addWidget(self.slider)

    def _on_change(self, v):
        self.value_label.setText(self._fmt(v))
        self.changed.emit(v)

    def value(self):
        return self.slider.value()

    def set_value(self, v):
        self.slider.setValue(v)


class StatTile(QFrame):
    """小さな数値表示"""

    def __init__(self, title):
        super().__init__()
        self.setObjectName('tile')
        self._value = label('—', 'tileValue')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        lay.addWidget(label(title, 'small'))
        lay.addWidget(self._value)

    def set_value(self, text):
        self._value.setText(text)


class MetricBars(QWidget):
    """処理前と処理後の値を横棒で並べる"""
    ROW_HEIGHT = 16
    BAR_HEIGHT = 8
    TITLE_HEIGHT = 24
    VALUE_WIDTH = 62
    COLORS = (theme.BAR_BEFORE, theme.ACCENT)
    VALUE_COLORS = (theme.MUTED, theme.ACCENT)

    def __init__(self, title, fmt):
        super().__init__()
        self._title = title
        self._fmt = fmt
        self._values = [None, None]   # 処理前・処理後
        self.setFixedHeight(self.TITLE_HEIGHT + self.ROW_HEIGHT * len(self._values))
        self.setAccessibleName(title)

    def set_values(self, before=None, after=None):
        self._values = [before, after]
        shown = ['—' if v is None else self._fmt(v) for v in self._values]
        self.setAccessibleDescription(f'処理前 {shown[0]}、処理後 {shown[1]}')
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(theme.TEXT))
        p.setFont(QFont(theme.FONT_UI, 10))
        p.drawText(QRectF(0, 0, self.width(), self.TITLE_HEIGHT - 4), Qt.AlignLeft | Qt.AlignVCenter, self._title)

        top = max((v for v in self._values if v is not None), default=0)
        bar_width = self.width() - self.VALUE_WIDTH
        mono = QFont(theme.FONT_MONO, 10)
        for i, v in enumerate(self._values):
            y = self.TITLE_HEIGHT + i * self.ROW_HEIGHT
            track = QRectF(0, y + (self.ROW_HEIGHT - self.BAR_HEIGHT) / 2, bar_width, self.BAR_HEIGHT)
            p.fillRect(track, QColor(theme.BG))
            if v is not None and top > 0:
                fill = QRectF(track)
                fill.setWidth(max(2.0, bar_width * v / top))
                p.fillRect(fill, QColor(self.COLORS[i]))
            p.setFont(mono)
            p.setPen(QColor(self.VALUE_COLORS[i] if v is not None else theme.DISABLED))
            p.drawText(QRectF(bar_width, y, self.VALUE_WIDTH, self.ROW_HEIGHT), Qt.AlignRight | Qt.AlignVCenter,
                       '—' if v is None else self._fmt(v))
        p.end()


class Legend(QWidget):
    """棒グラフの凡例"""

    def __init__(self, items):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        for color, text in items:
            swatch = QFrame()
            swatch.setFixedSize(8, 8)
            swatch.setStyleSheet(f'background: {color};')
            item = QHBoxLayout()
            item.setSpacing(4)
            item.addWidget(swatch)
            item.addWidget(label(text, 'small'))
            lay.addLayout(item)
