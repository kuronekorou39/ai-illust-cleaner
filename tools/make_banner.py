"""README の先頭に置くバナー（1280×640、背景は透明）を、GitHub のライト表示用とダーク表示用の2枚作る

使い方: python tools/make_banner.py   # docs/banner-light.png と docs/banner-dark.png（画面は docs/main.png を使う）
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication

from gui import icons, theme

W, H = 1280, 640
MARGIN = 72
FEATURES = ('メタデータの確認と消去', '塗り面ノイズの可視化と除去', '単色背景の透過')

# 表示ごとの文字色（名前, 機能, 下の小さな文字, 画面の枠）
PALETTES = {
    'dark': (theme.TEXT, theme.MUTED, theme.DISABLED, theme.BORDER_STRONG),
    'light': ('#1f1d1a', '#57534c', '#8a847a', '#d6d2ca'),
}

app = QApplication.instance() or QApplication(sys.argv)
theme.load_fonts()


def font(family, size, weight=QFont.Normal):
    f = QFont(family)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


for name, (title_color, text_color, note_color, frame_color) in PALETTES.items():
    img = QImage(W, H, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    # 右：実際の画面。右端と下端は切れてよい
    shot = QImage(str(ROOT / 'docs' / 'main.png'))
    shot = shot.scaledToWidth(760, Qt.SmoothTransformation)
    frame = QRectF(600, 96, shot.width(), shot.height())
    clip = QPainterPath()
    clip.addRoundedRect(frame, 14, 14)
    p.save()
    p.setClipPath(clip)
    p.drawImage(frame.topLeft(), shot)
    p.restore()
    p.setPen(QPen(QColor(frame_color), 2))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(frame, 14, 14)

    # 左：アイコン・名前・できること
    p.drawPixmap(MARGIN, 150, icons.app_pixmap(88))
    p.setPen(QColor(title_color))
    p.setFont(font(theme.FONT_MONO, 46, QFont.Medium))
    p.drawText(QPointF(MARGIN, 318), 'ai-illust-cleaner')
    p.setFont(font(theme.FONT_UI, 21, QFont.Medium))
    for i, text in enumerate(FEATURES):
        y = 392 + i * 42
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawEllipse(QPointF(MARGIN + 6, y - 7), 5, 5)
        p.setPen(QColor(text_color))
        p.drawText(QPointF(MARGIN + 24, y), text)
    p.setFont(font(theme.FONT_MONO, 16))
    p.setPen(QColor(note_color))
    p.drawText(QPointF(MARGIN, H - 56), 'Windows  ·  MIT License')

    p.end()
    out = ROOT / 'docs' / f'banner-{name}.png'
    img.save(str(out))
    print('saved:', out)
