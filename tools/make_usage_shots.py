"""README の「使い方」の画面とバナーの元の画面を撮る（画面は表示しない）

使い方: uv run python tools/make_usage_shots.py
出力:   docs/usage_1_start.png・usage_2_open.png・usage_3_check.png・main.png
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, Qt, QTimer  # noqa: E402

from gui.__main__ import create_app  # noqa: E402
from gui.window import MainWindow  # noqa: E402

SAMPLE = ROOT / 'docs' / 'sample.png'
EYE = (980.0, 660.0)   # 拡大して見せる場所（sample.png の目）
CHECK_ZOOM = 4.0

app = create_app()
win = MainWindow()
win.setAttribute(Qt.WA_DontShowOnScreen)   # 実際の見た目のまま、画面には出さずに撮る
win.resize(1440, 900)
win.show()


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def settle():
    wait(300)
    while not win.is_idle():
        wait(100)
    wait(300)


def shot(name):
    app.processEvents()
    path = ROOT / 'docs' / f'{name}.png'
    win.grab().save(str(path))
    print('saved:', path)


wait(300)
shot('usage_1_start')

win.open_image(SAMPLE)
wait(300)
settle()
win.viewer.set_mode('pair')
win.viewer.set_pair('orig_noise')
settle()
shot('usage_2_open')

win.viewer.set_mode('slide')
win.viewer.set_view('noise')
win.viewer.cx, win.viewer.cy = EYE
win.viewer.zoom_by(CHECK_ZOOM / win.viewer.zoom)
settle()
shot('usage_3_check')

win.viewer.set_view('image')
win.viewer.set_mode('quad')
win.viewer.fit()
win.panel.cutout.setChecked(True)   # バナーには背景の透過の結果も写す
settle()
shot('main')
win.close()
