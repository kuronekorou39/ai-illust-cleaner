"""Windowsのタイトルバーをアプリの配色に合わせる"""
import sys

from PySide6.QtGui import QColor

from . import theme

# DwmSetWindowAttribute の属性番号
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def _colorref(hex_color):
    c = QColor(hex_color)
    return c.red() | (c.green() << 8) | (c.blue() << 16)


def apply_dark_titlebar(widget):
    """対応していないOSでは何もしない（標準のタイトルバーのまま）"""
    if sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    hwnd = wintypes.HWND(int(widget.winId()))
    for attr, value in ((_DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
                        (_DWMWA_CAPTION_COLOR, _colorref(theme.SURFACE)),
                        (_DWMWA_TEXT_COLOR, _colorref(theme.TEXT)),
                        (_DWMWA_BORDER_COLOR, _colorref(theme.BORDER))):
        v = ctypes.c_int(value)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
