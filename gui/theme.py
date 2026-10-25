"""配色・フォント・スタイルシート"""
from pathlib import Path

from PySide6.QtGui import QFontDatabase

ASSETS = Path(__file__).resolve().parent / 'assets'

BG = '#121110'
SURFACE = '#171614'
PANEL = '#1b1a18'
CANVAS = '#0c0b0a'
BORDER = '#2a2825'
BORDER_STRONG = '#3a3733'
TEXT = '#ece8e1'
MUTED = '#a39d92'
DISABLED = '#6b665e'
ACCENT = '#e8a33d'
ON_ACCENT = '#1a1408'
BAR_BEFORE = '#8f887c'
CHIP_BG = (18, 17, 16, 220)

FONT_UI = 'Zen Kaku Gothic New'
FONT_MONO = 'DM Mono'
FONT_UI_FALLBACK = 'Yu Gothic UI'

RAIL_WIDTH = 64
PANEL_WIDTH = 340
BOTTOM_BAR_HEIGHT = 56


def load_fonts():
    """同梱フォントを登録する（無ければOSのフォントにフォールバック）"""
    for ttf in sorted((ASSETS / 'fonts').glob('*.ttf')):
        QFontDatabase.addApplicationFont(str(ttf))


_QSS = """
* { font-family: '@FONT_UI', '@FONT_FALLBACK'; font-size: 13px; color: @TEXT; outline: none; }
QMainWindow, QDialog { background: @BG; }
QToolTip { background: @SURFACE; color: @TEXT; border: 1px solid @BORDER_STRONG; padding: 4px 8px; }

#rail { background: @SURFACE; border-right: 1px solid @BORDER; }
#bottomBar { background: @SURFACE; border-top: 1px solid @BORDER; }
#panel { background: @PANEL; border-left: 1px solid @BORDER; }
#section { border-bottom: 1px solid @BORDER; }
#panelScroll { background: @PANEL; border: none; }

QLabel { background: transparent; }
QLabel[role="section"] { font-size: 12px; font-weight: 700; color: @MUTED; letter-spacing: 1px; }
QLabel[role="muted"] { color: @MUTED; }
QLabel[role="small"] { color: @MUTED; font-size: 11px; }
QLabel[role="mono"] { font-family: '@FONT_MONO'; }
QLabel[role="monoMuted"] { font-family: '@FONT_MONO'; font-size: 12px; color: @MUTED; }
QLabel[role="bigValue"] { font-family: '@FONT_MONO'; font-size: 22px; }
QLabel[role="tileValue"] { font-family: '@FONT_MONO'; font-size: 20px; }
QLabel[role="score"] { font-family: '@FONT_MONO'; font-size: 40px; color: @ACCENT; }
QLabel[role="fileName"] { font-size: 15px; font-weight: 700; }
QLabel[role="metaBox"] { font-family: '@FONT_MONO', '@FONT_UI'; font-size: 11px; color: @MUTED;
    background: @BG; border-radius: 6px; padding: 10px 12px; }
QLabel:disabled { color: @DISABLED; }

QToolButton[kind="rail"] { border: none; border-radius: 8px; background: transparent;
    color: @MUTED; font-size: 11px; padding-top: 6px; }
QToolButton[kind="rail"]:hover { background: #211f1d; color: @TEXT; }
QToolButton[kind="rail"]:checked { background: @BORDER; color: @TEXT; }

QPushButton { border: 1px solid @BORDER_STRONG; border-radius: 6px; background: transparent;
    padding: 0 12px; min-height: 32px; }
QPushButton:hover { border-color: @MUTED; }
QPushButton:pressed { background: @BORDER; }
QPushButton:disabled { color: @DISABLED; border-color: @BORDER; }
QPushButton:checked { border-color: @ACCENT; color: @ACCENT; }
QPushButton[kind="primary"] { border: none; border-radius: 8px; background: @ACCENT; color: @ON_ACCENT;
    font-weight: 700; min-height: 40px; padding: 0 28px; }
QPushButton[kind="primary"]:hover { background: #f2b458; }
QPushButton[kind="primary"]:pressed { background: #d2902f; }
QPushButton[kind="primary"]:disabled { background: @BORDER_STRONG; color: @DISABLED; }
QPushButton[kind="secondary"] { border-radius: 8px; min-height: 40px; padding: 0 18px; font-weight: 500; }
QPushButton[kind="icon"] { padding: 0; min-width: 32px; max-width: 32px; }
QPushButton[kind="preset"] { font-size: 12px; color: @MUTED; min-height: 34px; padding: 0 4px; }
QPushButton[kind="preset"]:checked { color: @ACCENT; border-color: @ACCENT; }
QPushButton[kind="help"] { border: 1px solid @MUTED; border-radius: 9px; color: @MUTED; font-size: 11px;
    font-weight: 700; min-height: 16px; padding: 0; }
QPushButton[kind="help"]:hover { border-color: @ACCENT; color: @ACCENT; }
QPushButton[kind="link"] { border: none; color: @MUTED; font-size: 12px; min-height: 24px; padding: 0 4px; }
QPushButton[kind="link"]:hover { color: @ACCENT; }

#segmented { background: @BG; border-radius: 8px; }
#segmented[floating="true"] { border: 1px solid @BORDER; }
QPushButton[kind="seg"] { border: none; border-radius: 6px; font-size: 12px; color: @MUTED;
    min-height: 32px; padding: 0 10px; }
QPushButton[kind="seg"]:hover { color: @TEXT; }
QPushButton[kind="seg"]:checked { background: @BORDER_STRONG; color: @TEXT; font-weight: 500; }

QSlider::groove:horizontal { height: 4px; background: @BORDER_STRONG; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @ACCENT; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: @TEXT; }
QSlider::handle:horizontal:hover { background: @ACCENT; }
QSlider::sub-page:horizontal:disabled { background: @DISABLED; }
QSlider::handle:horizontal:disabled { background: @DISABLED; }
QSlider { min-height: 20px; }

QCheckBox { color: @MUTED; spacing: 8px; }
QCheckBox:hover { color: @TEXT; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid @BORDER_STRONG; border-radius: 4px; background: @BG; }
QCheckBox::indicator:checked { background: @ACCENT; border-color: @ACCENT; image: url(@CHECK); }

#tile { background: @BG; border-radius: 8px; }

QLineEdit, QPlainTextEdit { background: @BG; border: 1px solid @BORDER_STRONG; border-radius: 6px;
    padding: 6px 8px; selection-background-color: @ACCENT; selection-color: @ON_ACCENT; }
QLineEdit { min-height: 20px; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: @ACCENT; }
QPlainTextEdit { font-family: '@FONT_MONO', '@FONT_UI'; font-size: 12px; }

QProgressBar { background: @BORDER_STRONG; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }
QProgressBar::chunk { background: @ACCENT; border-radius: 2px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @BORDER_STRONG; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


def stylesheet():
    values = {
        '@FONT_FALLBACK': FONT_UI_FALLBACK, '@FONT_UI': FONT_UI, '@FONT_MONO': FONT_MONO,
        '@BORDER_STRONG': BORDER_STRONG, '@BORDER': BORDER, '@BG': BG, '@SURFACE': SURFACE,
        '@PANEL': PANEL, '@TEXT': TEXT, '@MUTED': MUTED, '@DISABLED': DISABLED,
        '@ON_ACCENT': ON_ACCENT, '@ACCENT': ACCENT,
        '@CHECK': (ASSETS / 'check.svg').as_posix(),
    }
    qss = _QSS
    for key, value in values.items():   # 長い名前から順に並べてあるので部分一致で壊れない
        qss = qss.replace(key, value)
    return qss
