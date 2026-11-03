"""メインウィンドウ"""
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMessageBox,
                               QScrollArea, QVBoxLayout, QWidget)

import metadata as md
from denoise import load_image, save_clean, save_png
from . import __version__, icons, theme
from .dialogs import BATCH_SUFFIX, BatchDialog, MetaDialog, SaveDialog
from .imaging import composite_on_checker, mask_to_qimage, to_qimage
from .info import InfoPanel
from .panel import Panel
from .viewer import Viewer
from .widgets import button, label
from .winchrome import apply_dark_titlebar
from .worker import (FullProcessor, LatestWorker, PreviewProcessor, Source, analyze_source,
                     clamp_preview_rect, render_algos, render_noise)

APP_NAME = 'ai-illust-cleaner'
NO_META_SUFFIX = '_nometa'   # 元の画像のまま、メタデータだけ消して保存するときの既定のファイル名の末尾
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.jfif', '.jpe', '.webp', '.bmp', '.gif', '.tif', '.tiff')
OPEN_FILTER = ('画像 (' + ' '.join(f'*{s}' for s in IMAGE_SUFFIXES) + ');;すべてのファイル (*)')
FULL_DEBOUNCE_MS = 250    # スライダー操作が止まってから全体処理を始めるまでの待ち
STATUS_SHOW_MS = 6000     # 保存完了などの一時的なメッセージを出しておく時間


