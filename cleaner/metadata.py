"""画像のメタデータの読み取り・解析・消去

読み取り：PNGテキスト・EXIF・XMP・C2PA などを種類（GROUPS）ごとに集める
解析：A1111 / Forge・ComfyUI・NovelAI・InvokeAI の生成情報を、プロンプトや設定に分ける
消去：画像を作り直さず、ファイルから該当部分だけを取り除く。画素も形式も変わらない（PNG / JPEG / WebP）
色の情報（ICCプロファイル・sRGB・ガンマ）は見た目が変わるため、消去の対象にしない
"""
import io
import json
import os
import re
import struct
import tempfile
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import ExifTags, Image, PngImagePlugin

GROUPS = {
    'gen': ('生成情報', 'プロンプト・シード値・モデル名など、生成ツールが書き込む情報です。'),
    'exif': ('EXIF', '作成ソフト・日時・作者・撮影機器・位置情報などの標準的な情報です。'),
    'xmp': ('XMP', 'Photoshop などの編集ソフトが書き込む情報です。作者や編集履歴、AI生成の印が入ることもあります。'),
    'c2pa': ('C2PA', '画像の出どころを証明する、署名付きの情報（コンテンツクレデンシャル）です。'),
    'other': ('その他', 'タイトル・作者・コメント・IPTC・解釈できないデータなど、上記以外の情報です。'),
}
STRIPPABLE_FORMATS = ('PNG', 'JPEG', 'WEBP')   # ファイルの該当部分だけを取り除けるもの

# 生成ツールが書き込むPNGテキストのキー
GEN_KEYS = ('parameters', 'prompt', 'workflow', 'invokeai_metadata', 'invokeai_graph', 'sd-metadata', 'Dream',
            'generation_data', 'fooocus_scheme')
NOVELAI_KEYS = ('Description', 'Comment', 'Software', 'Source', 'Generation time', 'Title')
PNG_XMP_KEY = 'XML:com.adobe.xmp'
# ImageMagick などが書き込む、テキストに埋め込まれたプロファイル
RAW_PROFILE_GROUPS = {'Raw profile type exif': 'exif', 'Raw profile type APP1': 'exif',
                      'Raw profile type xmp': 'xmp', 'Raw profile type iptc': 'other'}

EXIF_IFD, GPS_IFD = 0x8769, 0x8825
USER_COMMENT, IMAGE_DESCRIPTION = 0x9286, 0x010E
MAKE, MODEL, ORIENTATION = 0x010F, 0x0110, 0x0112   # 向きは表示に必要なので、EXIF を消しても残す
EXIF_GEN_PREFIXES = ('prompt:', 'workflow:')        # ComfyUI が WebP の Make / Model に書き込む形式
AI_SOURCE_TYPES = ('trainedAlgorithmicMedia', 'compositeWithTrainedAlgorithmicMedia')   # IPTC の「AI生成」の区分
PREVIEW_CHARS = 2000   # すべての項目の一覧で、長い値を切り詰める長さ
MAX_TEXT_BYTES = 8 * 2 ** 20   # 圧縮されたテキストを展開する上限（小さなファイルが巨大に膨らむ細工に備える）
MAX_TOTAL_TEXT = 32 * 2 ** 20  # 1ファイルで展開するテキストの合計の上限
MAX_XMP_PARSE = 2 * 2 ** 20    # XMP を項目に分けて表示する上限
MAX_SIDE_EQUIV = 4096          # 開ける画像の大きさの上限を、正方形の一辺で表したもの
MAX_PIXELS = MAX_SIDE_EQUIV ** 2   # 約1680万画素。8K では画面での処理に約9GBのメモリが要るため、ここで止める
# 画素の最下位ビットに生成情報を埋める形式（stealth pnginfo）の印。ビットは列ごとに上から並ぶ
STEALTH_MAGICS = (b'stealth_pnginfo', b'stealth_pngcomp')       # アルファ値に埋める形式
STEALTH_RGB_MAGICS = (b'stealth_rgbinfo', b'stealth_rgbcomp')   # RGB に埋める形式
STEALTH_INFO = '__stealth__'   # load_image が返す情報の中で、埋め込みのある場所を示すキー
TEXT_OMITTED = '（読み取れないテキスト）'
PngImagePlugin.MAX_TEXT_CHUNK = MAX_TEXT_BYTES   # Pillow の既定（1MB）では、長いワークフローの画像を開けない

# A1111 の設定の行「Steps: 20, Sampler: Euler a, ...」の1項目（値は引用符で囲まれていることがある）
_A1111_KEY = re.compile(r'[\w \-/().]+')
_A1111_QUOTED = re.compile(r'"(?:\\.|[^\\"])*"')
_SETTING_NAMES = {   # 表示名をそろえる。ここに無い設定は元の名前のまま出す
    'seed': 'シード値', 'noise_seed': 'シード値', 'steps': 'ステップ数', 'sampler': 'サンプラー',
    'sampler_name': 'サンプラー', 'scheduler': 'スケジューラ', 'schedule type': 'スケジューラ',
    'cfg scale': 'CFG', 'cfg': 'CFG', 'cfg_scale': 'CFG', 'scale': 'CFG', 'guidance_scale': 'CFG',
    'model': 'モデル', 'ckpt_name': 'モデル', 'unet_name': 'モデル', 'base_model': 'モデル',
    'model hash': 'モデルのハッシュ', 'size': 'サイズ', 'denoising strength': 'ノイズ除去強度',
    'denoise': 'ノイズ除去強度', 'clip skip': 'Clip skip', 'version': 'バージョン',
}


