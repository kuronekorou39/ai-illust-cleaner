"""メタデータの表示・消去、フォルダ一括処理のダイアログ"""
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog, QGridLayout,
                               QHBoxLayout, QLineEdit, QPlainTextEdit, QProgressBar, QRadioButton,
                               QVBoxLayout)

from cleaner import metadata as md
from cleaner.cutout import cutout, merge_alpha
from cleaner.denoise import clean, load_image, save_png
from .errors import summary as error_summary
from .widgets import Segmented, button, label
from .winchrome import apply_dark_titlebar

BATCH_PATTERN = '*.png'
BATCH_SUFFIX = '_clean'
BATCH_DEFAULT_SUBDIR = 'clean'


class _Dialog(QDialog):
    def __init__(self, parent, title, width):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 20, 24, 20)
        self.body.setSpacing(14)

    def showEvent(self, e):
        apply_dark_titlebar(self)
        super().showEvent(e)


def _gen_text(gen):
    parts = [f'ツール：{gen.tool}']
    if gen.prompt:
        parts.append(f'■ プロンプト\n{gen.prompt}')
    if gen.negative:
        parts.append(f'■ ネガティブプロンプト\n{gen.negative}')
    if gen.settings:
        parts.append('■ 設定\n' + '\n'.join(f'{k}：{v}' for k, v in gen.settings.items()))
    return '\n\n'.join(parts)


def _clip(text):
    return text if len(text) <= md.PREVIEW_CHARS else text[:md.PREVIEW_CHARS] + f'…（全 {len(text):,} 文字）'


def _all_text(meta):
    parts = []
    for g, entries in meta.groups().items():
        lines = []
        for e in entries:
            fields = md.xmp_fields(e.value) if g == 'xmp' else []
            if fields:
                lines += [f'{k}：{v}' for k, v in fields]
            else:
                lines.append(f'{e.key}：{_clip(e.value)}')
        parts.append(f'■ {md.GROUPS[g][0]}\n' + '\n'.join(lines))
    return '\n\n'.join(parts)


class MetaDialog(_Dialog):
    """メタデータの内容を、生成情報（プロンプトと設定に分けたもの）とすべての項目で表示する"""

    def __init__(self, parent, meta):
        super().__init__(parent, 'メタデータ', 640)
        self._meta = meta
        tabs = [('all', 'すべての項目')]
        if meta.gen is not None:
            tabs.insert(0, ('gen', '生成情報'))
        self.tabs = Segmented(tabs)
        self.tabs.setFixedWidth(120 * len(tabs))
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMinimumHeight(360)
        self.tabs.changed.connect(self._show)
        self.copy = button('プロンプトをコピー', 'secondary')
        self.copy.clicked.connect(self._copy_prompt)
        self.copy.setVisible(meta.gen is not None and bool(meta.gen.prompt))
        close = button('閉じる', 'secondary')
        close.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addWidget(self.copy)
        foot.addStretch(1)
        foot.addWidget(close)
        self.body.addWidget(self.tabs)
        self.body.addWidget(self.view)
        self.body.addLayout(foot)
        self._show(tabs[0][0])

    def _show(self, tab):
        if tab == 'gen':
            self.view.setPlainText(_gen_text(self._meta.gen))
        else:
            self.view.setPlainText(_all_text(self._meta) or 'この画像にはメタデータがありません。')

    def _copy_prompt(self):
        QApplication.clipboard().setText(self._meta.gen.prompt)
        self.copy.setText('コピーしました')


