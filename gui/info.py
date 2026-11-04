"""左側のパネル（ファイル・メタデータ・結果の数値）"""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from cleaner import metadata as md
from . import icons, score, theme
from .widgets import ClickableLabel, Legend, MetricBars, StatTile, button, help_button, label, WarnNote

META_PREVIEW_CHARS = 140
META_SUMMARY_KEYS = ('シード値', 'モデル')   # 左パネルに出す生成設定
NOISE_HELP = ('線以外の塗りにある細かな色のばらつきで、小さいほどきれいです。\n'
              'SSIM・PSNR は処理前との近さで、大きいほど元の絵を保っています。')
META_HELP = '画像ファイルに埋め込まれた、画素以外の情報です。'
MOSTLY_LINES = 0.5   # 線とみなした割合がこれを超えたら、ほとんど処理できていない
NOISE_LEFT_TITLE = '処理後もノイズが多く残っています'
NOISE_LEFT_TEXT = {
    'lines': '画像の多くを線とみなして、処理していません。「線とみなす暗さ」を下げるか、「線とみなす輪郭の強さ」を上げてください。',
    'strong': 'このツールが対象とする塗りのノイズ（±1階調程度）より強いノイズです。強さを上げてください。',
}


class ValueRow(QWidget):
    """名前と値の1行。強調する行は塗りつぶした丸、それ以外は輪郭だけの丸で示す"""
    HEIGHT = 28

    def __init__(self, title):
        super().__init__()
        self._title = title
        self._value = ''
        self._strong = False
        self.setFixedHeight(self.HEIGHT)
        self.setAccessibleName(title)

    def set_state(self, value, strong=False):
        self._value, self._strong = value, strong
        self.setAccessibleDescription(value)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._strong:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ACCENT))
        else:
            p.setPen(QPen(QColor(theme.MUTED), 1.4))
            p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(1, self.HEIGHT / 2 - 5, 10, 10))
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(22, 0, self.width() - 22, self.HEIGHT), Qt.AlignLeft | Qt.AlignVCenter, self._title)
        p.setPen(QColor(theme.ACCENT if self._strong else theme.MUTED))
        p.drawText(QRectF(0, 0, self.width(), self.HEIGHT), Qt.AlignRight | Qt.AlignVCenter, self._value)
        p.end()


def _section(*items, spacing=10, last=False):
    box = QFrame()
    if not last:
        box.setObjectName('section')
    lay = QVBoxLayout(box)
    lay.setContentsMargins(20, 16, 20, 16)
    lay.setSpacing(spacing)
    for item in items:
        if isinstance(item, QWidget):
            lay.addWidget(item)
        else:
            lay.addLayout(item)
    return box


def _titled(text, help_text, right=None):
    """見出しと「？」ボタンを並べる"""
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(label(text, 'section'))
    row.addWidget(help_button(help_text, f'{text}の説明'))
    row.addStretch(1)
    if right is not None:
        row.addWidget(right)
    return row


