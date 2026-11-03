# ライセンス

[README に戻る](../README.md)

ai-illust-cleaner は [MIT License](../LICENSE)（© rou39）で公開している。

配布している exe には、次のライブラリとフォントを同梱している。

| 名前 | 用途 | ライセンス |
|---|---|---|
| [Python](https://www.python.org/) | 実行環境 | PSF License |
| [PySide6](https://doc.qt.io/qtforpython-6/) / [Qt 6](https://www.qt.io/) | 画面 | LGPL-3.0 |
| [OpenCV](https://opencv.org/)（opencv-contrib-python） | 画像処理 | Apache-2.0 |
| [NumPy](https://numpy.org/) | 数値計算 | BSD-3-Clause |
| [SciPy](https://scipy.org/) | 画像処理（フィルタ） | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | 画像の読み書き | MIT-CMU |
| [Zen Kaku Gothic New](https://fonts.google.com/specimen/Zen+Kaku+Gothic+New) | 画面の文字 | SIL Open Font License 1.1 |
| [DM Mono](https://fonts.google.com/specimen/DM+Mono) | 画面の数値 | SIL Open Font License 1.1 |

- Qt は LGPL-3.0 で、exe には DLL のまま同梱している（`_internal/PySide6/` の DLL は差し替えられる）
- opencv-contrib-python に含まれる FFmpeg（動画の読み込み用）は使わないため、exe には同梱していない
- フォントのライセンス文は `gui/assets/fonts/` にある
- exe は [PyInstaller](https://pyinstaller.org/) で作っている。PyInstaller のブートローダーは、作ったプログラムの配布を認める例外付きの GPL-2.0
