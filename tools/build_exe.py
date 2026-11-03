"""Windows 用の exe を作り、配布用の zip にまとめる

使い方: uv run python tools/build_exe.py
出力:   dist/ai-illust-cleaner/ai-illust-cleaner.exe と dist/ai-illust-cleaner-<版>-windows.zip
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gui import __version__  # noqa: E402

NAME = 'ai-illust-cleaner'
dist = ROOT / 'dist'

subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                '--distpath', str(dist), '--workpath', str(ROOT / 'build'),
                str(ROOT / 'packaging' / f'{NAME}.spec')], check=True)
# ライセンスは、展開したフォルダの一番上（exe の隣）に置く
for doc in (ROOT / 'LICENSE', ROOT / 'docs' / 'licenses.md'):
    shutil.copy2(doc, dist / NAME / doc.name)
archive = shutil.make_archive(str(dist / f'{NAME}-{__version__}-windows'), 'zip', dist, NAME)
print('saved:', archive)
