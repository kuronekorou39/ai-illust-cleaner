"""PNGのメタデータ（A1111の parameters、ComfyUIの prompt/workflow 等）を表示

使い方: python tools/inspect_meta.py 画像.png [画像2.png ...]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from common import AI_META_KEYS

for path in sys.argv[1:]:
    im = Image.open(path)
    print(f'== {path}  {im.size} {im.mode}')
    hit = False
    for k, v in im.info.items():
        s = str(v)
        mark = ' <- AI生成の手がかり' if k in AI_META_KEYS else ''
        hit |= bool(mark)
        print(f'  {k}: {s[:300]}{"..." if len(s) > 300 else ""}{mark}')
    print('  判定:', '生成パラメータあり' if hit else '生成パラメータなし（消されている可能性もあり）')
