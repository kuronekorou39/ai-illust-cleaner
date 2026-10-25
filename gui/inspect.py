"""読み込んだ画像の状態を確認するパネル（処理の前段階）"""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from . import detector, score, theme
from .widgets import Gauge, StatTile, button, help_button, label

META_PREVIEW_CHARS = 140
NOISE_HELP = ('線を除いた塗りの部分にある、細かな色のばらつきです。\n'
              '「ノイズ平均」はばらつきの平均の大きさ、\n'
              '「1階調を超える画素」は目立つばらつきがある画素の割合で、\n'
              'どちらも小さいほどきれいです。')
META_HELP = ('生成ツールが画像に書き込む、プロンプトなどの情報です。\n'
             '残っていると、画像の内容に関係なく生成画像だとわかります。')

_NOISE_TEXT = {
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
    downloadRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('panel')
        self.setFixedWidth(theme.PANEL_WIDTH)

        # 画像
        self.name = label('', 'fileName')
        self.info = label('', 'monoMuted')

        # AI判定度：判定モデルがあるときの表示
        self.score = label('—', 'score')
        self.gauge = Gauge()
        self.model_rows = [ValueRow(m.title) for m in detector.MODELS]
        self.result_box = QWidget()
        rl = QVBoxLayout(self.result_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        rl.addWidget(self.score)
        rl.addWidget(self.gauge)
        for row in self.model_rows:
            rl.addWidget(row)

        # AI判定度：判定モデルが無いときの表示
        self.setup_text = label(f'判定モデル（約 {detector.TOTAL_MB}MB）をダウンロードすると使えます。'
                                '判定はこのPCの中で行い、画像を外部に送りません。', 'muted')
        self.setup_text.setWordWrap(True)
        self.download_btn = button('判定モデルをダウンロード')
        self.download_btn.clicked.connect(self.downloadRequested)
        self.download_bar = QProgressBar()
        self.download_bar.setTextVisible(False)
        self.download_bar.hide()
        self.setup_box = QWidget()
        sl = QVBoxLayout(self.setup_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        sl.addWidget(self.setup_text)
        sl.addWidget(self.download_bar)
        sl.addWidget(self.download_btn)

        # メタデータ
        self.meta_state = ValueRow('生成パラメータ')
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
        lay.addWidget(_section(_titled('AI判定度', score.HELP), self.result_box, self.setup_box, spacing=6))
        lay.addWidget(_section(_titled('メタデータ', META_HELP), self.meta_state, self.meta_text,
                               meta_row, self.strip, spacing=8))
        lay.addWidget(_section(_titled('塗り面ノイズ', NOISE_HELP), tiles, self.advice, last=True))
        lay.addStretch(1)
        foot = QVBoxLayout()
        foot.setContentsMargins(20, 0, 20, 20)
        foot.addWidget(self.proceed)
        lay.addLayout(foot)
        self.sync_detector()

    # ---- 判定モデルの準備 ----

    def sync_detector(self):
        installed = detector.is_installed()
        self.result_box.setVisible(installed)
        self.setup_box.setVisible(not installed)

    def set_downloading(self, active):
        self.download_bar.setVisible(active)
        self.download_bar.setValue(0)
        self.download_btn.setText('中止' if active else '判定モデルをダウンロード')

    def set_download_progress(self, done, total):
        self.download_bar.setRange(0, max(1, total >> 10))
        self.download_bar.setValue(done >> 10)

    def set_download_error(self, text):
        self.setup_text.setText(f'ダウンロードできませんでした。\n{text}')

    # ---- 表示の更新 ----

    def set_source(self, src):
        path = Path(src.path)
        h, w = src.rgb.shape[:2]
        width = theme.PANEL_WIDTH - 40
        self.name.setText(QFontMetrics(self.name.font()).elidedText(path.name, Qt.ElideMiddle, width))
        self.name.setToolTip(str(path))
        alpha = '透過あり' if src.alpha is not None else '透過なし'
        self.info.setText(f'{w} × {h}　{path.suffix[1:].upper()}　{alpha}')

        found = score.ai_meta(src.meta)
        texts = {k: v for k, v in src.meta.items() if isinstance(v, str)}
        self.meta_state.set_state('残っている' if found else '見つからない', bool(found))
        if texts:
            key, value = next(iter((found or texts).items()))
            snippet = ' '.join(value.split())
            more = '…' if len(snippet) > META_PREVIEW_CHARS else ''
            self.meta_text.setText(f'{key}: {snippet[:META_PREVIEW_CHARS]}{more}')
        self.meta_text.setVisible(bool(texts))
        self.meta_view.setVisible(bool(texts))
        self.strip.setVisible(bool(texts))
        self.set_analyzing()

    def set_analyzing(self):
        """計測が終わるまでの表示"""
        self.score.setText('—')
        self.gauge.set_value(None)
        for row in self.model_rows:
            row.set_state('計測中')
        self.noise_mean.set_value('—')
        self.noise_over.set_value('—')
        self.advice.setText('')

    def set_analysis(self, metrics, probs, uniform_bg):
        self.info.setText(self.info.text() + ('　背景 単色' if uniform_bg else '　背景 複雑'))
        self.noise_mean.set_value(f"{metrics['mean']:.3f}")
        self.noise_over.set_value(f"{metrics['over']:.2f}%")
        self.advice.setText(_NOISE_TEXT[score.noise_level(metrics['mean'])])
        if probs is None:
            return
        p = score.combine(probs)
        self.score.setText(score.percent(p))
        self.gauge.set_value(p * 100)
        for row, prob in zip(self.model_rows, probs):
            row.set_state(score.percent(prob), prob >= score.LIKELY_AI)
