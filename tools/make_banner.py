"""README の先頭と GitHub の共有画像に使うバナー（1280×640）を作る

使い方: python tools/make_banner.py [出力.png]   # 既定は docs/banner.png（画面は docs/main.png を使う）
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
FEATURES = ('メタデータの確認', '塗り面ノイズの可視化と除去', '単色背景の透過')

app = QApplication.instance() or QApplication(sys.argv)
theme.load_fonts()


def font(family, size, weight=QFont.Normal):
    f = QFont(family)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


img = QImage(W, H, QImage.Format_ARGB32)
img.fill(QColor(theme.BG))
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
p.setPen(QPen(QColor(theme.BORDER_STRONG), 2))
p.setBrush(Qt.NoBrush)
p.drawRoundedRect(frame, 14, 14)

# 左：アイコン・名前・できること
p.drawPixmap(MARGIN, 150, icons.app_pixmap(88))
p.setPen(QColor(theme.TEXT))
p.setFont(font(theme.FONT_MONO, 46, QFont.Medium))
p.drawText(QPointF(MARGIN, 318), 'ai-illust-cleaner')
p.setFont(font(theme.FONT_UI, 21, QFont.Medium))
for i, text in enumerate(FEATURES):
    y = 392 + i * 42
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    p.drawEllipse(QPointF(MARGIN + 6, y - 7), 5, 5)
    p.setPen(QColor(theme.MUTED))
    p.drawText(QPointF(MARGIN + 24, y), text)
p.setFont(font(theme.FONT_MONO, 16))
p.setPen(QColor(theme.DISABLED))
p.drawText(QPointF(MARGIN, H - 56), 'Windows  ·  MIT License')

p.end()
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'docs' / 'banner.png'
out.parent.mkdir(parents=True, exist_ok=True)
img.save(str(out))
print('saved:', out)