@dataclass
class Entry:
    group: str
    key: str
    value: str


@dataclass
class GenInfo:
    tool: str
    prompt: str = ''
    negative: str = ''
    settings: dict = field(default_factory=dict)   # 表示名 -> 値


@dataclass
class Metadata:
    format: str = ''
    entries: list = field(default_factory=list)
    gen: GenInfo | None = None
    notes: list = field(default_factory=list)   # 注意書き（AI生成の印・位置情報など）
    has_color: bool = False  # ICCプロファイルなど、残す色の情報があるか
    text_groups: dict = field(default_factory=dict)   # PNGテキストのキー -> 種類（消去で使う）
    exif_has_gen: bool = False   # EXIF の中に生成情報があるか（EXIF を残して生成情報だけ消すとき書き換える）
    stealth: tuple = ()          # 画素の最下位ビットに生成情報が埋め込まれている場所（'alpha' / 'rgb'）

    def groups(self):
        """種類 -> 項目の並び（項目のある種類だけ、GROUPS の順）"""
        found = {}
        for e in self.entries:
            found.setdefault(e.group, []).append(e)
        return {g: found[g] for g in GROUPS if g in found}

    @property
    def strippable(self):
        return self.format in STRIPPABLE_FORMATS


# ---------------------------------------------------------------- 読み取り

def read(path):
    data = Path(path).read_bytes()
    return read_bytes(data)


def read_bytes(data):
    im = Image.open(io.BytesIO(data))
    meta = Metadata(format=im.format or '')
    info = im.info
    meta.has_color = bool(info.get('icc_profile')) or 'srgb' in info or 'gamma' in info

    items = png_text_items(data) if meta.format == 'PNG' else []
    texts = _first_texts(items)
    meta.text_groups = classify_png_texts(items)
    for k, v in items:
        meta.entries.append(Entry(meta.text_groups[k], k, v))

    try:
        _read_exif(im, meta)
    except Exception:   # 読めない EXIF も、下の _read_unlisted で項目として出す
        pass
    xmp = texts.get(PNG_XMP_KEY) or _as_text(info.get('xmp'))
    if xmp and meta.format != 'PNG':
        meta.entries.append(Entry('xmp', 'XMP', xmp))
    if xmp:
        _read_xmp(xmp, meta)
    if meta.format == 'JPEG':
        if info.get('comment'):
            meta.entries.append(Entry('other', 'コメント', _as_text(info['comment'])))
        if info.get('photoshop'):
            meta.entries.append(Entry('other', 'Photoshop / IPTC', f'{len(info["photoshop"])} 項目'))

    c2pa = find_c2pa(data, meta.format)
    if c2pa is not None:
        meta.entries.append(Entry('c2pa', 'マニフェスト', c2pa))

    try:
        meta.gen = meta.gen or parse_generation(texts)
    except Exception:   # 形が想定と違っても「なし」にはせず、生成情報があることは示す
        meta.gen = GenInfo('不明なツール')
    meta.stealth = _image_stealth(im)
    for kind in meta.stealth:
        name = '透明度' if kind == 'alpha' else 'RGB'
        meta.entries.append(Entry('gen', f'{name}に埋め込まれた情報', f'{name}の最下位ビットに書き込まれています'))
    _read_unlisted(data, meta)
    return meta


def unreadable(path):
    """メタデータを解析できなかった画像。「なし」と見せず、すべての種類を消せるようにする"""
    try:
        with Image.open(path) as im:
            fmt = im.format or ''
    except Exception:
        fmt = ''
    entries = [Entry(g, '読み取れなかったデータ', '') for g in ('gen', 'exif', 'xmp', 'other')]
    return Metadata(format=fmt, entries=entries)


def _read_unlisted(data, meta):
    """解釈できなかったブロックも項目にする（表示されないものは、消す対象に選べないため）"""
    def listed(marker):
        return (marker == 0xFE and im_info.get('comment')) or (marker == 0xED and im_info.get('photoshop'))

    im_info = Image.open(io.BytesIO(data)).info if meta.format == 'JPEG' else {}
    if meta.format == 'JPEG':
        blocks = ((None if listed(m) else _jpeg_kind(m, b), b) for m, b in _safe(_jpeg_segments(data)))
    else:
        blocks = _safe(_iter_blocks(data, meta.format))
    shown = set(meta.groups())
    sizes = {}
    for kind, body in blocks:
        if kind is None or (kind == 'exif' and _only_orientation(body)):
            continue
        sizes[kind] = sizes.get(kind, 0) + len(body)
    for kind, size in sizes.items():
        if kind == 'other' or kind not in shown:
            meta.entries.append(Entry(kind, 'そのほかのデータ', f'{size:,} バイト'))


def _inflate(data, limit=MAX_TEXT_BYTES):
    """zlib で圧縮されたデータを、limit まで展開する"""
    return zlib.decompressobj().decompress(data, limit)


