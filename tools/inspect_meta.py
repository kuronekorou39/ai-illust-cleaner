"""画像のメタデータ（生成情報・EXIF・XMP・C2PA など）を種類ごとに表示する

使い方: python tools/inspect_meta.py 画像 [画像2 ...]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metadata as md

PREVIEW = 300

for path in sys.argv[1:]:
    meta = md.read(path)
    print(f'== {path}  {meta.format}')
    groups = meta.groups()
    if not groups:
        print('  メタデータなし')
    for g, entries in groups.items():
        print(f'  [{md.GROUPS[g][0]}]')
        for e in entries:
            value = ' '.join(e.value.split())
            print(f'    {e.key}: {value[:PREVIEW]}{"..." if len(value) > PREVIEW else ""}')
    if meta.gen:
        print(f'  生成情報の解析: {meta.gen.tool}')
        for name, value in (('プロンプト', meta.gen.prompt), ('ネガティブ', meta.gen.negative),
                            *meta.gen.settings.items()):
            if value:
                print(f'    {name}: {value[:PREVIEW]}')
    for note in meta.notes:
        print(f'  ※ {note}')
