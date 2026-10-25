# ai-illust-denoise

Stable Diffusion などで生成したイラストの「塗り面ノイズ」を、線を保ったまま除去するツール。
あわせて、ノイズの可視化・処理前後の比較・PNGメタデータの確認ができる。

## セットアップ

[uv](https://docs.astral.sh/uv/) を使う。未導入なら:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```powershell
cd C:\projects\ai-illust-denoise
uv sync
```
`uv sync` で `.python-version`（3.12）のPythonと `uv.lock` どおりの依存が `.venv` に入る。
以降のコマンドは `uv run` で実行すれば、venvの有効化は不要。

## 使い方

### GUI
```powershell
uv run python -m gui                          # 起動
uv run python -m gui input\sd_img2img.png   # 画像を開いて起動
```
- 画像はドラッグ＆ドロップでも開ける（フォルダを落とすと一括処理）
- 開いた直後は「確認」画面。メタデータの有無と塗り面ノイズの量を見て、処理するかを決める。
  ノイズ除去は「調整」に進んだときに初めて実行する
- 「AI判定度」は、公開されているイラスト向け判定モデル2つ（`gui/detector.py`）が出す確率の平均。
  判定モデル（約178MB）は初回に確認画面からダウンロードし、判定はPCの中で行う。
  画像の内容だけを見ており、メタデータやノイズの量は含まない。断定はできず、他の判定サービスとは一致しない。
  手元の生成画像23枚では見逃しが多かった（50%以上と判定したのは4枚と1枚）
- 確認画面の「メタデータを消して保存」は、画素を変えずにメタデータだけを除く
- 調整画面の「背景の透過」は、縁の色を背景色とみなし、縁とつながっている同色の部分を透明にする（`cutout.py`）。
  背景が単色のイラスト向けで、風景などの複雑な背景は対象外。コマンドラインでは `--cutout`。
  輪郭線が1〜2画素途切れていても、その切れ目から塗りの中（白い紙や羽など）へは透明が広がらない。
  許容差（背景色とみなす色の差）は既定で自動。白に近い淡い塗りを背景と間違えないよう、背景のばらつきのすぐ上に置く。
  腕と体の間などの囲まれた背景は「クリックで透明を切り替え」で場所を指定する。
  自動判定（背景色と同じ色で大きい領域を穴とみなす）もあるが、白い胸などが消えることがあるため既定ではオフ
- 調整画面の表示は「スライド比較」と「4分割（元・処理後・それぞれのノイズ）」を切り替え
- ホイールで拡大縮小、ドラッグで移動、ダブルクリックで全体表示
- スライダーを動かしている間は表示範囲だけを先に処理し、手を離すと全体を処理して指標を更新する。
  L0 は画像全体を見て最適化するため、先行表示は全体処理の結果と1〜2階調ほど異なる（仮の表示）

見た目の確認用に、各状態のスクリーンショットを `output/gui/` に保存できる:
```powershell
uv run tools\gui_shot.py
```

### ノイズ除去（コマンドライン）
```powershell
uv run denoise.py input\sd_img2img.png output\clean.png            # 標準
uv run denoise.py input\sd_img2img.png output\clean.png --lam 0.001 # 弱め
uv run denoise.py input\sd_img2img.png output\clean.png --lam 0.005 # 強め
uv run denoise.py input\sd_img2img.png output\clean.png --mode soft # 控えめ
uv run denoise.py 入力フォルダ output                                  # 一括処理
```
生成パラメータなどのPNGメタデータは既定で引き継ぐ。不要なら `--strip-meta`。

### ノイズの可視化
```powershell
uv run tools\visualize_noise.py input\sd_img2img.png --crop 300,140,110
```

### 処理前後の比較
```powershell
uv run tools\compare.py input\sd_img2img.png output\clean.png --ref input\handdrawn.png
```

### メタデータ確認
```powershell
uv run tools\inspect_meta.py input\*.png
```

## 仕組み

1. 暗い画素と強い勾配から線を検出し、線は元画像のまま保護する
2. 線以外に L0 スムージングをかけ、VAE由来の細かな色ムラを平坦化する
3. 境目を細いフェザーでなじませて合成する

ガイデッドフィルタも試したが、ノイズを含む画像自身がガイドになるためノイズが残り、不採用。

## 検証結果（input/sd_img2img.png）

| | 塗り面ノイズ平均 | 1階調超の画素 | SSIM（元画像比） |
|---|---|---|---|
| SD元画像 | 0.149 | 3.62% | — |
| 標準（lam 0.002） | 0.010 | 0.15% | 0.998 |
| soft | 0.044 | 0.45% | 0.999 |
| 手描き（参考） | 0.006 | 0.14% | — |

## 注意

- L0 はやわらかいグラデーションや頬染めも平坦にしやすい。厚塗り系は `--lam` を下げる
- ノイズ以外の違い（線の揺らぎ・入り抜き・太さの均一さ）はこのツールでは変わらない
- JPEG経由の画像はブロックノイズが混ざるため、指標・処理とも想定外

## 構成

```
ai-illust-denoise/
├─ denoise.py            ノイズ除去本体（CLI）
├─ common.py             共通処理（読み込み・ノイズ指標）
├─ cutout.py             単色の背景を透明にする
├─ gui/                  GUI（PySide6）
│  ├─ window.py          メインウィンドウ
│  ├─ viewer.py          比較ビュー
│  ├─ inspect.py         確認パネル（AI判定度・メタデータ・ノイズ量）
│  ├─ detector.py        AI判定度（判定モデルの取得と実行）
│  ├─ score.py           判定度・ノイズ量の区分と書式
│  ├─ panel.py           調整パネル
│  ├─ worker.py          裏で走る処理
│  ├─ theme.py           配色・スタイル
│  └─ assets/fonts/      同梱フォント（SIL Open Font License）
├─ tools/
│  ├─ visualize_noise.py ノイズ可視化
│  ├─ compare.py         処理前後の比較図
│  ├─ inspect_meta.py    PNGメタデータ確認
│  └─ gui_shot.py        GUIのスクリーンショット撮影
├─ input/               検証用画像（git管理外）
├─ output/               出力先（git管理外）
├─ pyproject.toml        依存定義
├─ uv.lock               依存のロックファイル
└─ .python-version      Pythonバージョン（3.12）
```
