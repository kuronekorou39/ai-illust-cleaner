"""GUIの起動

使い方:
  uv run python -m gui                 # 起動
  uv run python -m gui 画像.png        # 画像を開いて起動
"""
import sys

from PySide6.QtWidgets import QApplication

from . import theme
from .window import APP_NAME, MainWindow


def create_app():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    theme.load_fonts()
    app.setStyleSheet(theme.stylesheet())
    return app


def main():
    app = create_app()
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.open_image(sys.argv[1])
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