class Task(QThread):
    """関数を1回だけ裏で実行する"""
    done = Signal(object)

    def __init__(self, fn, arg, parent):
        super().__init__(parent)
        self._fn, self._arg = fn, arg
        self.finished.connect(self.deleteLater)

    def run(self):
        self.done.emit(self._fn(self._arg))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} {__version__}')
        self.setWindowIcon(icons.app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1180, 720)
        self.setAcceptDrops(True)

        self.source = None
        self.result = None          # 最後に表示した全体処理の結果
        self._algos_key = None      # 4通りの比較で表示中のノイズの (画像, 表示方式)
        self._save_choice = (True, None)   # 前回の保存の設定 (処理を反映するか, 消す種類)。None はすべて
        self._awaiting = False      # 現在の設定での全体処理の結果を待っているか
        self._analyzing = 0         # 実行中の指標計算の数
        self._toggles = ()          # 透過をクリックで反転させた位置（画像ごと）
        self._smoothed = (None, None)   # (条件, 平滑化結果)。プレビューで使い回す

        self.viewer = Viewer()
        self.info_panel = InfoPanel()
        self.panel = Panel()
        self._build()

        self.full = LatestWorker(FullProcessor(), self)
        self.preview = LatestWorker(PreviewProcessor(), self)
        self.full.done.connect(self._on_full)
        self.preview.done.connect(self._on_preview)
        for w in (self.full, self.preview):
            w.failed.connect(self._on_failed)
            w.start()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._submit_full)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.status.setText(''))

        self.panel.changed.connect(self._on_params)
        self.panel.maskToggled.connect(self.viewer.set_show_mask)
        self.panel.pick.toggled.connect(self.viewer.set_pick_mode)
        self.panel.pick_clear.clicked.connect(self._clear_toggles)
        self.viewer.picked.connect(self._on_picked)
        self.panel.noiseChanged.connect(self._on_noise_changed)
        self.panel.compareAlgos.connect(self._on_compare_algos)
        self.viewer.algoPicked.connect(self._on_algo_picked)
        self.info_panel.metaRequested.connect(self.open_meta)
        self.info_panel.openRequested.connect(self.choose_image)
        self.info_panel.batchRequested.connect(lambda: self.open_batch())
        self.viewer.openRequested.connect(self.choose_image)
        self._sync_enabled()

    # ---- 画面の組み立て ----

    def _build(self):
        # 左の列：ファイル・メタデータ・結果（縦に送れる）と、下に固定した保存
        self.status = label('', 'small')
        self.status.setWordWrap(True)
        self.save_btn = button('保存', 'primary')
        self.save_btn.clicked.connect(self.save)
        foot = QFrame()
        foot.setObjectName('leftFoot')
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(20, 12, 20, 20)
        fl.setSpacing(8)
        fl.addWidget(self.status)
        fl.addWidget(self.save_btn)
        left = QFrame()
        left.setObjectName('panel')
        left.setProperty('side', 'left')
        left.setFixedWidth(theme.LEFT_PANEL_WIDTH)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addWidget(self._scroll(self.info_panel), 1)
        ll.addWidget(foot)

        root = QWidget()
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(left)
        lay.addWidget(self.viewer, 1)
        right = self._scroll(self.panel)
        right.setFixedWidth(theme.PANEL_WIDTH)
        lay.addWidget(right)
        self.setCentralWidget(root)

    @staticmethod
    def _scroll(panel):
        """項目が多いパネルを、低い画面では縦に送れるようにする"""
        area = QScrollArea()
        area.setObjectName('panelScroll')
        area.setWidget(panel)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return area

    def showEvent(self, e):
        apply_dark_titlebar(self)
        super().showEvent(e)

    def _sync_enabled(self):
        has = self.source is not None
        self.panel.setEnabled(has)
        self.save_btn.setVisible(has)
        self.save_btn.setEnabled(has)
        self.viewer.set_busy(self._awaiting or self._analyzing > 0)

    def _say(self, text):
        self.status.setText(text)
        self._status_timer.start(STATUS_SHOW_MS)

    def is_idle(self):
        return not self._awaiting and self._analyzing == 0

    # ---- 画像を開く ----

    def choose_image(self):
        start = str(Path(self.source.path).parent) if self.source else ''
        path, _ = QFileDialog.getOpenFileName(self, '画像を開く', start, OPEN_FILTER)
        if path:
            self.open_image(path)

    def _load(self, path):
        try:
            meta, rgb, alpha = load_image(path)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f'画像を開けませんでした。\n{path}\n\n{e}')
            return None
        try:
            info = md.read(path)
        except Exception:   # メタデータが壊れていても、画像は開けるようにする
            info = md.Metadata()
        return Source(str(path), rgb, alpha, meta, info)

    def open_image(self, path):
        src = self._load(path)
        if src is None:
            return
        self.source = src
        self.result = None
        self._smoothed = (None, None)
        self._toggles = ()
        self.panel.set_toggle_count(0)
        self.panel.pick.setChecked(False)
        self.setWindowTitle(f'{Path(path).name} — {APP_NAME} {__version__}')
        self._awaiting = False
        self._debounce.stop()
        self.viewer.set_source(to_qimage(src.display))
        self.info_panel.set_source(src)
        self._algos_key = None
        self.panel.compare.setChecked(False)
        self._sync_enabled()
        self._run_task(analyze_source, (src, self.panel.noise_key()), self._on_analysis)
        self._on_params(False)   # 開いたらすぐノイズ除去する

    def _run_task(self, fn, arg, on_done):
        task = Task(fn, arg, self)
        task.done.connect(on_done)
        task.done.connect(self._on_task_done)
        self._analyzing += 1
        self._sync_enabled()
        task.start()

    def _on_task_done(self, _):
        self._analyzing -= 1
        self._sync_enabled()

    def _is_current(self, r):
        return self.source is not None and r['source_id'] == self.source.id

    def _on_analysis(self, r):
        if self._is_current(r):
            if r['noise_key'] == self.panel.noise_key():   # 違えば切り替え時の描き直しに任せる
                self.viewer.set_layer('noise_orig', to_qimage(r['noise']))
            self.info_panel.set_analysis(r['metrics'], r['uniform_bg'])

    # ---- ノイズの表示方式 ----

    def _on_noise_changed(self, *_):
        self.viewer.set_noise(*self.panel.noise_key())
        if self.source is None:
            return
        self.viewer.clear_patch()
        self._run_task(render_noise, (self.source, self.result, self.panel.noise_key()), self._on_noise_rendered)
        if self.viewer.mode == 'algos':
            self._render_algos()

    def _on_noise_rendered(self, r):
        if not self._is_current(r) or r['noise_key'] != self.panel.noise_key():
            return
        self.viewer.set_layer('noise_orig', to_qimage(r['orig']))
        if r['after'] is not None and r['result'] is self.result:
            self.viewer.set_layer('noise_result', to_qimage(r['after']))

    # ---- 抽出方式の4通り比較 ----

    def _on_compare_algos(self, on):
        if self.source is None:
            return
        if on:
            self.viewer.set_mode('algos')
            self._render_algos()
        else:
            self.viewer.set_mode(self.viewer.compare_mode)

    def _render_algos(self):
        key = (self.source.id, self.panel.noise_key()[1])
        if key != self._algos_key:
            self._algos_key = key
            self._run_task(render_algos, (self.source, key[1]), self._on_algos_rendered)

    def _on_algos_rendered(self, r):
        if self._is_current(r) and r['style'] == self.panel.noise_key()[1]:
            for k, rgb in r['layers'].items():
                self.viewer.set_layer(f'algo_{k}', to_qimage(rgb))

    def _on_algo_picked(self, key):
        self.panel.set_algo(key)
        self.panel.compare.setChecked(False)   # 選んだら元の表示に戻る

    # ---- 処理 ----

    def _params(self):
        return replace(self.panel.params(), toggles=self._toggles)

    def _on_picked(self, x, y):
        self._toggles = self._toggles + ((x, y),)
        self.panel.set_toggle_count(len(self._toggles))
        self._on_params(False)

    def _clear_toggles(self):
        self._toggles = ()
        self.panel.set_toggle_count(0)
        self._on_params(False)

    def _on_params(self, interactive):
        if self.source is None:
            return
        params = self._params()
        if self.result is not None and self.result['params'] == params:
            self._debounce.stop()
            self._awaiting = False
            self.viewer.clear_patch()
            self._sync_enabled()
            return
        self._awaiting = True
        self._sync_enabled()
        self._submit_preview(params)
        self._debounce.start(FULL_DEBOUNCE_MS if interactive else 0)

    def _submit_preview(self, params):
        rect = self.viewer.visible_image_rect()
        if rect is None:
            return
        rect = clamp_preview_rect(rect, self.viewer.view_center())
        key, smoothed = self._smoothed
        reuse = smoothed if key == (self.source.id, params.smooth_key) else None
        self.preview.submit((self.source, params, rect, reuse, self.panel.noise_key()))

    def _submit_full(self):
        if self.source is not None:
            self.full.submit((self.source, self._params(), self.panel.noise_key()))

    def _on_preview(self, r):
        current = self.source is not None and r['source_id'] == self.source.id
        if current and self._awaiting and r['params'] == self._params():
            view = r['display']
            last = self.result
            if r['params'].cutout and last is not None and last['params'].cutout_key == r['params'].cutout_key:
                # 透過の設定が変わっていなければ、前回の全体処理の透明部分を借りて見た目をそろえる
                x0, y0, x1, y1 = r['rect']
                view = composite_on_checker(view, last['alpha'][y0:y1, x0:x1], (x0, y0))
            fresh = r['noise'] is not None and r['noise_key'] == self.panel.noise_key()
            noise_img = to_qimage(r['noise']) if fresh else None
            self.viewer.set_patch(r['rect'], to_qimage(view), noise_img, mask_to_qimage(r['mask']))

    def _on_full(self, r):
        if self.source is None or r['source_id'] != self.source.id:
            return
        self._smoothed = ((r['source_id'], r['params'].smooth_key), r['smoothed'])
        if r['params'] != self._params():
            return   # 設定が変わった後に届いた古い結果。新しい結果を待つ
        self.result = r
        self._awaiting = False
        self.viewer.set_layer('result', to_qimage(r['view']))
        if r['noise_key'] == self.panel.noise_key():
            self.viewer.set_layer('noise_result', to_qimage(r['noise']))
        else:   # 処理中に表示方式が切り替わった
            self._on_noise_changed()
        self.viewer.set_layer('mask', mask_to_qimage(r['mask']))
        self.viewer.clear_patch()
        self.info_panel.set_metrics('after', r['metrics'])
        self.panel.show_tolerance(r['tolerance'])
        self._sync_enabled()

    def _on_failed(self, trace):
        self._awaiting = False
        self._sync_enabled()
        QMessageBox.warning(self, APP_NAME, f'処理中にエラーが起きました。\n\n{trace}')

    # ---- 保存・一括処理・メタデータ ----

    def save(self):
        """処理を反映するか、どの種類のメタデータを消すかを選んで保存する"""
        info = self.source.metadata
        ready = self.result is not None and not self._awaiting
        apply, remove = self._save_choice
        dlg = SaveDialog(self, info, ready, apply, remove)
        if not dlg.exec():
            return
        apply, remove = dlg.apply_processing(), dlg.selected()
        self._save_choice = (apply, remove)
        src = Path(self.source.path)
        keep_format = not apply and info.strippable
        suffix = BATCH_SUFFIX if apply else NO_META_SUFFIX
        ext = src.suffix if keep_format else '.png'
        path, _ = QFileDialog.getSaveFileName(self, '保存', str(src.with_name(f'{src.stem}{suffix}{ext}')),
                                              f'{info.format if keep_format else "PNG"} (*{ext})')
        if not path:
            return
        try:
            if apply:
                save_clean(self.result['result'], self.result['alpha'], self.source.meta, path, remove)
            elif keep_format:   # 画素には触れず、ファイルから選んだ部分だけを取り除く
                md.strip_file(src, path, remove)
            else:               # 部分的に消せない形式は、PNG として保存し直す
                save_png(self.source.rgb, self.source.alpha, self.source.meta, path, strip_meta=True)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f'保存できませんでした。\n{path}\n\n{e}')
            return
        self._say(f'保存しました：{Path(path).name}')

    def open_batch(self, folder=None):
        BatchDialog(self, self.panel.params(), folder).exec()

    def open_meta(self):
        MetaDialog(self, self.source.metadata).exec()

    # ---- ドラッグ＆ドロップ ----

    @staticmethod
    def _dropped_path(e):
        urls = e.mimeData().urls()
        if not urls or not urls[0].isLocalFile():
            return None
        path = Path(urls[0].toLocalFile())
        return path if path.is_dir() or path.suffix.lower() in IMAGE_SUFFIXES else None

    def dragEnterEvent(self, e):
        if self._dropped_path(e) is not None:
            e.acceptProposedAction()

    def dropEvent(self, e):
        path = self._dropped_path(e)
        if path is None:
            return
        if path.is_dir():
            self.open_batch(path)
        else:
            self.open_image(path)

    def closeEvent(self, e):
        self.full.stop()
        self.preview.stop()
        super().closeEvent(e)
