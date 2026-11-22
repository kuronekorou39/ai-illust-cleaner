# 開発

[README に戻る](../README.md)

[uv](https://docs.astral.sh/uv/) を使う。

```powershell
uv sync
uv run python -m gui              # 起動
uv run python -m gui 画像.png     # 画像を開いて起動
uv run pytest                     # テスト
uv run python tools/build_exe.py  # exe を作り、dist/ に zip をまとめる
```

## フォルダ

| フォルダ | 中身 |
|---|---|
| `cleaner/` | 処理の本体（ノイズ除去・ノイズの抽出・背景の透過・メタデータ） |
| `gui/` | 画面 |
| `tests/` | テスト |
| `tools/` | 開発用のスクリプト（下記） |
| `packaging/` | exe の作成の設定・入口・アイコン |
| `docs/` | ドキュメントと画像 |

## コマンドライン

```powershell
uv run python -m cleaner.denoise 入力.png 出力.png              # 標準
uv run python -m cleaner.denoise 入力.png 出力.png --lam 0.005  # 強め（既定 0.002、弱めは 0.001）
uv run python -m cleaner.denoise 入力.png 出力.png --mode soft  # 控えめ（小さな揺らぎだけ除去）
uv run python -m cleaner.denoise 入力.png 出力.png --cutout     # 単色の背景を透明にする
uv run python -m cleaner.denoise 入力フォルダ 出力フォルダ       # 一括処理
```

メタデータ（PNGテキスト・EXIF・XMP）は既定で引き継ぐ。不要なら `--strip-meta`（色の情報は残る）。

## 開発用のスクリプト（tools/）

| スクリプト | 用途 |
|---|---|
| `inspect_meta.py` | メタデータを種類ごとに表示する |
| `visualize_noise.py` | ノイズの強度マップ・色ノイズ・周波数を1枚にまとめる |
| `compare.py` | 処理前後のノイズと SSIM・PSNR を並べる |
| `benchmark.py` | 他のノイズ除去手法と比べる（[docs/comparison.md](comparison.md) の数値と図） |
| `gui_shot.py` | 画面のキャプチャを撮る（README の画像） |
| `make_icon.py` | アプリのアイコンを `packaging/icon.ico` に書き出す |
| `make_banner.py` | `docs/banner.png` を作る |
| `build_exe.py` | exe を作って zip にまとめる |

exe は `ai-illust-cleaner.exe --smoke-test 入力画像 出力.png` で、画面を出さずに画像を開いて処理し、画面の画像を書き出して終わる（配布前の動作確認用）。

## リリース

1. 版を `pyproject.toml` と `gui/__init__.py` の両方で上げる（テストでそろっているかを確かめる）
2. `v0.1.0` のようなタグを push する

GitHub Actions がテストを走らせ、exe を作って動作を確かめ、Releases に zip を載せる。
