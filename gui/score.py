"""AI判定度と塗り面ノイズの表示に使う区分・書式"""
from common import AI_META_KEYS
from .detector import MODELS

# 判定モデルは確率を出すだけで断定はできないため、0% と 100% は表示しない
PERCENT_MIN = 1
PERCENT_MAX = 99
LIKELY_AI = 0.5       # これ以上なら「AI生成寄り」として強調する

# 塗り面ノイズ平均の区分。サンプルの実測（手描き 0.006、SD生成 0.149）から置いた暫定値
NOISE_LOW = 0.02      # これ未満は「少ない」
NOISE_HIGH = 0.08     # これ以上は「多い」

HELP = ('公開されているイラスト向けの判定モデル2つに画像を見せ、\n'
        'AI生成と判定された確率の平均を表示します。\n'
        '画像の内容だけを見ており、メタデータやノイズの量は含みません。\n\n'
        '・判定モデルは誤ることがあり、断定はできません\n'
        '・手描きを元にした img2img や、加工した画像は低く出やすいです\n'
        '・他の判定サービスとは結果が一致しません\n\n'
        + '\n'.join(f'{m.title}：{m.repo}' for m in MODELS))


def ai_meta(meta):
    """AI生成ツールが書き込んだとみられるテキストメタデータだけを返す"""
    return {k: v for k, v in meta.items() if k in AI_META_KEYS and isinstance(v, str)}


def combine(probs):
    """モデルごとの確率をまとめた判定度（単純平均）"""
    return sum(probs) / len(probs)


def percent(p):
    value = p * 100
    if value < PERCENT_MIN:
        return f'{PERCENT_MIN}%未満'
    if value > PERCENT_MAX:
        return f'{PERCENT_MAX}%超'
    return f'{value:.0f}%'


def noise_level(mean):
    if mean >= NOISE_HIGH:
        return 'high'
    return 'mid' if mean >= NOISE_LOW else 'low'