def png_text_items(data):
    """PNG のテキスト（tEXt / zTXt / iTXt）を (キー, 値) の並びで返す。画像データの後ろにあるものも含む
    途中でファイルが壊れていたら、そこまでに読めたものを返す。展開する量は合計でも MAX_TOTAL_TEXT まで
    """
    items, budget = [], MAX_TOTAL_TEXT
    for ctype, body in _safe(_png_chunks(data)):
        if ctype not in _PNG_TEXT:
            continue
        key, _, rest = body.partition(b'\x00')
        limit = min(MAX_TEXT_BYTES, budget)
        try:
            if ctype == b'tEXt':
                value = rest.decode('latin-1')
            elif ctype == b'zTXt':
                raw = _inflate(rest[1:], limit) if limit > 0 else None
                value = TEXT_OMITTED if raw is None else raw.decode('latin-1')
                budget -= len(raw or b'')
            else:
                compressed, rest = rest[0], rest[2:]
                _lang, _, rest = rest.partition(b'\x00')
                _translated, _, rest = rest.partition(b'\x00')
                raw = rest if not compressed else _inflate(rest, limit) if limit > 0 else None
                value = TEXT_OMITTED if raw is None else raw.decode('utf-8', 'replace')
                budget -= len(raw or b'') if compressed else 0
        except (zlib.error, IndexError):
            value = TEXT_OMITTED
        items.append((key.decode('latin-1'), value))
    return items


def _first_texts(items):
    """キー -> 値。同じキーが並ぶときは先頭を使うが、生成情報の JSON があればそちらを優先する"""
    texts = {}
    for k, v in items:
        if k not in texts or (k == 'Comment' and _json_with(v, 'prompt', 'uc')):
            texts[k] = v
    return texts


def png_texts(data):
    return _first_texts(png_text_items(data))


def classify_png_texts(items):
    """PNGテキストのキーごとに種類を決める。同じキーのどれかが生成情報なら、そのキーは生成情報にする"""
    novelai = _first_texts(items).get('Software', '').startswith('NovelAI')
    groups = {}
    for k, v in items:
        if k == PNG_XMP_KEY:
            group = 'xmp'
        elif k in RAW_PROFILE_GROUPS:
            group = RAW_PROFILE_GROUPS[k]
        elif k in GEN_KEYS or (novelai and k in NOVELAI_KEYS) or (k == 'Comment' and _json_with(v, 'prompt', 'uc')):
            group = 'gen'
        else:
            group = 'other'
        if groups.get(k) != 'gen':
            groups[k] = group
    return groups


def _text_group(key, meta):
    return meta.text_groups.get(key) or ('gen' if key in GEN_KEYS else 'other')


