"""メタデータの表示・消去、フォルダ一括処理のダイアログ"""
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QLineEdit, QPlainTextEdit, QProgressBar, QVBoxLayout)

import metadata as md
from cutout import cutout, merge_alpha
from denoise import clean, load_image, save_png
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


class StripDialog(_Dialog):
    """消すメタデータの種類を選ぶ"""

    def __init__(self, parent, meta):
        super().__init__(parent, 'メタデータを消して保存', 480)
        groups = meta.groups()
        self.boxes = {}
        for g, entries in groups.items():
            name, desc = md.GROUPS[g]
            box = QCheckBox(f'{name}（{len(entries)} 項目）')
            box.setChecked(True)
            box.setEnabled(meta.strippable)
            box.setCursor(Qt.PointingHandCursor)
            box.toggled.connect(self._sync)
            note = label(desc, 'small')
            note.setWordWrap(True)
            note.setContentsMargins(26, 0, 0, 0)   # チェックボックスの文字の位置にそろえる
            item = QVBoxLayout()
            item.setSpacing(2)
            item.addWidget(box)
            item.addWidget(note)
            self.body.addLayout(item)
            self.boxes[g] = box
        if meta.strippable:
            text = '画素はそのままで、ファイルから選んだ部分だけを取り除きます。形式も変わりません。'
            if meta.has_color:
                text += '\n色の情報（ICCプロファイルなど）は、見た目が変わるため残します。'
        else:
            text = (f'{meta.format or "この"}形式は部分的に消せないため、PNG として保存し直します。'
                    'メタデータはすべて消えます（色の情報は残します）。')
        info = label(text, 'muted')
        info.setWordWrap(True)
        self.ok = button('保存先を選ぶ', 'primary')
        self.ok.clicked.connect(self.accept)
        cancel = button('キャンセル', 'secondary')
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.ok)
        self.body.addWidget(info)
        self.body.addLayout(foot)

    def selected(self):
        return {g for g, box in self.boxes.items() if box.isChecked()}

    def _sync(self):
        self.ok.setEnabled(bool(self.selected()))


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
                save_png(out, alpha, meta, self._out_dir / f'{src.stem}{BATCH_SUFFIX}.png', self._strip_meta)
                ok += 1
            except Exception as e:
                errors.append(f'{src.name}: {e}')
            self.progress.emit(i + 1, src.name)
        self.finished_all.emit(ok, errors)


class BatchDialog(_Dialog):
    """フォルダ内のPNGを、現在の設定でまとめて処理する"""

    def __init__(self, parent, params, strip_meta, folder=None):
        super().__init__(parent, 'フォルダを一括処理', 560)
        self._params, self._strip_meta = params, strip_meta
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
        meta = 'メタデータは引き継がない' if strip_meta else 'メタデータを引き継ぐ'
        summary = label(f'{mode}{strength}　暗部 {params.dark}　勾配 {params.grad}{cutout}　{meta}', 'muted')
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
        return sorted(src.glob(BATCH_PATTERN)) if self.src.text() and src.is_dir() else []

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
        self._thread = BatchThread(files, Path(self.dst.text()), self._params, self._strip_meta, self)
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
