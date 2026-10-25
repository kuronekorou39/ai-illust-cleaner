"""GUIの見た目を確認するためのスクリーンショットを撮る

使い方: python tools/gui_shot.py [画像.png] [--out 出力フォルダ] [--center x,y] [--zoom 倍率]
空の状態・確認画面・4分割・スライド比較（画像／ノイズ）・線マスク表示を順に保存する
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QPointF, QTimer

from gui.__main__ import create_app
from gui.window import MainWindow

ap = argparse.ArgumentParser()
ap.add_argument('src', nargs='?', default='input/sd_img2img.png')
ap.add_argument('--out', default='output/gui')
ap.add_argument('--center', help='拡大表示の中心（画像座標 x,y）。省略時は左上寄り')
ap.add_argument('--zoom', type=float, default=3.0, help='拡大表示の倍率（1.0 で等倍）')
a = ap.parse_args()

app = create_app()
win = MainWindow()
win.show()
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def settle():
    """処理が終わって画面が落ち着くまで待つ"""
    wait(300)
    while not win.is_idle():
        wait(100)
    wait(300)


def shot(name):
    app.processEvents()
    win.grab().save(str(out / f'{name}.png'))
    print('saved:', out / f'{name}.png')


wait(300)
shot('0_empty')
win.open_image(a.src)
settle()
shot('1_inspect')

win.set_stage('adjust')
settle()
shot('1_quad_fit')

if a.center:
    win.viewer.cx, win.viewer.cy = map(float, a.center.split(','))
    win.viewer.zoom_by(a.zoom / win.viewer.zoom)
else:
    center = QPointF(win.viewer.width() * 0.25, win.viewer.height() * 0.25)
    win.viewer.zoom_by(a.zoom / win.viewer.zoom, center)
settle()
shot('2_quad_zoom')

win.viewer.set_mode('slide')
shot('3_slide_image')
win.viewer.set_view('noise')
shot('4_slide_noise')

win.viewer.set_view('image')
win.panel.show_mask.setChecked(True)
shot('5_slide_mask')
win.close()