class SaveDialog(_Dialog):
    """保存の設定：処理を反映するか、どの種類のメタデータを消すか"""

    def __init__(self, parent, meta, processed_ready, apply=True, keep=()):
        super().__init__(parent, '保存', 500)
        self._meta = meta

        self.apply = QRadioButton('ノイズ除去と背景の透過を反映する（PNG で保存）')
        self.original = QRadioButton(f'元の画像のまま（{meta.format}形式のまま、画素も変えない）' if meta.strippable
                                     else '元の画像のまま（PNG で保存し直す）')
        group = QButtonGroup(self)
        for b in (self.apply, self.original):
            b.setCursor(Qt.PointingHandCursor)
            group.addButton(b)
        self.apply.setEnabled(processed_ready)
        (self.apply if apply and processed_ready else self.original).setChecked(True)
        self.body.addWidget(label('画像', 'section'))
        self.body.addWidget(self.apply)
        self.body.addWidget(self.original)

        self.body.addSpacing(6)
        self.body.addWidget(label('削除するメタデータ', 'section'))
        self.boxes = {}
        groups = meta.groups()
        for g, entries in groups.items():
            name, desc = md.GROUPS[g]
            box = QCheckBox(f'{name}（{len(entries)} 項目）')
            box.setChecked(g not in keep)   # 残すと選んだことのない種類は、消す側で始める
            box.setCursor(Qt.PointingHandCursor)
            note = label(desc, 'small')
            note.setWordWrap(True)
            note.setContentsMargins(26, 0, 0, 0)   # チェックボックスの文字の位置にそろえる
            item = QVBoxLayout()
            item.setSpacing(2)
            item.addWidget(box)
            item.addWidget(note)
            self.body.addLayout(item)
            self.boxes[g] = box
        if not groups:
            self.body.addWidget(label('この画像にはメタデータがありません。', 'muted'))
        self.note = label('', 'small')
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)

        ok = button('保存先を選ぶ', 'primary')
        ok.clicked.connect(self.accept)
        cancel = button('キャンセル', 'secondary')
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        self.body.addLayout(foot)
        for b in (self.apply, self.original):   # チェックボックスを作ってからつなぐ
            b.toggled.connect(self._sync)
        self._sync()

    def apply_processing(self):
        return self.apply.isChecked()

    def selected(self):
        return {g for g, box in self.boxes.items() if box.isChecked()}

    def _sync(self, _=None):
        partial = self.apply_processing() or self._meta.strippable
        for box in self.boxes.values():   # 部分的に消せない形式を元のまま保存するときは、すべて消える
            box.setEnabled(partial)
            if not partial:
                box.setChecked(True)
        if partial:
            text = '色の情報（ICCプロファイルなど）は、見た目が変わるため残します。'
        else:
            text = (f'{self._meta.format}形式は部分的に消せないため、PNG として保存し直し、'
                    'メタデータはすべて削除します（色の情報は残します）。')
        if self.apply_processing() and 'c2pa' in self._meta.groups():
            text += '\nC2PA は署名が処理後の画像と合わなくなるため、反映するときは引き継ぎません。'
        self.note.setText(text)


def batch_output(out_dir, src):
    """一括処理の保存先。同じ名前のファイルがあれば、上書きせず番号を付ける"""
    path = out_dir / f'{src.stem}{BATCH_SUFFIX}.png'
    n = 2
    while path.exists():
        path = out_dir / f'{src.stem}{BATCH_SUFFIX} ({n}).png'
        n += 1
    return path


def batch_sources(folder):
    """一括処理の対象。以前の処理結果（元の画像が同じフォルダにある *_clean.png）は除く"""
    def is_result(p):
        return p.stem.endswith(BATCH_SUFFIX) and p.with_name(p.stem[:-len(BATCH_SUFFIX)] + p.suffix).exists()
    return [p for p in sorted(folder.glob(BATCH_PATTERN)) if not is_result(p)]


class BatchThread(QThread):
    progress = Signal(int, str)       # 完了した枚数、処理したファイル名
    finished_all = Signal(int, list)  # 成功した枚数、失敗の一覧

    def __init__(self, files, out_dir, params, strip_meta, parent=None):
        super().__init__(parent)
        self._files, self._out_dir = files, out_dir
        self._params, self._strip_meta = params, strip_meta
        self.cancelled = False

    def run(self):
        ok, errors = 0, []
        p = self._params
        for i, src in enumerate(self._files):
            if self.cancelled:
                break
            try:
                meta, rgb, alpha = load_image(src)
                out = clean(rgb, p.mode, p.lam, p.dark, p.grad)
                if p.cutout:
                    out, cut, _ = cutout(out, p.tolerance, p.holes)
                    alpha = merge_alpha(cut, alpha)
                save_png(out, alpha, meta, batch_output(self._out_dir, src), self._strip_meta)
                ok += 1
            except Exception as e:
                errors.append(f'{src.name}: {error_summary(e)}')
            self.progress.emit(i + 1, src.name)
        self.finished_all.emit(ok, errors)


