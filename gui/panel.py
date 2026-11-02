"""右側の調整パネル"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from cutout import AUTO_MAX
from denoise import DEFAULT_DARK, DEFAULT_GRAD, DEFAULT_LAM
from . import theme
from .widgets import Legend, MetricBars, Segmented, SliderRow, StatTile, button, help_button, label
from .worker import Params

LAM_STEP = 0.0002            # 強さスライダーの1目盛り
LAM_SLIDER_MAX = 50          # 目盛りの数（最大 0.01）
LAM_PRESETS = (('弱', 0.001), ('標準', 0.002), ('強', 0.005))
DARK_MAX = 255
GRAD_MAX = 400
TOLERANCE_MAX = 64
CUTOUT_HELP = ('画像の縁の色を背景色とみなし、縁とつながっている同じ色の部分を透明にします。\n'
               '腕と体の間など、囲まれた背景は「クリックで透明を切り替え」を押してから\n'
               'その場所をクリックすると透明になります。もう一度クリックすると戻ります。\n'
               '背景が単色のイラスト向けで、風景などの複雑な背景は対象外です。\n\n'
               '許容差は「背景色とみなす色の差」です。自動では背景のばらつきのすぐ上に置き、\n'
               '白に近い淡い塗りを背景と間違えないようにします。\n'
               '背景の残りが気になるときは上げ、淡い塗りが消えるときは下げてください。')


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

        # 背景の透過
        cutout_head = QHBoxLayout()
        cutout_head.setSpacing(6)
        cutout_head.addWidget(label('背景の透過', 'section'))
        cutout_head.addWidget(help_button(CUTOUT_HELP, '背景の透過の説明'))
        cutout_head.addStretch(1)
        self.cutout = QCheckBox('単色の背景を透明にする')
        self.tolerance = SliderRow('許容差', 0, TOLERANCE_MAX, AUTO_MAX)
        self.auto_tolerance = QCheckBox('自動')
        self.holes = QCheckBox('囲まれた背景も自動で透明にする')
        self.holes.setToolTip('腕と体の間などの、背景色と同じ色で大きい領域を透明にします。\n'
                              '白い胸など、背景色と同じ色の大きな塗りも消えることがあります。\n'
                              '確実なのは、下の「クリックで透明を切り替え」で場所を指定する方法です。')
        self.pick = button('クリックで透明を切り替え', checkable=True)
        self.pick.setToolTip('押した後、画像上の場所をクリックすると、その部分の透明・不透明が入れ替わります')
        self.pick_clear = button('切り替えを取り消す', 'link')
        self.pick_clear.hide()
        pick_row = QVBoxLayout()
        pick_row.setSpacing(4)
        pick_row.addWidget(self.pick)
        pick_row.addWidget(self.pick_clear, 0, Qt.AlignRight)
        for box in (self.cutout, self.holes, self.auto_tolerance):
            box.setCursor(Qt.PointingHandCursor)
            box.toggled.connect(lambda _: self.changed.emit(False))
        self.auto_tolerance.setChecked(True)
        self.cutout.toggled.connect(self._sync_cutout)
        self.auto_tolerance.toggled.connect(self._sync_cutout)
        tolerance_row = QHBoxLayout()
        tolerance_row.addWidget(self.tolerance, 1)
        tolerance_row.addSpacing(12)
        tolerance_row.addWidget(self.auto_tolerance, 0, Qt.AlignBottom)
        self.tolerance.changed.connect(lambda _: self.changed.emit(self.tolerance.slider.isSliderDown()))
        self.tolerance.released.connect(lambda: self.changed.emit(False))
        self.bg_note = label('', 'small')
        self.bg_note.setWordWrap(True)
        self._sync_cutout(False)

        # 結果
        head = QHBoxLayout()
        head.addWidget(label('結果', 'section'))
        head.addStretch(1)
        head.addWidget(Legend([(theme.BAR_BEFORE, '処理前'), (theme.ACCENT, '処理後')]))
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
        lay.addWidget(_section(cutout_head, self.cutout, tolerance_row, self.holes, pick_row, self.bg_note,
                               spacing=10))
        results = _section(head, self.noise_mean, self.noise_over, tiles, spacing=14)
        results.setObjectName('')   # 最後の区切り線は不要
        lay.addWidget(results)
        lay.addStretch(1)

        self._metrics = {'before': None, 'after': None}

    # ---- 値 ----

    def params(self):
        return Params(self.mode.value(), round(self.lam.value() * LAM_STEP, 6),
                      self.dark.value(), self.grad.value(),
                      self.cutout.isChecked(),
                      None if self.auto_tolerance.isChecked() else self.tolerance.value(),
                      self.holes.isChecked())

    def _sync_cutout(self, _=None):
        on = self.cutout.isChecked()
        self.auto_tolerance.setEnabled(on)
        self.holes.setEnabled(on)
        self.pick.setEnabled(on)
        if not on:
            self.pick.setChecked(False)
        self.tolerance.setEnabled(on and not self.auto_tolerance.isChecked())

    def set_toggle_count(self, n):
        self.pick_clear.setVisible(n > 0)
        self.pick_clear.setText(f'切り替えを取り消す（{n}）')

    def show_tolerance(self, value):
        """自動で決まった許容差をスライダーに反映する（手動に切り替えたとき、そこから調整できる）"""
        if value is not None and self.auto_tolerance.isChecked():
            self.tolerance.slider.blockSignals(True)
            self.tolerance.set_value(value)
            self.tolerance.value_label.setText(f'自動 {value}')
            self.tolerance.slider.blockSignals(False)

    def set_background(self, uniform):
        """背景が単色かどうかで、透過の案内を変える"""
        self.bg_note.setText('' if uniform else '縁の色がそろっていないため、きれいに抜けない可能性があります。')

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
