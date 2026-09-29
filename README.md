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

### ノイズ除去
```powershell
uv run denoise.py samples\sd_img2img.png output\clean.png            # 標準
uv run denoise.py samples\sd_img2img.png output\clean.png --lam 0.001 # 弱め
uv run denoise.py samples\sd_img2img.png output\clean.png --lam 0.005 # 強め
uv run denoise.py samples\sd_img2img.png output\clean.png --mode soft # 控えめ
uv run denoise.py 入力フォルダ output                                  # 一括処理
```
生成パラメータなどのPNGメタデータは既定で引き継ぐ。不要なら `--strip-meta`。

### ノイズの可視化
```powershell
uv run tools\visualize_noise.py samples\sd_img2img.png --crop 300,140,110
```

### 処理前後の比較
```powershell
uv run tools\compare.py samples\sd_img2img.png output\clean.png --ref samples\handdrawn.png
```

### メタデータ確認
```powershell
uv run tools\inspect_meta.py samples\*.png
```

## 仕組み

1. 暗い画素と強い勾配から線を検出し、線は元画像のまま保護する
2. 線以外に L0 スムージングをかけ、VAE由来の細かな色ムラを平坦化する
3. 境目を細いフェザーでなじませて合成する

ガイデッドフィルタも試したが、ノイズを含む画像自身がガイドになるためノイズが残り、不採用。

## 検証結果（samples/sd_img2img.png）

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
├─ tools/
│  ├─ visualize_noise.py ノイズ可視化
│  ├─ compare.py         処理前後の比較図
│  └─ inspect_meta.py    PNGメタデータ確認
├─ samples/              検証用画像
├─ output/               出力先（git管理外）
├─ pyproject.toml        依存定義
├─ uv.lock               依存のロックファイル
└─ .python-version      Pythonバージョン（3.12）
```
