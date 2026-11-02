"""読み込んだ画像の状態を確認するパネル（処理の前段階）"""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

import metadata as md
from . import score, theme
from .widgets import StatTile, button, help_button, label

META_PREVIEW_CHARS = 140
META_SUMMARY_KEYS = ('シード値', 'モデル')   # 確認画面に出す生成設定
NOISE_HELP = ('線を除いた塗りの部分にある、細かな色のばらつきです。\n'
              '「ノイズ平均」はばらつきの平均の大きさ、\n'
              '「1階調を超える画素」は目立つばらつきがある画素の割合で、\n'
              'どちらも小さいほどきれいです。')
META_HELP = ('画像ファイルに埋め込まれた、画素以外の情報です。\n\n'
             + '\n'.join(f'・{name}：{desc}' for name, desc in md.GROUPS.values())
             + '\n\n公開前に、意図しない情報が残っていないか確かめてください。')

_NOISE_TEXT = {
    'extreme': 'ノイズが大きすぎます。このツールが対象とする塗り面ノイズの範囲を超えているため、'
               '除去しても残ります。一般的なノイズ除去（NLM など）のほうが向いています。',
    'high': 'ノイズが多めです。除去の効果が見込めます。',
    'mid': 'ノイズがやや見られます。',
    'low': 'ノイズは少なく、処理は不要そうです。',
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


def _titled(text, help_text):
    """見出しと「？」ボタンを並べる"""
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(label(text, 'section'))
    row.addWidget(help_button(help_text, f'{text}の説明'))
    row.addStretch(1)
    return row


class InspectPanel(QFrame):
    proceedRequested = Signal()
    stripSaveRequested = Signal()
    metaRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('panel')
        self.setFixedWidth(theme.PANEL_WIDTH)

        # 画像
        self.name = label('', 'fileName')
        self.info = label('', 'monoMuted')

        # メタデータ
        self.meta_rows = {g: ValueRow(name) for g, (name, _) in md.GROUPS.items()}
        self.notes = label('', 'small')
        self.notes.setWordWrap(True)
        self.meta_text = label('', 'metaBox')
        self.meta_text.setWordWrap(True)
        self.meta_text.setTextFormat(Qt.PlainText)
        self.meta_view = button('内容をすべて見る', 'link')
        self.meta_view.clicked.connect(self.metaRequested)
        self.strip = button('メタデータを消して保存')
        self.strip.clicked.connect(self.stripSaveRequested)
        meta_row = QHBoxLayout()
        meta_row.addWidget(self.meta_view)
        meta_row.addStretch(1)

        # 塗り面ノイズ
        self.noise_mean = StatTile('ノイズ平均')
        self.noise_over = StatTile('1階調を超える画素')
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        tiles.addWidget(self.noise_mean)
        tiles.addWidget(self.noise_over)
        self.advice = label('', 'muted')
        self.advice.setWordWrap(True)

        self.proceed = button('ノイズ除去に進む', 'primary')
        self.proceed.clicked.connect(self.proceedRequested)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_section(self.name, self.info, spacing=4))
        lay.addWidget(_section(_titled('メタデータ', META_HELP), *self.meta_rows.values(), self.notes,
                               self.meta_text, meta_row, self.strip, spacing=4))
        lay.addWidget(_section(_titled('塗り面ノイズ', NOISE_HELP), tiles, self.advice, last=True))
        lay.addStretch(1)
        foot = QVBoxLayout()
        foot.setContentsMargins(20, 0, 20, 20)
        foot.addWidget(self.proceed)
        lay.addLayout(foot)

    # ---- 表示の更新 ----

    def set_source(self, src):
        path = Path(src.path)
        h, w = src.rgb.shape[:2]
        width = theme.PANEL_WIDTH - 40
        self.name.setText(QFontMetrics(self.name.font()).elidedText(path.name, Qt.ElideMiddle, width))
        self.name.setToolTip(str(path))
        alpha = '透過あり' if src.alpha is not None else '透過なし'
        self.info.setText(f'{w} × {h}　{path.suffix[1:].upper()}　{alpha}')

        self._show_meta(src.metadata)
        self.set_analyzing()

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
        self.meta_view.setVisible(bool(groups))
        self.strip.setVisible(bool(groups))

    def set_analyzing(self):
        """計測が終わるまでの表示"""
        self.noise_mean.set_value('—')
        self.noise_over.set_value('—')
        self.advice.setText('')

    def set_analysis(self, metrics, uniform_bg):
        self.info.setText(self.info.text() + ('　背景 単色' if uniform_bg else '　背景 複雑'))
        self.noise_mean.set_value(f"{metrics['mean']:.3f}")
        self.noise_over.set_value(f"{metrics['over']:.2f}%")
        self.advice.setText(_NOISE_TEXT[score.noise_level(metrics['mean'])])
