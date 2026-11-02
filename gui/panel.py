"""右側の調整パネル。上は保存する画像に反映する「処理」、下は見え方だけを変える「表示」"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

import noise
from cutout import AUTO_MAX
from denoise import DEFAULT_DARK, DEFAULT_GRAD, DEFAULT_LAM
from .widgets import ChoiceGrid, Segmented, SliderRow, button, help_button, label
from .worker import Params

LAM_STEP = 0.0002            # 強さスライダーの1目盛り
LAM_SLIDER_MAX = 50          # 目盛りの数（最大 0.01）
LAM_PRESETS = (('弱', 0.001), ('標準', 0.002), ('強', 0.005))
DARK_MAX = 255
GRAD_MAX = 400
TOLERANCE_MAX = 64
LINE_HELP = ('ノイズ除去で線がぼやけないよう、線と判断した部分は処理せず元のまま残します。\n'
             '次のどちらかに当てはまる画素を線とみなします。\n\n'
             '・線とみなす暗さ：これより暗い画素（大きくすると、濃い塗りも線として残る）\n'
             '・線とみなす輪郭の強さ：色の変わり方がこれより急な画素（小さくすると、細かな境目も残る）\n\n'
             '「保護する線を色で表示」で、線とみなした範囲を確かめられます。')
NOISE_HELP = ('ノイズの見え方を切り替えます。ノイズ除去の結果や数値には影響しません。\n'
              '数値は比べられるよう、常にメディアン差で計算しています。\n\n'
              '抽出：画像から何を引いてノイズとみなすか\n'
              + '\n'.join(f'・{name}：{desc.splitlines()[0]}' for name, desc in noise.ALGOS.values())
              + '\n\n表示：取り出したノイズをどう見せるか\n'
              + '\n'.join(f'・{name}：{desc.splitlines()[0]}' for name, desc in noise.STYLES.values()))
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


def _titled(text, help_text):
    """見出しと「？」ボタンを並べる"""
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(label(text, 'section'))
    row.addWidget(help_button(help_text, f'{text}の説明'))
    row.addStretch(1)
    return row


def _group(title, note):
    """「処理」「表示」のまとまりの見出し"""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(20, 20, 20, 0)
    lay.setSpacing(10)
    lay.addWidget(label(title, 'groupTitle'), 0, Qt.AlignBottom)
    sub = label(note, 'small')
    sub.setWordWrap(True)   # パネルの幅を押し広げないようにする
    lay.addWidget(sub, 1, Qt.AlignBottom)
    return box


class Panel(QFrame):
    changed = Signal(bool)          # 引数は「操作の途中か」（スライダーをつかんでいる間は True）
    maskToggled = Signal(bool)
    noiseChanged = Signal(str, str)  # ノイズの (抽出方式, 表示方式)
    compareAlgos = Signal(bool)      # 抽出方式を4通り並べて比べる表示のオン・オフ

    def __init__(self):
        super().__init__()
        self.setObjectName('panel')

        # ノイズ除去（方式 → 強さ。強さは「面を平坦化」にだけ効く）
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

        self.mode = Segmented([('l0', '面を平坦化'), ('soft', '揺らぎだけ除去')])
        self.mode.changed.connect(self._on_mode)
        self.lam_box = QWidget()
        lam_lay = QVBoxLayout(self.lam_box)
        lam_lay.setContentsMargins(0, 6, 0, 0)
        lam_lay.setSpacing(12)
        lam_lay.addWidget(self.lam)
        lam_lay.addLayout(presets)

        # 線の保護
        self.dark = SliderRow('線とみなす暗さ', 0, DARK_MAX, DEFAULT_DARK)
        self.grad = SliderRow('線とみなす輪郭の強さ', 0, GRAD_MAX, DEFAULT_GRAD)
        self.dark.setToolTip('これより暗い画素を線として残します。大きくすると、濃い塗りも線として残ります。')
        self.grad.setToolTip('色の変わり方がこれより急な画素を線として残します。小さくすると、細かな境目も残ります。')
        for row in (self.dark, self.grad):
            row.changed.connect(lambda _, r=row: self.changed.emit(r.slider.isSliderDown()))
            row.released.connect(lambda: self.changed.emit(False))
        self.show_mask = QCheckBox('保護する線を色で表示')
        self.show_mask.setToolTip('線とみなして処理しない範囲を、画像の上に色で重ねます。')
        self.show_mask.setCursor(Qt.PointingHandCursor)
        self.show_mask.toggled.connect(self.maskToggled)

        # ノイズの見え方
        noise_head = QHBoxLayout()
        noise_head.setSpacing(6)
        noise_head.addWidget(label('ノイズの見え方', 'section'))
        noise_head.addWidget(help_button(NOISE_HELP, 'ノイズの見え方の説明'))
        noise_head.addStretch(1)
        self.algo = ChoiceGrid([(k, name, desc) for k, (name, desc) in noise.ALGOS.items()], 2)
        self.compare = button('4通りを並べて選ぶ', checkable=True)
        self.compare.setToolTip('画面を4分割し、それぞれの抽出方式で取り出したノイズを並べます。\n'
                                '画面をクリックすると、その方式に決まります。')
        self.compare.toggled.connect(self.compareAlgos)
        self.noise_style = ChoiceGrid([(k, name, desc) for k, (name, desc) in noise.STYLES.items()], 3)
        for grid in (self.algo, self.noise_style):
            grid.changed.connect(lambda _: self.noiseChanged.emit(*self.noise_key()))

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
        # 透過の設定は、透過をオンにしたときだけ出す
        self.cutout_opts = QWidget()
        opts = QVBoxLayout(self.cutout_opts)
        opts.setContentsMargins(0, 4, 0, 0)
        opts.setSpacing(10)
        opts.addLayout(tolerance_row)
        opts.addWidget(self.holes)
        opts.addLayout(pick_row)
        self._sync_cutout(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_group('処理', '保存する画像に反映'))
        lay.addWidget(_section(label('ノイズ除去', 'section'), self.mode, self.lam_box))
        lay.addWidget(_section(_titled('線の保護', LINE_HELP), self.dark, self.grad))
        lay.addWidget(_section(cutout_head, self.cutout, self.cutout_opts, spacing=10))
        lay.addWidget(_group('表示', '見え方だけ。保存する画像は変わらない'))
        lay.addWidget(_section(self.show_mask))
        noise_box = _section(noise_head, label('抽出', 'small'), self.algo, self.compare,
                             label('表示', 'small'), self.noise_style, spacing=8)
        noise_box.setObjectName('')   # 最後の区切り線は不要
        lay.addWidget(noise_box)
        lay.addStretch(1)

    # ---- 値 ----

    def params(self):
        return Params(self.mode.value(), round(self.lam.value() * LAM_STEP, 6),
                      self.dark.value(), self.grad.value(),
                      self.cutout.isChecked(),
                      None if self.auto_tolerance.isChecked() else self.tolerance.value(),
                      self.holes.isChecked())

    def noise_key(self):
        return self.algo.value(), self.noise_style.value()

    def set_algo(self, key):
        """4通りの比較で選ばれた抽出方式にする"""
        if key != self.algo.value():
            self.algo.set_value(key)
            self.noiseChanged.emit(*self.noise_key())

    def _sync_cutout(self, _=None):
        on = self.cutout.isChecked()
        self.cutout_opts.setVisible(on)
        if not on:
            self.pick.setChecked(False)
        self.tolerance.setEnabled(not self.auto_tolerance.isChecked())

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
        self.lam_box.setVisible(mode == 'l0')   # 強さは「面を平坦化」にだけ効く
        self.changed.emit(False)
