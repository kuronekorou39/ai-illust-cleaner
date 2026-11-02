"""バージョン番号が pyproject.toml とアプリでそろっているか"""
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gui  # noqa: E402


def test_version_matches_pyproject():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    assert gui.__version__ == project['version']
