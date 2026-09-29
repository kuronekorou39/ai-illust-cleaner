"""線画アイコン（SVGを色指定で描画）"""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

_SVG = {
    'open': '<path d="M2 5h5l1.5 2H16v8H2z"/>',
    'compare': '<rect x="2" y="3" width="14" height="12"/><path d="M9 1v16"/>',
    'batch': '<rect x="2" y="2" width="10" height="10"/><path d="M6 16h10V6"/>',
    'inspect': '<circle cx="8" cy="8" r="5"/><path d="M12 12l4 4"/>',
    'adjust': '<path d="M2 5h5M11 5h5M2 13h2M8 13h8"/><circle cx="9" cy="5" r="2"/><circle cx="6" cy="13" r="2"/>',
    'minus': '<path d="M4 9h10"/>',
    'plus': '<path d="M4 9h10M9 4v10"/>',
    'image': '<rect x="2" y="3" width="14" height="12"/><path d="M2 12l4-4 3 3 3-2 4 3"/>',
}
_RENDER_SCALE = 3   # 高DPIでもぼけないよう大きめに描く


def pixmap(name, color=theme.TEXT, size=18):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18" fill="none" '
           f'stroke="{color}" stroke-width="1.4">{_SVG[name]}</svg>')
    pm = QPixmap(size * _RENDER_SCALE, size * _RENDER_SCALE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode())).render(p)
    p.end()
    pm.setDevicePixelRatio(_RENDER_SCALE)
    return pm


def icon(name, size=18):
    """通常時は控えめ、選択・ホバー時は明るい色になるアイコン"""
    ic = QIcon()
    ic.addPixmap(pixmap(name, theme.MUTED, size), QIcon.Normal, QIcon.Off)
    ic.addPixmap(pixmap(name, theme.TEXT, size), QIcon.Normal, QIcon.On)
    ic.addPixmap(pixmap(name, theme.TEXT, size), QIcon.Active, QIcon.Off)
    ic.addPixmap(pixmap(name, theme.DISABLED, size), QIcon.Disabled, QIcon.Off)
    return ic


def app_icon():
    """アプリのアイコン：半分だけ塗った円（処理前後の比較を表す）"""
    ic = QIcon()
    for size in (16, 32, 48, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size / 16, size / 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE))
        p.drawRoundedRect(QRectF(0, 0, 16, 16), 3.5, 3.5)
        half = QPainterPath()
        half.moveTo(8, 3.5)
        half.arcTo(QRectF(3.5, 3.5, 9, 9), 90, -180)
        half.closeSubpath()
        p.setBrush(QColor(theme.ACCENT))
        p.drawPath(half)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT), 1.3))
        p.drawEllipse(QRectF(3.5, 3.5, 9, 9))
        p.end()
        ic.addPixmap(pm)
    return ic
