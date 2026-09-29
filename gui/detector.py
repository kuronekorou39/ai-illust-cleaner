"""AI判定度：公開されているイラスト向け判定モデルで、AI生成らしさの確率を出す

モデルは初回にダウンロードして利用者のフォルダに置く（アプリには同梱しない）。
"""
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QStandardPaths

DOWNLOAD_CHUNK = 1 << 20
DOWNLOAD_TIMEOUT_SEC = 30
NORMALIZE_MEAN = NORMALIZE_STD = 0.5   # 両モデルの前処理設定（preprocessor_config.json）に合わせた値


@dataclass(frozen=True)
class ModelSpec:
    key: str
    title: str        # 画面に出す名前
    repo: str         # Hugging Face のリポジトリ
    file: str
    size_mb: int
    input_side: int   # 入力画像の一辺（この大きさに縮小して判定する）
    ai_index: int     # 出力のうち「AI生成」にあたる番号

    @property
    def url(self):
        return f'https://huggingface.co/{self.repo}/resolve/main/{self.file}'

    @property
    def path(self):
        return model_dir() / f'{self.key}.onnx'


MODELS = (
    ModelSpec('legekka_vit', '判定モデルA（ViT）', 'onnx-community/AI-Anime-Image-Detector-ViT-ONNX',
              'onnx/model_quantized.onnx', 89, 512, 1),
    ModelSpec('saltacc_beit', '判定モデルB（BEiT）', 'freakb0b/anime-ai-detect-ONNX',
              'onnx/model_quantized.onnx', 89, 224, 0),
)
TOTAL_MB = sum(m.size_mb for m in MODELS)


def model_dir():
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / 'models'


def is_installed():
    return all(m.path.exists() for m in MODELS)


def download(on_progress=None, is_cancelled=lambda: False):
    """未取得のモデルをダウンロードする。on_progress(完了バイト数, 全体バイト数)"""
    todo = [m for m in MODELS if not m.path.exists()]
    total = sum(m.size_mb for m in todo) * (1 << 20)
    done = 0
    model_dir().mkdir(parents=True, exist_ok=True)
    for m in todo:
        part = m.path.with_suffix('.part')   # 途中で止まったファイルを完成品と見誤らないため
        with urllib.request.urlopen(m.url, timeout=DOWNLOAD_TIMEOUT_SEC) as res, open(part, 'wb') as f:
            while chunk := res.read(DOWNLOAD_CHUNK):
                if is_cancelled():
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(min(done, total), total)
        if is_cancelled():
            part.unlink(missing_ok=True)
            return False
        part.replace(m.path)
    return True


class Detector:
    """判定モデルの実行。モデルの読み込みは最初の判定時に行う"""

    def __init__(self):
        self._sessions = None
        self._lock = threading.Lock()

    def _load(self):
        import onnxruntime as ort   # 起動を遅くしないよう、使うときに読み込む
        with self._lock:
            if self._sessions is None:
                self._sessions = [ort.InferenceSession(str(m.path), providers=['CPUExecutionProvider'])
                                  for m in MODELS]
        return self._sessions

    def predict(self, display):
        """白背景に合成済みのRGB画像から、モデルごとのAI生成の確率（0〜1）を返す。未取得なら None"""
        if not is_installed():
            return None
        pil = Image.fromarray(display)
        result = []
        for spec, sess in zip(MODELS, self._load()):
            x = np.asarray(pil.resize((spec.input_side, spec.input_side), Image.BILINEAR), np.float32) / 255
            x = ((x - NORMALIZE_MEAN) / NORMALIZE_STD).transpose(2, 0, 1)[None]
            logits = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
            p = np.exp(logits - logits.max())
            result.append(float(p[spec.ai_index] / p.sum()))
        return result


detector = Detector()