class BatchDialog(_Dialog):
    """フォルダ内のPNGを、現在の設定でまとめて処理する"""

    def __init__(self, parent, params, folder=None):
        super().__init__(parent, 'フォルダを一括処理', 560)
        self._params = params
        self._thread = None

        self.src = QLineEdit()
        self.dst = QLineEdit()
        self.src.setPlaceholderText('処理したいPNGが入ったフォルダ')
        self.dst.setPlaceholderText('保存先のフォルダ')
        self.src.textChanged.connect(self._on_src)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        for row, (text, edit) in enumerate((('入力フォルダ', self.src), ('出力フォルダ', self.dst))):
            name = label(text)
            name.setBuddy(edit)
            pick = button('選ぶ')
            pick.clicked.connect(lambda _=False, e=edit: self._pick(e))
            grid.addWidget(name, row, 0)
            grid.addWidget(edit, row, 1)
            grid.addWidget(pick, row, 2)

        mode = '面を平坦化' if params.mode == 'l0' else '揺らぎだけ除去'
        strength = f'　強さ {params.lam:.4f}' if params.mode == 'l0' else ''
        tolerance = '自動' if params.tolerance is None else str(params.tolerance)
        cutout = f'　背景を透明化（許容差 {tolerance}）' if params.cutout else ''
        summary = label(f'{mode}{strength}　線の暗さ {params.dark}　線の輪郭 {params.grad}{cutout}', 'muted')
        summary.setWordWrap(True)

        self.status = label('', 'muted')
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setValue(0)
        self.run = button('処理を始める', 'primary')
        self.run.clicked.connect(self._start)
        self.close_btn = button('閉じる', 'secondary')
        self.close_btn.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(self.close_btn)
        foot.addWidget(self.run)

        self.body.addLayout(grid)
        self.body.addWidget(summary)
        self.strip_meta = QCheckBox('メタデータをすべて削除する（色の情報は残す）')
        self.strip_meta.setChecked(True)
        self.strip_meta.setCursor(Qt.PointingHandCursor)
        self.body.addWidget(self.strip_meta)
        self.body.addWidget(self.bar)
        self.body.addWidget(self.status)
        self.body.addLayout(foot)
        if folder:
            self.src.setText(str(folder))
        self._on_src()

    def _pick(self, edit):
        path = QFileDialog.getExistingDirectory(self, 'フォルダを選ぶ', edit.text())
        if path:
            edit.setText(path)

    def _files(self):
        src = Path(self.src.text())
        return batch_sources(src) if self.src.text() and src.is_dir() else []

    def _on_src(self):
        files = self._files()
        if files and not self.dst.text():
            self.dst.setText(str(Path(self.src.text()) / BATCH_DEFAULT_SUBDIR))
        self.run.setEnabled(bool(files))
        if self.src.text():
            self.status.setText(f'PNG {len(files)} 枚が対象です。' if files else 'このフォルダにPNGが見つかりません。')

    def _start(self):
        if self._thread is not None:   # 実行中に押されたら中止
            self._thread.cancelled = True
            self.run.setEnabled(False)
            return
        files = self._files()
        if not self.dst.text():
            self.status.setText('出力フォルダを指定してください。')
            return
        self.bar.setRange(0, len(files))
        self.bar.setValue(0)
        self.run.setText('中止')
        self.close_btn.setEnabled(False)
        self._thread = BatchThread(files, Path(self.dst.text()), self._params, self.strip_meta.isChecked(), self)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished_all.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, done, name):
        self.bar.setValue(done)
        self.status.setText(f'{done} / {self.bar.maximum()}　{name}')

    def _on_finished(self, ok, errors):
        self._thread.wait()
        self._thread = None
        self.run.setText('処理を始める')
        self.run.setEnabled(True)
        self.close_btn.setEnabled(True)
        text = f'{ok} 枚を保存しました：{self.dst.text()}'
        if errors:
            text += f'\n失敗 {len(errors)} 枚：' + '、'.join(errors[:3]) + ('…' if len(errors) > 3 else '')
        self.status.setText(text)

    def reject(self):
        if self._thread is None:
            super().reject()