def has_stealth(alpha):
    """アルファ値（2次元配列）の最下位ビットに、生成情報の印があるか"""
    h, w = alpha.shape
    bits = len(STEALTH_MAGICS[0]) * 8
    if h * w < bits:
        return False
    head = alpha[:, :-(-bits // h)].T.ravel()[:bits] & 1
    return np.packbits(head).tobytes() in STEALTH_MAGICS


def has_stealth_rgb(rgb):
    """RGB（高さ×幅×3）の最下位ビットに、生成情報の印があるか。1画素に R・G・B の順で3ビット入る"""
    h, w = rgb.shape[:2]
    pixels = len(STEALTH_RGB_MAGICS[0]) * 8 // 3
    if h * w < pixels:
        return False
    head = rgb[:, :-(-pixels // h)].transpose(1, 0, 2).reshape(-1, 3)[:pixels].ravel() & 1
    return np.packbits(head).tobytes() in STEALTH_RGB_MAGICS


def stealth_kinds(rgb, alpha):
    """生成情報が埋め込まれている場所を ('alpha', 'rgb') の形で返す"""
    kinds = []
    if alpha is not None and has_stealth(alpha):
        kinds.append('alpha')
    if rgb is not None and has_stealth_rgb(rgb):
        kinds.append('rgb')
    return tuple(kinds)


def scrub_lsb(values):
    """最下位ビットの情報を消す（変化は最大1階調）。0 と 255 は変わらないので、白や完全な透明は保たれる"""
    return np.where(values >= 128, values | 1, values & 0xFE).astype(np.uint8)


def scrub_stealth(rgb, alpha, kinds):
    """kinds の場所の最下位ビットを消した (rgb, alpha) を返す"""
    return (scrub_lsb(rgb) if 'rgb' in kinds else rgb,
            scrub_lsb(alpha) if 'alpha' in kinds and alpha is not None else alpha)


def _image_stealth(im):
    if im.format == 'JPEG' or im.mode not in ('RGB', 'RGBA', 'LA') or im.width * im.height > MAX_PIXELS:
        return ()
    try:
        pixels = np.asarray(im)
        return stealth_kinds(None if im.mode == 'LA' else pixels[..., :3],
                             pixels[..., -1] if 'A' in im.getbands() else None)
    except Exception:   # 画素が壊れている
        return ()


def _as_text(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace').strip('\x00')
    return str(value)


def _json_with(text, *keys):
    obj = _load_json(text)
    return isinstance(obj, dict) and any(k in obj for k in keys)


def _load_json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return None


def decode_user_comment(raw):
    """EXIF の UserComment（先頭8バイトが文字コード）を文字列にする"""
    if isinstance(raw, str):
        return raw.strip('\x00')
    if not isinstance(raw, bytes):   # 型が仕様と違う細工されたファイル
        return str(raw)
    head, body = raw[:8], raw[8:]
    if head.startswith(b'UNICODE'):
        # A1111 は UTF-16BE で書く。BE で読めなければ LE とみなす
        be = body.decode('utf-16-be', 'replace')
        le = body.decode('utf-16-le', 'replace')
        text = be if sum(c.isprintable() for c in be) >= sum(c.isprintable() for c in le) else le
    elif head.startswith(b'ASCII') or head == b'\x00' * 8:
        text = body.decode('utf-8', 'replace')
    else:
        text = raw.decode('utf-8', 'replace')
    return text.strip('\x00 ')


def _exif_value(tag, value):
    if tag == USER_COMMENT:
        return decode_user_comment(value)
    if isinstance(value, bytes):
        text = value.decode('utf-8', 'replace').strip('\x00')
        return text if text.isprintable() else f'（{len(value)} バイトのデータ）'
    if isinstance(value, tuple) and len(value) > 8:
        return f'（{len(value)} 個の値）'
    return str(value)


def _read_exif(im, meta):
    try:
        exif = im.getexif()
    except Exception:
        return
    if not exif:
        return
    items = [(ExifTags.TAGS.get(t, hex(t)), t, v) for t, v in exif.items()
             if t not in (EXIF_IFD, GPS_IFD, ORIENTATION)]
    items += [(ExifTags.TAGS.get(t, hex(t)), t, v) for t, v in exif.get_ifd(EXIF_IFD).items()]
    items += [(f'GPS {ExifTags.GPSTAGS.get(t, hex(t))}', t, v) for t, v in exif.get_ifd(GPS_IFD).items()]
    if exif.get_ifd(GPS_IFD):
        meta.notes.append('EXIF に位置情報（GPS）が含まれています')
    for name, tag, value in items:
        text = _exif_value(tag, value)
        gen = _exif_gen(tag, text)
        if gen is not None:
            if meta.gen is None or not meta.gen.prompt:
                meta.gen = gen
            meta.exif_has_gen = True
            meta.entries.append(Entry('gen', f'EXIF {name}', text))
        else:
            meta.entries.append(Entry('exif', name, text))


def _exif_gen(tag, text):
    """EXIF の値が生成情報なら GenInfo を返す"""
    if tag in (MAKE, MODEL):
        if text.startswith('prompt:'):
            return parse_comfy(_load_json(text[len('prompt:'):]))
        return GenInfo('ComfyUI') if text.startswith(EXIF_GEN_PREFIXES) else None
    if tag not in (USER_COMMENT, IMAGE_DESCRIPTION):
        return None
    gen = parse_a1111(text)
    obj = None if gen else _load_json(text)
    if not isinstance(obj, dict):
        return gen
    if 'uc' in obj:
        return parse_novelai({'Comment': text})
    if 'prompt' in obj:
        return _parse_json_params(obj)
    if any(isinstance(v, dict) and 'class_type' in v for v in obj.values()):
        return parse_comfy(obj)
    return None


def _read_xmp(xmp, meta):
    """XMP の中から、AI生成の印（IPTC の DigitalSourceType）を探す"""
    for kind in AI_SOURCE_TYPES:
        if re.search(rf'digitalsourcetype/{kind}\b', xmp, re.I):
            meta.notes.append(f'XMP に AI 生成の印があります（{kind}）')
            return


def xmp_fields(xmp):
    """XMP の値を (名前, 値) の並びにする（表示用）。読めなければ空"""
    if len(xmp) > MAX_XMP_PARSE:
        return []
    prefixes = {uri: p for p, uri in re.findall(r'xmlns:([\w-]+)="([^"]+)"', xmp)}

    def name(tag):
        uri, _, local = tag[1:].partition('}') if tag.startswith('{') else ('', '', tag)
        return f'{prefixes.get(uri, uri.rstrip("/#").rsplit("/", 1)[-1])}:{local}' if uri else local

    try:
        root = ET.fromstring(xmp[xmp.find('<'):].encode('utf-8'))
    except ET.ParseError:
        return []
    rdf = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}'
    parent = {child: el for el in root.iter() for child in el}
    out = []
    for el in root.iter():
        text = (el.text or '').strip()
        if el.tag == f'{rdf}Description':
            out += [(name(k), v) for k, v in el.attrib.items() if not k.startswith(rdf)]
        elif el.tag == f'{rdf}li' and text:
            # rdf:li → rdf:Seq / Bag / Alt → プロパティ
            prop = parent.get(parent.get(el))
            out.append((name(prop.tag) if prop is not None else 'rdf:li', text))
        elif not el.tag.startswith(rdf) and text and len(el) == 0:
            out.append((name(el.tag), text))
    return out


def find_c2pa(data, fmt):
    """C2PA のマニフェストがあれば、その概要を返す（無ければ None）"""
    blocks = [body for kind, body in _safe(_iter_blocks(data, fmt)) if kind == 'c2pa']
    if not blocks:
        return None
    raw = b''.join(blocks)
    text = f'{len(raw):,} バイト'
    found = re.search(rb'claim_generator.{1,3}?([\x20-\x7e]{3,80})', raw)
    if found:
        text += f'　作成：{found.group(1).decode("ascii")}'
    return text


# ---------------------------------------------------------------- 生成情報の解析

def parse_generation(texts):
    """PNGテキストから生成情報を取り出す（見つからなければ None）"""
    if 'parameters' in texts:
        params = texts['parameters']
        return _parse_json_params(_load_json(params)) or parse_a1111(params) or GenInfo('不明なツール', params)
    if 'prompt' in texts or 'workflow' in texts:
        return parse_comfy(_load_json(texts.get('prompt')))
    if texts.get('Software', '').startswith('NovelAI') or _json_with(texts.get('Comment'), 'prompt', 'uc'):
        return parse_novelai(texts)
    if 'invokeai_metadata' in texts:
        return parse_invoke(_load_json(texts['invokeai_metadata']))
    if 'sd-metadata' in texts:
        return parse_invoke_legacy(_load_json(texts['sd-metadata']))
    for k in ('generation_data', 'Dream'):
        if k in texts:
            return _parse_json_params(_load_json(texts[k])) or GenInfo('不明なツール', texts[k])
    return None


def _setting(settings, key, value):
    if value is None or value == '':
        return
    name = _SETTING_NAMES.get(str(key).lower(), str(key))
    settings.setdefault(name, str(value))


def parse_a1111(text):
    """A1111 / Forge 形式（プロンプト・Negative prompt:・設定の行）。形式が違えば None"""
    lines = text.strip().split('\n')
    idx = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].startswith('Steps: ')), None)
    if idx is None:
        return None
    body = lines[:idx]
    neg_at = next((i for i, line in enumerate(body) if line.startswith('Negative prompt:')), len(body))
    prompt = '\n'.join(body[:neg_at]).strip()
    negative = '\n'.join(body[neg_at:])[len('Negative prompt:'):].strip() if neg_at < len(body) else ''
    settings = {}
    for key, value in _a1111_settings(' '.join(lines[idx:])):
        _setting(settings, key, value)
    tool = 'Forge' if settings.get('バージョン', '').startswith('f') else 'A1111 WebUI'
    return GenInfo(tool, prompt, negative, settings)


def _a1111_settings(line):
    """設定の行を (名前, 値) に分ける。長い行でも時間が行の長さに比例するよう、前から1回だけ読む"""
    pos, n = 0, len(line)
    while pos < n:
        colon = line.find(':', pos)
        if colon < 0:
            return
        key = line[max(pos, line.rfind(',', pos, colon) + 1):colon].strip()
        start = colon + 1
        while start < n and line[start].isspace():
            start += 1
        quoted = _A1111_QUOTED.match(line, start)
        if quoted:
            value, end = quoted.group()[1:-1].replace('\\"', '"'), quoted.end()
            comma = line.find(',', end)
            pos = n if comma < 0 else comma + 1
        else:
            comma = line.find(',', start)
            end = n if comma < 0 else comma
            value, pos = line[start:end].strip(), end + 1
        if _A1111_KEY.fullmatch(key):
            yield key, value


def _parse_json_params(obj):
    """Fooocus などの、JSON で書かれた parameters"""
    if not isinstance(obj, dict) or 'prompt' not in obj:
        return None
    gen = GenInfo('Fooocus' if 'fooocus' in json.dumps(obj).lower() else '不明なツール',
                  str(obj.get('prompt', '')), str(obj.get('negative_prompt', obj.get('negative', ''))))
    for k, v in obj.items():
        if k not in ('prompt', 'negative_prompt', 'negative') and not isinstance(v, (dict, list)):
            _setting(gen.settings, k, v)
    return gen


def parse_comfy(graph):
    """ComfyUI の prompt（ノードの依存関係）から、サンプラーにつながるプロンプトと設定を取り出す"""
    gen = GenInfo('ComfyUI')
    if not isinstance(graph, dict):
        return gen   # workflow だけの場合（画面の配置情報で、設定は読み取らない）
    nodes = {k: v for k, v in graph.items() if isinstance(v, dict) and isinstance(v.get('inputs'), dict)}

    def text_of(link, seen, depth=0):
        """つながりをたどってテキストを探す。同じノードは1度しか見ない（循環や分岐の爆発を防ぐ）"""
        if not isinstance(link, list) or not link or depth > 8:
            return ''
        node = str(link[0])
        if node not in nodes or node in seen:
            return ''
        seen.add(node)
        inputs = nodes[node]['inputs']
        for key in ('text', 'text_g', 'prompt', 'positive'):
            if isinstance(inputs.get(key), str):
                return inputs[key]
            if isinstance(inputs.get(key), list):
                return text_of(inputs[key], seen, depth + 1)
        # 条件の合成などは、最初につながっている入力をたどる
        return next((t for v in inputs.values() if (t := text_of(v, seen, depth + 1))), '')

    sampler = next((n for n in nodes.values() if 'sampler' in str(n.get('class_type', '')).lower()
                    and 'positive' in n['inputs']), None)
    if sampler:
        inp = sampler['inputs']
        gen.prompt = text_of(inp.get('positive'), set())
        gen.negative = text_of(inp.get('negative'), set())
        for k in ('seed', 'noise_seed', 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise'):
            if not isinstance(inp.get(k), list):
                _setting(gen.settings, k, inp.get(k))
    else:
        texts = [n['inputs']['text'] for n in nodes.values() if isinstance(n['inputs'].get('text'), str)]
        gen.prompt = '\n\n'.join(texts)
    for n in nodes.values():
        inp = n['inputs']
        for k in ('ckpt_name', 'unet_name'):
            if isinstance(inp.get(k), str):
                _setting(gen.settings, k, inp[k])
        if 'latent' in str(n.get('class_type', '')).lower() and isinstance(inp.get('width'), int):
            _setting(gen.settings, 'size', f"{inp['width']}x{inp.get('height')}")
    return gen


def parse_novelai(texts):
    gen = GenInfo('NovelAI', texts.get('Description', ''))
    obj = _load_json(texts.get('Comment')) or {}
    if isinstance(obj, dict):
        gen.prompt = str(obj.get('prompt', gen.prompt))
        gen.negative = str(obj.get('uc', ''))
        for k in ('seed', 'steps', 'scale', 'sampler', 'noise_schedule', 'strength'):
            _setting(gen.settings, k, obj.get(k))
        if 'width' in obj:
            _setting(gen.settings, 'size', f"{obj['width']}x{obj.get('height')}")
    _setting(gen.settings, 'model', texts.get('Source'))
    return gen


def parse_invoke(obj):
    gen = GenInfo('InvokeAI')
    if not isinstance(obj, dict):
        return gen
    gen.prompt = str(obj.get('positive_prompt', ''))
    gen.negative = str(obj.get('negative_prompt', ''))
    for k in ('seed', 'steps', 'cfg_scale', 'scheduler'):
        _setting(gen.settings, k, obj.get(k))
    model = obj.get('model')
    _setting(gen.settings, 'model', model.get('name') if isinstance(model, dict) else model)
    if 'width' in obj:
        _setting(gen.settings, 'size', f"{obj['width']}x{obj.get('height')}")
    return gen


def parse_invoke_legacy(obj):
    gen = GenInfo('InvokeAI')
    if not isinstance(obj, dict):
        return gen
    image = obj.get('image') if isinstance(obj.get('image'), dict) else {}
    prompt = image.get('prompt')
    gen.prompt = prompt[0].get('prompt', '') if isinstance(prompt, list) and prompt else str(prompt or '')
    for k in ('seed', 'steps', 'cfg_scale', 'sampler'):
        _setting(gen.settings, k, image.get(k))
    _setting(gen.settings, 'model', obj.get('model_weights'))
    return gen


# ---------------------------------------------------------------- 消去

class UnsupportedFormat(Exception):
    pass


def strip(data, remove, meta=None):
    """remove（GROUPS のキーの集合）に当たる部分を取り除いたファイルの中身を返す"""
    meta = meta or read_bytes(data)
    if meta.format == 'PNG':
        return _strip_png(data, remove, meta)
    if meta.format == 'JPEG':
        return _strip_jpeg(data, remove, meta)
    if meta.format == 'WEBP':
        return _strip_webp(data, remove, meta)
    raise UnsupportedFormat(meta.format)


def strip_file(src, dst, remove):
    data = Path(src).read_bytes()
    write_atomic(dst, strip(data, remove))


def write_atomic(dst, data):
    """一時ファイルに書いてから置き換える。途中で失敗しても、既にあるファイルは壊れない"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=f'.{dst.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load_exif(exif_bytes):
    exif = Image.Exif()
    exif.load(exif_bytes if exif_bytes.startswith(b'Exif') else b'Exif\x00\x00' + exif_bytes)
    return exif


def _exif_bytes(exif):
    out = exif.tobytes()
    return out[6:] if out.startswith(b'Exif\x00\x00') else out


def _exif_without_gen(exif_bytes):
    """生成情報の入った項目だけを除いた EXIF（先頭の Exif\\0\\0 は付けない）"""
    exif = _load_exif(exif_bytes)
    for tag in (IMAGE_DESCRIPTION, MAKE, MODEL):
        if tag in exif and _exif_gen(tag, _exif_value(tag, exif[tag])):
            del exif[tag]
    ifd = exif.get_ifd(EXIF_IFD)
    if USER_COMMENT in ifd:
        del ifd[USER_COMMENT]
    return _exif_bytes(exif)


def _orientation_exif(exif_bytes):
    """向きだけを残した EXIF。向きの指定がなければ None（消すと画像が横倒しになるため残す）"""
    try:
        value = _load_exif(exif_bytes).get(ORIENTATION, 1)
    except Exception:
        return None
    if value == 1:
        return None
    exif = Image.Exif()
    exif[ORIENTATION] = value
    return _exif_bytes(exif)


def _only_orientation(exif_bytes):
    try:
        return set(_load_exif(exif_bytes)) <= {ORIENTATION}
    except Exception:
        return False


def _rewritten_exif(body, action):
    """action（'clean' / 'drop'）に応じて書き換えた EXIF。何も残らなければ None"""
    return _exif_without_gen(body) if action == 'clean' else _orientation_exif(body)


def _exif_action(remove, meta):
    """EXIF をどうするか：'drop'（丸ごと消す）/ 'clean'（生成情報だけ消す）/ 'keep'"""
    if 'exif' in remove:
        return 'drop'
    return 'clean' if 'gen' in remove and meta.exif_has_gen else 'keep'


def _iter_blocks(data, fmt):
    """ファイルを (種類, 中身) の並びに分ける。種類は消去の単位で、None は常に残すもの"""
    if fmt == 'PNG':
        for _ctype, body, kind in _png_blocks(data):
            yield kind, body
    elif fmt == 'JPEG':
        for marker, body in _jpeg_segments(data):
            yield _jpeg_kind(marker, body), body
    elif fmt == 'WEBP':
        for fourcc, body in _webp_chunks(data):
            yield _webp_kind(fourcc), body


# ---- PNG

class BrokenFile(ValueError):
    """ファイルの構造が壊れていて読めない"""


def _safe(blocks):
    """読み取り用：壊れたところで止め、それまでに読めた分だけを返す"""
    try:
        yield from blocks
    except BrokenFile:
        return


_TRAILER = b''   # ファイルの終わりの印より後ろにある、形式に沿わないデータ
_PNG_TEXT = (b'tEXt', b'zTXt', b'iTXt')
# 画像の表示に必要なチャンク。これ以外の未知のチャンクは「その他」として消せるようにする
_PNG_KEEP = {b'IHDR', b'PLTE', b'IDAT', b'IEND', b'tRNS', b'gAMA', b'cHRM', b'sRGB', b'iCCP', b'sBIT', b'cICP',
             b'mDCV', b'cLLI', b'pHYs', b'bKGD', b'hIST', b'acTL', b'fcTL', b'fdAT'}


def _png_chunks(data):
    pos, ended = 8, False
    while pos + 8 <= len(data):
        length, ctype = struct.unpack('>I4s', data[pos:pos + 8])
        if pos + 12 + length > len(data):
            if ended:
                yield _TRAILER, data[pos:]
                return
            raise BrokenFile('PNG のチャンクが途中で切れています')
        yield ctype, data[pos + 8:pos + 8 + length]
        ended = ended or ctype == b'IEND'
        pos += 12 + length


def _png_blocks(data):
    """(チャンク名, 中身, 種類) の並び。IEND より後ろは表示に使われないので、テキスト以外は「その他」"""
    ended = False
    for ctype, body in _png_chunks(data):
        if ctype in _PNG_TEXT:
            kind = None
        elif ctype == b'caBX':
            kind = 'c2pa'
        elif ctype == b'eXIf':
            kind = 'exif'
        else:
            kind = None if ctype in _PNG_KEEP and not ended else 'other'
        yield ctype, body, kind
        ended = ended or ctype == b'IEND'


def _png_chunk(ctype, body):
    return struct.pack('>I', len(body)) + ctype + body + struct.pack('>I', zlib.crc32(ctype + body) & 0xFFFFFFFF)


def _png_raw(ctype, body):
    return body if ctype == _TRAILER else _png_chunk(ctype, body)


def _scrub_png_pixels(data, kinds):
    """画素の最下位ビットに埋め込まれた情報を消す。画像データだけを作り直し、ほかのチャンクは保つ"""
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        bands = [Image.fromarray(scrub_lsb(np.asarray(band))) if ('alpha' if name == 'A' else 'rgb') in kinds else band
                 for name, band in zip(im.getbands(), im.split())]
        buf = io.BytesIO()
        Image.merge(im.mode, bands).save(buf, 'PNG')
    fresh = list(_png_chunks(buf.getvalue()))
    ihdr = next(b for c, b in fresh if c == b'IHDR')
    idat = [_png_chunk(c, b) for c, b in fresh if c == b'IDAT']
    out, wrote = [data[:8]], False
    for ctype, body in _png_chunks(data):
        if ctype == b'IDAT':
            if not wrote:
                out += idat
                wrote = True
        elif ctype != b'sBIT':
            out.append(_png_raw(ctype, ihdr if ctype == b'IHDR' else body))
    return b''.join(out)


def _strip_png(data, remove, meta):
    if meta.stealth and 'gen' in remove:
        data = _scrub_png_pixels(data, meta.stealth)
    out = [data[:8]]
    action = _exif_action(remove, meta)
    for ctype, body, kind in _png_blocks(data):
        if ctype in _PNG_TEXT:
            if _text_group(body.split(b'\x00', 1)[0].decode('latin-1'), meta) in remove:
                continue
        elif kind == 'exif' and action != 'keep':
            body = _rewritten_exif(body, action)
            if body is None:
                continue
        elif kind in remove:
            continue
        out.append(_png_raw(ctype, body))
    return b''.join(out)


# ---- JPEG

_SOS, _EOI = 0xDA, 0xD9
_JPEG_TRAILER = -1   # EOI より後ろのデータ（マーカーの代わりの印）
_STANDALONE = {0x01, *range(0xD0, 0xD8)}   # 長さを持たないマーカー（TEM・RST）
_NEXT_MARKER = re.compile(rb'\xff[^\x00\xd0-\xd7\xff]')   # 圧縮データの次のマーカー


def _jpeg_segments(data):
    """セグメントを (マーカー, 中身) で返す。プログレッシブ JPEG のスキャンの間にあるものも含む
    SOS の中身は圧縮データの終わりまで。EOI より後ろがあれば (_JPEG_TRAILER, 残り全部)
    """
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF or pos + 1 >= len(data):
            raise BrokenFile('JPEG のセグメントを読めません')
        marker = data[pos + 1]
        if marker == 0xFF:   # 詰め物
            pos += 1
            continue
        if marker in _STANDALONE:
            yield marker, b''
            pos += 2
            continue
        if marker == _EOI:
            yield marker, b''
            if pos + 2 < len(data):
                yield _JPEG_TRAILER, data[pos + 2:]
            return
        if marker == _SOS:
            found = _NEXT_MARKER.search(data, pos + 2)
            end = found.start() if found else len(data)
            yield marker, data[pos + 2:end]
            pos = end
            continue
        if pos + 4 > len(data):
            raise BrokenFile('JPEG のセグメントが途中で切れています')
        length = struct.unpack('>H', data[pos + 2:pos + 4])[0]
        if length < 2 or pos + 2 + length > len(data):
            raise BrokenFile('JPEG のセグメントが途中で切れています')
        yield marker, data[pos + 4:pos + 2 + length]
        pos += 2 + length


def _jpeg_kind(marker, body):
    """消去の単位。表示に要るもの（JFIF・ICC・Adobe の色変換・画像データ）は None で、未知の APP は「その他」"""
    if marker == _JPEG_TRAILER:
        return 'other'
    if marker == 0xE1 and body.startswith(b'Exif\x00'):
        return 'exif'
    if marker == 0xE1 and body.startswith(b'http://ns.adobe.com/'):
        return 'xmp'
    if marker == 0xEB and (b'jumb' in body[:64] or b'c2pa' in body[:64]):
        return 'c2pa'
    if (marker == 0xE0 and body.startswith(b'JFIF\x00')) or (marker == 0xE2 and body.startswith(b'ICC_PROFILE\x00')) \
            or (marker == 0xEE and body.startswith(b'Adobe')):
        return None
    if 0xE0 <= marker <= 0xEF or marker == 0xFE:   # APP（Photoshop / IPTC など）、コメント
        return 'other'
    return None


def _jpeg_segment(marker, body):
    return bytes([0xFF, marker]) + struct.pack('>H', len(body) + 2) + body


def _strip_jpeg(data, remove, meta):
    out = [data[:2]]
    action = _exif_action(remove, meta)
    for marker, body in _jpeg_segments(data):
        kind = _jpeg_kind(marker, body)
        if marker == _JPEG_TRAILER:
            if kind not in remove:
                out.append(body)
        elif marker in (_SOS, _EOI):
            out.append(bytes([0xFF, marker]) + body)
        elif marker in _STANDALONE:
            out.append(bytes([0xFF, marker]))
        elif kind == 'exif' and action != 'keep':
            tiff = _rewritten_exif(body, action)
            if tiff is not None:
                out.append(_jpeg_segment(marker, b'Exif\x00\x00' + tiff))
        elif kind not in remove:
            out.append(_jpeg_segment(marker, body))
    return b''.join(out)


# ---- WebP

_VP8X_EXIF, _VP8X_XMP = 0x08, 0x04


_WEBP_KEEP = {b'VP8X', b'VP8 ', b'VP8L', b'ALPH', b'ANIM', b'ANMF', b'ICCP'}   # 画像の表示に必要なもの


def _webp_kind(fourcc):
    if fourcc in _WEBP_KEEP:
        return None
    return {b'C2PA': 'c2pa', b'EXIF': 'exif', b'XMP ': 'xmp'}.get(fourcc, 'other')


def _webp_chunks(data):
    """チャンクを (名前, 中身) で返す。RIFF の範囲より後ろがあれば (_TRAILER, 残り全部)"""
    pos = 12
    end = min(len(data), 8 + struct.unpack('<I', data[4:8])[0])
    while pos + 8 <= end:
        fourcc, size = struct.unpack('<4sI', data[pos:pos + 8])
        if pos + 8 + size > end:
            raise BrokenFile('WebP のチャンクが途中で切れています')
        yield fourcc, data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    if end < len(data):
        yield _TRAILER, data[end:]


def _webp_chunk(fourcc, body):
    return fourcc + struct.pack('<I', len(body)) + body + (b'\x00' if len(body) & 1 else b'')


def _strip_webp(data, remove, meta):
    if meta.stealth and 'gen' in remove:
        raise UnsupportedFormat('画素に埋め込まれた生成情報は、WebP のままでは消せません。'
                                '「ノイズ除去と背景の透過を反映する」で PNG として保存してください。')
    action = _exif_action(remove, meta)
    chunks, trailer = [], b''
    for fourcc, body in _webp_chunks(data):
        kind = _webp_kind(fourcc)
        if fourcc == _TRAILER:
            trailer = b'' if kind in remove else body
            continue
        if kind == 'exif' and action != 'keep':
            body = _rewritten_exif(body, action)
            if body is None:
                continue
        elif kind in remove:
            continue
        chunks.append((fourcc, body))
    present = {f for f, _ in chunks}
    out = []
    for fourcc, body in chunks:
        if fourcc == b'VP8X':   # 拡張ヘッダの「EXIF あり」「XMP あり」の印を実態に合わせる
            flags = body[0] & ~(_VP8X_EXIF | _VP8X_XMP)
            flags |= (_VP8X_EXIF if b'EXIF' in present else 0) | (_VP8X_XMP if b'XMP ' in present else 0)
            body = bytes([flags]) + body[1:]
        out.append(_webp_chunk(fourcc, body))
    payload = b'WEBP' + b''.join(out)
    return b'RIFF' + struct.pack('<I', len(payload)) + payload + trailer
