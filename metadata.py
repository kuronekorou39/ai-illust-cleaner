"""画像のメタデータの読み取り・解析・消去

読み取り：PNGテキスト・EXIF・XMP・C2PA などを種類（GROUPS）ごとに集める
解析：A1111 / Forge・ComfyUI・NovelAI・InvokeAI の生成情報を、プロンプトや設定に分ける
消去：画像を作り直さず、ファイルから該当部分だけを取り除く。画素も形式も変わらない（PNG / JPEG / WebP）
色の情報（ICCプロファイル・sRGB・ガンマ）は見た目が変わるため、消去の対象にしない
"""
import io
import json
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ExifTags, Image

GROUPS = {
    'gen': ('生成情報', 'プロンプト・シード値・モデル名など、生成ツールが書き込む情報です。'),
    'exif': ('EXIF', '作成ソフト・日時・作者・撮影機器・位置情報などの標準的な情報です。'),
    'xmp': ('XMP', 'Photoshop などの編集ソフトが書き込む情報です。作者や編集履歴、AI生成の印が入ることもあります。'),
    'c2pa': ('C2PA', '画像の出どころを証明する、署名付きの情報（コンテンツクレデンシャル）です。'),
    'other': ('その他', 'タイトル・作者・コメント・IPTC など、上記以外の情報です。'),
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
AI_SOURCE_TYPES = ('trainedAlgorithmicMedia', 'compositeWithTrainedAlgorithmicMedia')   # IPTC の「AI生成」の区分
PREVIEW_CHARS = 2000   # すべての項目の一覧で、長い値を切り詰める長さ

# A1111 の設定の行「Steps: 20, Sampler: Euler a, ...」の1項目（値は引用符で囲まれていることがある）
_A1111_SETTING = re.compile(r'\s*([\w \-/().]+?):\s*("(?:\\.|[^\\"])*"|[^,]*)(?:,|$)')
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

    texts = png_texts(data) if meta.format == 'PNG' else {}
    meta.text_groups = classify_png_texts(texts)
    for k, v in texts.items():
        meta.entries.append(Entry(meta.text_groups[k], k, v))

    _read_exif(im, meta)
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

    meta.gen = meta.gen or parse_generation(texts)
    return meta


def png_texts(data):
    """PNG のテキスト（tEXt / zTXt / iTXt）をキー -> 値で返す。画像データの後ろにあるものも含む"""
    texts = {}
    for ctype, body in _png_chunks(data):
        if ctype not in (b'tEXt', b'zTXt', b'iTXt'):
            continue
        key, _, rest = body.partition(b'\x00')
        try:
            if ctype == b'tEXt':
                value = rest.decode('latin-1')
            elif ctype == b'zTXt':
                value = zlib.decompress(rest[1:]).decode('latin-1')
            else:
                compressed, rest = rest[0], rest[2:]
                _lang, _, rest = rest.partition(b'\x00')
                _translated, _, rest = rest.partition(b'\x00')
                value = (zlib.decompress(rest) if compressed else rest).decode('utf-8', 'replace')
        except (zlib.error, IndexError):
            continue
        texts.setdefault(key.decode('latin-1'), value)
    return texts


def classify_png_texts(texts):
    """PNGテキストのキーごとに種類を決める"""
    novelai = texts.get('Software', '').startswith('NovelAI')
    groups = {}
    for k, v in texts.items():
        if k == PNG_XMP_KEY:
            groups[k] = 'xmp'
        elif k in RAW_PROFILE_GROUPS:
            groups[k] = RAW_PROFILE_GROUPS[k]
        elif k in GEN_KEYS or (novelai and k in NOVELAI_KEYS) or (k == 'Comment' and _json_with(v, 'prompt', 'uc')):
            groups[k] = 'gen'
        else:
            groups[k] = 'other'
    return groups


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
    except (TypeError, ValueError):
        return None


def decode_user_comment(raw):
    """EXIF の UserComment（先頭8バイトが文字コード）を文字列にする"""
    if isinstance(raw, str):
        return raw.strip('\x00')
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
    items = [(ExifTags.TAGS.get(t, hex(t)), t, v) for t, v in exif.items() if t not in (EXIF_IFD, GPS_IFD)]
    items += [(ExifTags.TAGS.get(t, hex(t)), t, v) for t, v in exif.get_ifd(EXIF_IFD).items()]
    items += [(f'GPS {ExifTags.GPSTAGS.get(t, hex(t))}', t, v) for t, v in exif.get_ifd(GPS_IFD).items()]
    if exif.get_ifd(GPS_IFD):
        meta.notes.append('EXIF に位置情報（GPS）が含まれています')
    for name, tag, value in items:
        text = _exif_value(tag, value)
        gen = parse_a1111(text) if tag in (USER_COMMENT, IMAGE_DESCRIPTION) else None
        if gen is not None and meta.gen is None:
            meta.gen = gen
            meta.exif_has_gen = True
            meta.entries.append(Entry('gen', f'EXIF {name}', text))
        else:
            meta.entries.append(Entry('exif', name, text))


def _read_xmp(xmp, meta):
    """XMP の中から、AI生成の印（IPTC の DigitalSourceType）を探す"""
    for kind in AI_SOURCE_TYPES:
        if re.search(rf'digitalsourcetype/{kind}\b', xmp, re.I):
            meta.notes.append(f'XMP に AI 生成の印があります（{kind}）')
            return


def xmp_fields(xmp):
    """XMP の値を (名前, 値) の並びにする（表示用）。読めなければ空"""
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
    blocks = [body for kind, body in _iter_blocks(data, fmt) if kind == 'c2pa']
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
    for key, value in _A1111_SETTING.findall(' '.join(lines[idx:])):
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"')
        _setting(settings, key.strip(), value)
    tool = 'Forge' if settings.get('バージョン', '').startswith('f') else 'A1111 WebUI'
    return GenInfo(tool, prompt, negative, settings)


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
    nodes = {k: v for k, v in graph.items() if isinstance(v, dict) and 'inputs' in v}

    def text_of(link, depth=0):
        if not isinstance(link, list) or depth > 8 or str(link[0]) not in nodes:
            return ''
        inputs = nodes[str(link[0])]['inputs']
        for key in ('text', 'text_g', 'prompt', 'positive'):
            if isinstance(inputs.get(key), str):
                return inputs[key]
            if isinstance(inputs.get(key), list):
                return text_of(inputs[key], depth + 1)
        # 条件の合成などは、最初につながっている入力をたどる
        return next((t for v in inputs.values() if (t := text_of(v, depth + 1))), '')

    sampler = next((n for n in nodes.values() if 'sampler' in n.get('class_type', '').lower()
                    and 'positive' in n['inputs']), None)
    if sampler:
        inp = sampler['inputs']
        gen.prompt = text_of(inp.get('positive'))
        gen.negative = text_of(inp.get('negative'))
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
        if 'latent' in n.get('class_type', '').lower() and isinstance(inp.get('width'), int):
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
    Path(dst).write_bytes(strip(data, remove))


def _exif_without_gen(exif_bytes):
    """生成情報の入った UserComment / ImageDescription だけを除いた EXIF（先頭の Exif\\0\\0 は付けない）"""
    exif = Image.Exif()
    exif.load(exif_bytes if exif_bytes.startswith(b'Exif') else b'Exif\x00\x00' + exif_bytes)
    if IMAGE_DESCRIPTION in exif and parse_a1111(_exif_value(IMAGE_DESCRIPTION, exif[IMAGE_DESCRIPTION])):
        del exif[IMAGE_DESCRIPTION]
    ifd = exif.get_ifd(EXIF_IFD)
    if USER_COMMENT in ifd:
        del ifd[USER_COMMENT]
    out = exif.tobytes()
    return out[6:] if out.startswith(b'Exif\x00\x00') else out


def _exif_action(remove, meta):
    """EXIF をどうするか：'drop'（丸ごと消す）/ 'clean'（生成情報だけ消す）/ 'keep'"""
    if 'exif' in remove:
        return 'drop'
    return 'clean' if 'gen' in remove and meta.exif_has_gen else 'keep'


def _iter_blocks(data, fmt):
    """ファイルを (種類, 中身) の並びに分ける。種類は消去の単位で、None は常に残すもの"""
    if fmt == 'PNG':
        for ctype, body in _png_chunks(data):
            yield {b'caBX': 'c2pa', b'eXIf': 'exif'}.get(ctype), body
    elif fmt == 'JPEG':
        for marker, body in _jpeg_segments(data):
            yield _jpeg_kind(marker, body), body
    elif fmt == 'WEBP':
        for fourcc, body in _webp_chunks(data):
            yield {b'C2PA': 'c2pa', b'EXIF': 'exif', b'XMP ': 'xmp'}.get(fourcc), body


# ---- PNG

def _png_chunks(data):
    pos = 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack('>I4s', data[pos:pos + 8])
        yield ctype, data[pos + 8:pos + 8 + length]
        pos += 12 + length


def _png_chunk(ctype, body):
    return struct.pack('>I', len(body)) + ctype + body + struct.pack('>I', zlib.crc32(ctype + body) & 0xFFFFFFFF)


def _strip_png(data, remove, meta):
    out = [data[:8]]
    action = _exif_action(remove, meta)
    for ctype, body in _png_chunks(data):
        if ctype in (b'tEXt', b'zTXt', b'iTXt'):
            key = body.split(b'\x00', 1)[0].decode('latin-1')
            if meta.text_groups.get(key, 'other') in remove:
                continue
        elif ctype == b'caBX' and 'c2pa' in remove:
            continue
        elif ctype == b'eXIf':
            if action == 'drop':
                continue
            if action == 'clean':
                out.append(_png_chunk(ctype, _exif_without_gen(body)))
                continue
        out.append(_png_chunk(ctype, body))
    return b''.join(out)


# ---- JPEG

_SOS, _EOI = 0xDA, 0xD9
_STANDALONE = {0x01, *range(0xD0, 0xD8)}   # 長さを持たないマーカー（TEM・RST）


def _jpeg_segments(data):
    """SOS より前のセグメントを (マーカー, 中身) で返す。SOS 以降は (SOS, 残り全部)"""
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ValueError('JPEG のセグメントを読めません')
        marker = data[pos + 1]
        if marker == 0xFF:   # 詰め物
            pos += 1
            continue
        if marker in _STANDALONE:
            yield marker, b''
            pos += 2
            continue
        if marker in (_SOS, _EOI):
            yield marker, data[pos + 2:]
            return
        length = struct.unpack('>H', data[pos + 2:pos + 4])[0]
        yield marker, data[pos + 4:pos + 2 + length]
        pos += 2 + length


def _jpeg_kind(marker, body):
    if marker == 0xE1 and body.startswith(b'Exif\x00'):
        return 'exif'
    if marker == 0xE1 and body.startswith(b'http://ns.adobe.com/'):
        return 'xmp'
    if marker == 0xEB and (b'jumb' in body[:64] or b'c2pa' in body[:64]):
        return 'c2pa'
    if marker == 0xED or marker == 0xFE:   # Photoshop / IPTC、コメント
        return 'other'
    return None


def _jpeg_segment(marker, body):
    return bytes([0xFF, marker]) + struct.pack('>H', len(body) + 2) + body


def _strip_jpeg(data, remove, meta):
    out = [data[:2]]
    action = _exif_action(remove, meta)
    for marker, body in _jpeg_segments(data):
        if marker in (_SOS, _EOI):
            out.append(bytes([0xFF, marker]) + body)
            break
        if marker in _STANDALONE:
            out.append(bytes([0xFF, marker]))
            continue
        kind = _jpeg_kind(marker, body)
        if kind == 'exif' and action != 'drop':
            body = b'Exif\x00\x00' + _exif_without_gen(body) if action == 'clean' else body
        elif kind in remove:
            continue
        out.append(_jpeg_segment(marker, body))
    return b''.join(out)


# ---- WebP

_VP8X_EXIF, _VP8X_XMP = 0x08, 0x04


def _webp_chunks(data):
    pos = 12
    while pos + 8 <= len(data):
        fourcc, size = struct.unpack('<4sI', data[pos:pos + 8])
        yield fourcc, data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)


def _webp_chunk(fourcc, body):
    return fourcc + struct.pack('<I', len(body)) + body + (b'\x00' if len(body) & 1 else b'')


def _strip_webp(data, remove, meta):
    action = _exif_action(remove, meta)
    chunks = []
    for fourcc, body in _webp_chunks(data):
        kind = {b'C2PA': 'c2pa', b'EXIF': 'exif', b'XMP ': 'xmp'}.get(fourcc)
        if kind == 'exif' and action != 'drop':
            body = _exif_without_gen(body) if action == 'clean' else body
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
    return b'RIFF' + struct.pack('<I', len(payload)) + payload
