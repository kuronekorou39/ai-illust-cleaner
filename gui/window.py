"""メインウィンドウ"""
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QMainWindow,
                               QMessageBox, QStackedWidget, QVBoxLayout, QWidget)

from denoise import load_image, save_png
from . import detector, icons, theme
from .dialogs import BATCH_SUFFIX, BatchDialog, MetaDialog
from .imaging import mask_to_qimage, to_qimage
from .inspect import InspectPanel
from .panel import Panel
from .viewer import Viewer
from .widgets import RailButton, button, icon_button, label
from .winchrome import apply_dark_titlebar
from .worker import (FullProcessor, LatestWorker, PreviewProcessor, Source, analyze_source,
                     clamp_preview_rect)

APP_NAME = 'ai-illust-denoise'
NO_META_SUFFIX = '_nometa'   # メタデータだけ消して保存するときの既定のファイル名の末尾
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp')
OPEN_FILTER = '画像 (*.png *.jpg *.jpeg *.webp)'
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


class ModelDownload(QThread):
    """判定モデルのダウンロード"""
    progress = Signal(int, int)
    finished_ok = Signal(bool)   # 中止された場合は False
    failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.cancelled = False

    def run(self):
        try:
            self.finished_ok.emit(detector.download(self.progress.emit, lambda: self.cancelled))
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1080, 720)
        self.setAcceptDrops(True)

        self.source = None
        self.result = None          # 最後に表示した全体処理の結果
        self.stage = 'inspect'      # 'inspect'（状態の確認）→ 'adjust'（ノイズ除去の調整）
        self._awaiting = False      # 現在の設定での全体処理の結果を待っているか
        self._analyzing = 0         # 実行中の指標計算の数
        self._download = None       # 実行中の判定モデルのダウンロード
        self._smoothed = (None, None)   # (条件, 平滑化結果)。プレビューで使い回す

        self.viewer = Viewer()
        self.inspect = InspectPanel()
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
        self.inspect.downloadRequested.connect(self.toggle_download)
        self.inspect.metaRequested.connect(self.open_meta)
        self.inspect.stripSaveRequested.connect(self.save_without_meta)
        self.inspect.proceedRequested.connect(lambda: self.set_stage('adjust'))
        self.viewer.zoomChanged.connect(lambda z: self.zoom_label.setText(f'{z * 100:.0f}%'))
        self.viewer.cursorMoved.connect(self._on_cursor)
        self.viewer.openRequested.connect(self.choose_image)
        self._sync_enabled()

    # ---- 画面の組み立て ----

    def _build(self):
        rail = QFrame()
        rail.setObjectName('rail')
        rail.setFixedWidth(theme.RAIL_WIDTH)
        self.open_btn = RailButton('open', '開く')
        self.inspect_btn = RailButton('inspect', '確認', checkable=True)
        self.adjust_btn = RailButton('adjust', '調整', checkable=True)
        self.batch_btn = RailButton('batch', '一括')
        stages = QButtonGroup(self)
        stages.addButton(self.inspect_btn)
        stages.addButton(self.adjust_btn)
        self.inspect_btn.setChecked(True)
        self.inspect_btn.clicked.connect(lambda: self.set_stage('inspect'))
        self.adjust_btn.clicked.connect(lambda: self.set_stage('adjust'))
        self.open_btn.clicked.connect(self.choose_image)
        self.batch_btn.clicked.connect(lambda: self.open_batch())
        self.open_btn.setToolTip('画像を開く')
        self.inspect_btn.setToolTip('画像の状態を確認')
        self.adjust_btn.setToolTip('ノイズ除去を調整')
        self.batch_btn.setToolTip('フォルダを一括処理')
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(0, 12, 0, 12)
        rl.setSpacing(4)
        for b in (self.open_btn, self.inspect_btn, self.adjust_btn, self.batch_btn):
            rl.addWidget(b, 0, Qt.AlignHCenter)
        rl.addStretch(1)

        bar = QFrame()
        bar.setObjectName('bottomBar')
        bar.setFixedHeight(theme.BOTTOM_BAR_HEIGHT)
        self.zoom_out_btn = icon_button('minus', '縮小')
        self.zoom_in_btn = icon_button('plus', '拡大')
        self.fit_btn = button('全体表示')
        self.zoom_label = label('—', 'mono')
        self.zoom_label.setFixedWidth(52)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.coords = label('', 'monoMuted')
        self.coords.setFixedWidth(120)
        self.info = label('', 'monoMuted')
        self.status = label('', 'muted')
        self.keep_meta_box = QCheckBox('メタデータを引き継ぐ')
        self.keep_meta_box.setChecked(True)
        self.keep_meta_box.setCursor(Qt.PointingHandCursor)
        self.batch_wide_btn = button('フォルダを一括処理', 'secondary')
        self.save_btn = button('保存', 'primary')
        self.zoom_out_btn.clicked.connect(self.viewer.zoom_out)
        self.zoom_in_btn.clicked.connect(self.viewer.zoom_in)
        self.fit_btn.clicked.connect(self.viewer.fit)
        self.batch_wide_btn.clicked.connect(lambda: self.open_batch())
        self.save_btn.clicked.connect(self.save)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 16, 0)
        bl.setSpacing(8)
        for w in (self.zoom_out_btn, self.zoom_label, self.zoom_in_btn, self.fit_btn):
            bl.addWidget(w)
        bl.addSpacing(12)
        bl.addWidget(self.coords)
        bl.addWidget(self.info)
        bl.addStretch(1)
        bl.addWidget(self.status)
        bl.addSpacing(8)
        bl.addWidget(self.keep_meta_box)
        bl.addSpacing(8)
        bl.addWidget(self.batch_wide_btn)
        bl.addWidget(self.save_btn)

        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        center.addWidget(self.viewer, 1)
        center.addWidget(bar)

        root = QWidget()
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(rail)
        self.stack = QStackedWidget()
        self.stack.setFixedWidth(theme.PANEL_WIDTH)
        self.stack.addWidget(self.inspect)
        self.stack.addWidget(self.panel)
        lay.addLayout(center, 1)
        lay.addWidget(self.stack)
        self.setCentralWidget(root)

    def showEvent(self, e):
        apply_dark_titlebar(self)
        super().showEvent(e)

    def _sync_enabled(self):
        has = self.source is not None
        for w in (self.zoom_out_btn, self.zoom_in_btn, self.fit_btn, self.inspect_btn, self.adjust_btn,
                  self.inspect, self.panel):
            w.setEnabled(has)
        adjusting = has and self.stage == 'adjust'
        self.save_btn.setVisible(adjusting)
        self.keep_meta_box.setVisible(adjusting)
        self.save_btn.setEnabled(adjusting and self.result is not None and not self._awaiting)
        self.viewer.set_busy(self._awaiting or self._analyzing > 0)

    @property
    def keep_meta(self):
        return self.keep_meta_box.isChecked()

    def set_stage(self, stage):
        """確認と調整を切り替える。ノイズ除去は調整に入ったときに初めて実行する"""
        if self.source is None:
            return
        self.stage = stage
        (self.adjust_btn if stage == 'adjust' else self.inspect_btn).setChecked(True)
        self.stack.setCurrentWidget(self.panel if stage == 'adjust' else self.inspect)
        self.viewer.set_mode(self.viewer.compare_mode if stage == 'adjust' else 'inspect')
        self._sync_enabled()
        if stage == 'adjust':
            self._on_params(False)

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
        return Source(str(path), rgb, alpha, meta)

    def open_image(self, path):
        src = self._load(path)
        if src is None:
            return
        self.source = src
        self.result = None
        self._smoothed = (None, None)
        h, w = src.rgb.shape[:2]
        self.setWindowTitle(f'{Path(path).name} — {APP_NAME}')
        self.info.setText(f'{w} × {h}　{Path(path).suffix[1:].upper()}')
        self._awaiting = False
        self._debounce.stop()
        self.viewer.set_source(to_qimage(src.display))
        self.inspect.set_source(src)
        for kind in ('before', 'after'):
            self.panel.set_metrics(kind, None)
            self.panel.set_ai(kind, None)
        self.set_stage('inspect')
        self._run_task(analyze_source, src, self._on_analysis)

    # ---- 判定モデル ----

    def toggle_download(self):
        if self._download is not None:   # 実行中に押されたら中止
            self._download.cancelled = True
            return
        self._download = ModelDownload(self)
        self._download.progress.connect(self.inspect.set_download_progress)
        self._download.finished_ok.connect(self._on_download_done)
        self._download.failed.connect(self._on_download_failed)
        self.inspect.set_downloading(True)
        self._download.start()

    def _end_download(self):
        self._download.wait()
        self._download = None
        self.inspect.set_downloading(False)
        self.inspect.sync_detector()

    def _on_download_done(self, ok):
        self._end_download()
        if ok and self.source is not None:   # 開いている画像をすぐ判定する
            self.result = None
            self.inspect.set_analyzing()
            self._run_task(analyze_source, self.source, self._on_analysis)
            self._on_params(False)

    def _on_download_failed(self, text):
        self._end_download()
        self.inspect.set_download_error(text)

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

    def _on_analysis(self, r):
        if self.source is not None and r['source_id'] == self.source.id:
            self.viewer.set_layer('noise_orig', to_qimage(r['noise']))
            self.panel.set_metrics('before', r['metrics'])
            self.panel.set_ai('before', r['ai'])
            self.inspect.set_analysis(r['metrics'], r['ai'])

    # ---- 処理 ----

    def _on_params(self, interactive):
        if self.source is None or self.stage != 'adjust':
            return
        params = self.panel.params()
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
        self.preview.submit((self.source, params, rect, reuse))

    def _submit_full(self):
        if self.source is not None:
            self.full.submit((self.source, self.panel.params()))

    def _on_preview(self, r):
        current = self.source is not None and r['source_id'] == self.source.id
        if current and self._awaiting and r['params'] == self.panel.params():
            self.viewer.set_patch(r['rect'], to_qimage(r['display']), to_qimage(r['noise']),
                                  mask_to_qimage(r['mask']))

    def _on_full(self, r):
        if self.source is None or r['source_id'] != self.source.id:
            return
        self._smoothed = ((r['source_id'], r['params'].smooth_key), r['smoothed'])
        if r['params'] != self.panel.params():
            return   # 設定が変わった後に届いた古い結果。新しい結果を待つ
        self.result = r
        self._awaiting = False
        self.viewer.set_layer('result', to_qimage(r['display']))
        self.viewer.set_layer('noise_result', to_qimage(r['noise']))
        self.viewer.set_layer('mask', mask_to_qimage(r['mask']))
        self.viewer.clear_patch()
        self.panel.set_metrics('after', r['metrics'])
        self.panel.set_ai('after', r['ai'])
        self._sync_enabled()

    def _on_failed(self, trace):
        self._awaiting = False
        self._sync_enabled()
        QMessageBox.warning(self, APP_NAME, f'処理中にエラーが起きました。\n\n{trace}')

    def _on_cursor(self, x, y):
        self.coords.setText(f'x {x}  y {y}' if x >= 0 else '')

    # ---- 保存・一括処理・メタデータ ----

    def save(self):
        self._save_as(self.result['result'], BATCH_SUFFIX, '処理後の画像を保存', not self.keep_meta)

    def save_without_meta(self):
        """画素はそのまま、メタデータだけを除いて保存する"""
        self._save_as(self.source.rgb, NO_META_SUFFIX, 'メタデータを消して保存', True)

    def _save_as(self, rgb, suffix, title, strip_meta):
        src = Path(self.source.path)
        default = src.with_name(f'{src.stem}{suffix}.png')
        path, _ = QFileDialog.getSaveFileName(self, title, str(default), 'PNG (*.png)')
        if not path:
            return
        try:
            save_png(rgb, self.source.alpha, self.source.meta, path, strip_meta)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f'保存できませんでした。\n{path}\n\n{e}')
            return
        self._say(f'保存しました：{Path(path).name}')

    def open_batch(self, folder=None):
        BatchDialog(self, self.panel.params(), not self.keep_meta, folder).exec()

    def open_meta(self):
        MetaDialog(self, self.source.meta).exec()

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
        if self._download is not None:
            self._download.cancelled = True
            self._download.wait()
        self.full.stop()
        self.preview.stop()
        super().closeEvent(e)
