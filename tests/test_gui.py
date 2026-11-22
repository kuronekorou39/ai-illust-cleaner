"""画面まわり：裏での読み込み・壊れたファイル・メモリ不足の表示・大きな画像の知らせ・2分割"""
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui import errors, window as window_mod  # noqa: E402
from gui.viewer import PAIR_LAYERS, Viewer  # noqa: E402


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, monkeypatch):
    warnings = []
    monkeypatch.setattr(window_mod.QMessageBox, 'warning', lambda _p, _t, text: warnings.append(text))
    w = window_mod.MainWindow()
    w.warnings = warnings
    yield w
    w.close()


def _wait(win, until, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if until():
            return
        time.sleep(0.01)
    raise TimeoutError


def _png(tmp_path, name, size=(64, 48)):
    path = tmp_path / name
    rng = np.random.default_rng(0)
    Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)).save(path)
    return path


# ---------------------------------------------------------------- エラーの文

def test_out_of_memory_is_short():
    assert errors.summary(MemoryError()) == errors.OUT_OF_MEMORY
    assert errors.details(MemoryError()) == errors.OUT_OF_MEMORY
    assert errors.summary(cv2.error('Insufficient memory (Failed to allocate 1 bytes)')) == errors.OUT_OF_MEMORY


def test_other_errors_keep_details():
    try:
        raise ValueError('壊れています')
    except ValueError as e:
        assert errors.summary(e) == '壊れています'
        assert 'Traceback' in errors.details(e)


# ---------------------------------------------------------------- 読み込み

def test_open_broken_file_shows_message(win, tmp_path):
    bad = tmp_path / 'bad.png'
    bad.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 40)
    win.open_image(bad)
    _wait(win, lambda: win.warnings)
    assert '開けませんでした' in win.warnings[0]
    assert win.source is None
    _wait(win, win.is_idle)


def test_open_runs_in_background_and_keeps_latest(win, tmp_path):
    first, second = _png(tmp_path, 'a.png'), _png(tmp_path, 'b.png')
    win.open_image(first)
    win.open_image(second)   # 1枚目の読み込みが終わる前に2枚目を開く
    _wait(win, lambda: win.is_idle() and win.result is not None)
    assert Path(win.source.path).name == 'b.png'
    assert not win.warnings


def test_large_image_notice_only_while_processing(win, tmp_path, monkeypatch):
    monkeypatch.setattr(window_mod, 'LARGE_PIXELS', 1000)
    win.open_image(_png(tmp_path, 'big.png'))
    _wait(win, lambda: win.source is not None)
    assert win.viewer._notice == window_mod.LARGE_NOTICE
    _wait(win, lambda: win.is_idle() and win.result is not None)
    assert win.viewer._notice is None


# ---------------------------------------------------------------- 2分割

@pytest.mark.parametrize('size, stacked', [((400, 100), True), ((100, 400), False)])
def test_pair_layout_follows_image_shape(app, size, stacked):
    v = Viewer()
    v.resize(800, 600)
    img = QImage(*size, QImage.Format_RGB888)
    img.fill(0)
    v.set_source(img)
    v.set_mode('pair')
    cells = v._cells()
    assert len(cells) == 2
    assert (cells[0].width() == 800) == stacked   # 横長の画像は上下、縦長の画像は左右に並べる


def test_pair_choices(app):
    v = Viewer()
    v.set_mode('pair')
    for key, layers in PAIR_LAYERS.items():
        v.pair_seg.changed.emit(key)
        assert v._grid_layers() == layers
