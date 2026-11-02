"""メタデータと塗り面ノイズの表示に使う区分"""
from common import AI_META_KEYS

# 塗り面ノイズ平均の区分。サンプルの実測（手描き 0.006、SD生成 0.149）から置いた暫定値
NOISE_LOW = 0.02      # これ未満は「少ない」
NOISE_HIGH = 0.08     # これ以上は「多い」
NOISE_EXTREME = 0.5   # これ以上はVAE由来の塗り面ノイズ（±1階調程度）の範囲を超えており、このツールの対象外


def ai_meta(meta):
    """AI生成ツールが書き込んだとみられるテキストメタデータだけを返す"""
    return {k: v for k, v in meta.items() if k in AI_META_KEYS and isinstance(v, str)}


def noise_level(mean):
    if mean >= NOISE_EXTREME:
        return 'extreme'
    if mean >= NOISE_HIGH:
        return 'high'
    return 'mid' if mean >= NOISE_LOW else 'low'
