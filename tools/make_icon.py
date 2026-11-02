"""アプリのアイコン（gui/icons.py の app_icon と同じ絵）を exe 用の .ico に書き出す

使い方: python tools/make_icon.py [出力.ico]   # 既定は packaging/icon.ico
"""
import io
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from gui import icons

SIZES = (16, 24, 32, 48, 64, 128, 256)

out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'packaging' / 'icon.ico'
app = QApplication.instance() or QApplication(sys.argv)
images = []
for size in SIZES:   # 大きさごとに描き直す（縮小より小さいサイズがくっきりする）
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    icons.app_pixmap(size).save(buf, 'PNG')
    images.append(Image.open(io.BytesIO(bytes(buf.data()))).convert('RGBA'))
out.parent.mkdir(parents=True, exist_ok=True)
images[-1].save(out, sizes=[(s, s) for s in SIZES], append_images=images[:-1])
print('saved:', out)