class InfoPanel(QFrame):
    openRequested = Signal()
    batchRequested = Signal()
    metaRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('panelBody')

        # ファイル
        self.open_btn = button('開く')
        self.batch_btn = button('フォルダを一括処理')
        for b, icon_name in ((self.open_btn, 'open'), (self.batch_btn, 'batch')):
            b.setIcon(icons.icon(icon_name, 16))
        self.open_btn.clicked.connect(self.openRequested)
        self.batch_btn.clicked.connect(self.batchRequested)
        files = QHBoxLayout()
        files.setSpacing(8)
        files.addWidget(self.open_btn, 1)
        files.addWidget(self.batch_btn, 2)
        self.name = label('', 'fileName')
        self.info = label('', 'monoMuted')
        self.info.setWordWrap(True)

        # メタデータ
        self.meta_rows = {g: ValueRow(name) for g, (name, _) in md.GROUPS.items()}
        self.notes = label('', 'small')
        self.notes.setWordWrap(True)
        self.meta_text = ClickableLabel('metaBox', 'クリックで内容をすべて表示')
        self.meta_text.setWordWrap(True)
        self.meta_text.setTextFormat(Qt.PlainText)
        self.meta_text.clicked.connect(self.metaRequested)

        # 結果（塗り面ノイズの処理前後と、元の絵の保たれ具合）
        self.noise_mean = MetricBars('塗り面ノイズ平均', lambda v: f'{v:.3f}')
        self.noise_over = MetricBars('1階調を超える画素', lambda v: f'{v:.2f}%')
        self.noise_warn = WarnNote(NOISE_LEFT_TITLE, '')
        self.noise_warn.hide()
        self.ssim = StatTile('SSIM')
        self.psnr = StatTile('PSNR')
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        tiles.addWidget(self.ssim)
        tiles.addWidget(self.psnr)
        legend = Legend([(theme.BAR_BEFORE, '処理前'), (theme.ACCENT, '処理後')])

        self.meta_box = _section(_titled('メタデータ', META_HELP), *self.meta_rows.values(), self.notes,
                                 self.meta_text, spacing=4)
        self.result_box = _section(_titled('塗り面ノイズ', NOISE_HELP, legend), self.noise_mean,
                                   self.noise_over, tiles, self.noise_warn, spacing=12, last=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_section(files, self.name, self.info, spacing=6))
        lay.addWidget(self.meta_box)
        lay.addWidget(self.result_box)
        lay.addStretch(1)

        self._metrics = {'before': None, 'after': None}
        self._sync_loaded(False)

    def _sync_loaded(self, loaded):
        for w in (self.name, self.info, self.meta_box, self.result_box):
            w.setVisible(loaded)

    # ---- 表示の更新 ----

    def set_source(self, src):
        path = Path(src.path)
        h, w = src.rgb.shape[:2]
        width = theme.LEFT_PANEL_WIDTH - 40
        self.name.setText(QFontMetrics(self.name.font()).elidedText(path.name, Qt.ElideMiddle, width))
        self.name.setToolTip(str(path))
        alpha = '透過あり' if src.alpha is not None else '透過なし'
        self.info.setText(f'{w} × {h}　{path.suffix[1:].upper()}　{alpha}')
        self._show_meta(src.metadata)
        for kind in ('before', 'after'):
            self.set_metrics(kind, None)
        self.noise_warn.reset()
        self.noise_warn.hide()
        self._sync_loaded(True)

    def _show_meta(self, meta):
        groups = meta.groups()
        for g, row in self.meta_rows.items():
            entries = groups.get(g, [])
            if g == 'gen' and meta.gen is not None:
                value = meta.gen.tool
            elif g == 'c2pa' and entries:
                value = 'あり'
            else:
                value = f'{len(entries)} 項目' if entries else 'なし'
            row.set_state(value, bool(entries))
        self.notes.setText('\n'.join(f'※ {n}' for n in meta.notes))
        self.notes.setVisible(bool(meta.notes))

        summary = ''
        if meta.gen is not None:
            prompt = ' '.join(meta.gen.prompt.split())
            more = '…' if len(prompt) > META_PREVIEW_CHARS else ''
            settings = '　'.join(f'{k} {meta.gen.settings[k]}' for k in META_SUMMARY_KEYS if k in meta.gen.settings)
            summary = '\n'.join(t for t in (prompt[:META_PREVIEW_CHARS] + more, settings) if t)
        elif meta.entries:
            e = meta.entries[0]
            value = ' '.join(e.value.split())
            summary = f'{e.key}: {value[:META_PREVIEW_CHARS]}' + ('…' if len(value) > META_PREVIEW_CHARS else '')
        self.meta_text.setText(summary)
        self.meta_text.setVisible(bool(summary))

    def set_analysis(self, metrics, uniform_bg):
        """元画像の計測結果"""
        self.info.setText(self.info.text() + ('　背景 単色' if uniform_bg else '　背景 複雑'))
        self.set_metrics('before', metrics)

    def set_metrics(self, kind, metrics):
        """kind は 'before' / 'after'。metrics が None なら未計測に戻す"""
        self._metrics[kind] = metrics
        get = lambda k, name: None if self._metrics[k] is None else self._metrics[k][name]
        self.noise_mean.set_values(get('before', 'mean'), get('after', 'mean'))
        self.noise_over.set_values(get('before', 'over'), get('after', 'over'))
        after = self._metrics['after']
        self._sync_warn(after)
        self.ssim.set_value('—' if after is None else f"{after['ssim']:.3f}")
        if after is None:
            self.psnr.set_value('—')
        else:
            self.psnr.set_value('変化なし' if after['psnr'] is None else f"{after['psnr']:.1f} dB")

    def _sync_warn(self, after):
        """処理しても対象外の強さのノイズが残るときだけ、原因に合わせた注意を出す"""
        left = after is not None and score.noise_level(after['mean']) == 'extreme'
        if left:
            cause = 'lines' if after['line_ratio'] > MOSTLY_LINES else 'strong'
            self.noise_warn.set_content(NOISE_LEFT_TITLE, NOISE_LEFT_TEXT[cause])
        self.noise_warn.setVisible(left)
