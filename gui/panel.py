"""右側の調整パネル"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from denoise import DEFAULT_DARK, DEFAULT_GRAD, DEFAULT_LAM
from . import score, theme
from .widgets import Legend, MetricBars, Segmented, SliderRow, StatTile, button, help_button, label
from .worker import Params

LAM_STEP = 0.0002            # 強さスライダーの1目盛り
LAM_SLIDER_MAX = 50          # 目盛りの数（最大 0.01）
LAM_PRESETS = (('弱', 0.001), ('標準', 0.002), ('強', 0.005))
DARK_MAX = 255
GRAD_MAX = 400


def _lam_to_tick(lam):
    return round(lam / LAM_STEP)


def _section(*widgets, spacing=12):
    box = QFrame()
    box.setObjectName('section')
    lay = QVBoxLayout(box)
    lay.setContentsMargins(20, 16, 20, 16)
    lay.setSpacing(spacing)
    for w in widgets:
        if isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return box


class Panel(QFrame):
    changed = Signal(bool)          # 引数は「操作の途中か」（スライダーをつかんでいる間は True）
    maskToggled = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName('panel')
        self.setFixedWidth(theme.PANEL_WIDTH)

        # 強さ
        self.lam = SliderRow('強さ', 1, LAM_SLIDER_MAX, _lam_to_tick(DEFAULT_LAM),
                             fmt=lambda v: f'{v * LAM_STEP:.4f}', big_value=True)
        self.lam.changed.connect(self._on_lam)
        self.lam.released.connect(lambda: self.changed.emit(False))
        presets = QGridLayout()
        presets.setSpacing(6)
        self._presets = QButtonGroup(self)
        self._presets.setExclusive(False)
        for i, (name, lam) in enumerate(LAM_PRESETS):
            b = button(f'{name} {lam:g}', 'preset', checkable=True)
            b.clicked.connect(lambda _=False, v=lam: self._apply_preset(v))
            self._presets.addButton(b, _lam_to_tick(lam))
            presets.addWidget(b, 0, i)
        self._sync_presets()

        # 方式
        self.mode = Segmented([('l0', '面を平坦化'), ('soft', '揺らぎだけ除去')])
        self.mode.changed.connect(self._on_mode)
        mode_box = QVBoxLayout()
        mode_box.setSpacing(10)
        mode_box.addWidget(label('方式', 'section'))
        mode_box.addWidget(self.mode)

        # 線の保護
        self.dark = SliderRow('暗部しきい値', 0, DARK_MAX, DEFAULT_DARK)
        self.grad = SliderRow('勾配しきい値', 0, GRAD_MAX, DEFAULT_GRAD)
        for row in (self.dark, self.grad):
            row.changed.connect(lambda _, r=row: self.changed.emit(r.slider.isSliderDown()))
            row.released.connect(lambda: self.changed.emit(False))
        self.show_mask = QCheckBox('線マスクを重ねて表示')
        self.show_mask.setCursor(Qt.PointingHandCursor)
        self.show_mask.toggled.connect(self.maskToggled)

        # 結果
        head = QHBoxLayout()
        head.addWidget(label('結果', 'section'))
        head.addStretch(1)
        head.addWidget(Legend([(theme.BAR_BEFORE, '処理前'), (theme.ACCENT, '処理後')]))
        self.ai_score = label('—', 'tileValue')
        score_row = QHBoxLayout()
        score_row.setSpacing(6)
        score_row.addWidget(label('AI判定度'))
        score_row.addWidget(help_button(score.HELP, 'AI判定度の説明'))
        score_row.addStretch(1)
        score_row.addWidget(self.ai_score)
        self.noise_mean = MetricBars('塗り面ノイズ平均', lambda v: f'{v:.3f}')
        self.noise_over = MetricBars('1階調を超える画素', lambda v: f'{v:.2f}%')
        self.ssim = StatTile('SSIM')
        self.psnr = StatTile('PSNR')
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        tiles.addWidget(self.ssim)
        tiles.addWidget(self.psnr)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_section(self.lam, presets))
        lay.addWidget(_section(mode_box))
        lay.addWidget(_section(label('線の保護', 'section'), self.dark, self.grad, self.show_mask))
        results = _section(head, score_row, self.noise_mean, self.noise_over, tiles, spacing=14)
        results.setObjectName('')   # 最後の区切り線は不要
        lay.addWidget(results)
        lay.addStretch(1)

        self._metrics = {'before': None, 'after': None}
        self._ai = {'before': None, 'after': None}

    # ---- 値 ----

    def params(self):
        return Params(self.mode.value(), round(self.lam.value() * LAM_STEP, 6),
                      self.dark.value(), self.grad.value())

    def _on_lam(self, _):
        self._sync_presets()
        self.changed.emit(self.lam.slider.isSliderDown())

    def _apply_preset(self, lam):
        self.lam.set_value(_lam_to_tick(lam))
        self._sync_presets()

    def _sync_presets(self):
        for b in self._presets.buttons():
            b.setChecked(self._presets.id(b) == self.lam.value())

    def _on_mode(self, mode):
        self.lam.setEnabled(mode == 'l0')   # 強さは「面を平坦化」にだけ効く
        for b in self._presets.buttons():
            b.setEnabled(mode == 'l0')
        self.changed.emit(False)

    # ---- 結果の表示 ----

    def set_metrics(self, kind, metrics):
        """kind は 'before' / 'after'。metrics が None なら未計測に戻す"""
        self._metrics[kind] = metrics
        get = lambda k, name: None if self._metrics[k] is None else self._metrics[k][name]
        self.noise_mean.set_values(get('before', 'mean'), get('after', 'mean'))
        self.noise_over.set_values(get('before', 'over'), get('after', 'over'))
        after = self._metrics['after']
        self.ssim.set_value('—' if after is None else f"{after['ssim']:.3f}")
        if after is None:
            self.psnr.set_value('—')
        else:
            self.psnr.set_value('変化なし' if after['psnr'] is None else f"{after['psnr']:.1f} dB")

    def set_ai(self, kind, probs):
        """kind は 'before' / 'after'。probs が None なら未計測（判定モデルが無い場合を含む）"""
        self._ai[kind] = probs
        show = lambda p: '—' if p is None else score.percent(score.combine(p))
        self.ai_score.setText(f"{show(self._ai['before'])} → {show(self._ai['after'])}")
