"""消したはずの情報が残らないか、細工されたファイルで固まらないか、画像やファイルを壊さないか"""
import io
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms, PngImagePlugin

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from cleaner import denoise as dn, metadata as md  # noqa: E402
import test_metadata as tm  # noqa: E402

ALL = set(md.GROUPS)
SECRET = b'SECRET-PROMPT-TEXT'


def _before_iend(data, chunk):
    iend = data.rfind(b'IEND') - 4
    return data[:iend] + chunk + data[iend:]


def _pixels(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert('RGBA'))


def _stealth_rgba(size=64):
    alpha = np.full((size, size), 255, np.uint8)
    bits = np.unpackbits(np.frombuffer(md.STEALTH_MAGICS[1] + SECRET, np.uint8))
    column = alpha.T.ravel()
    column[:len(bits)] = 254 | bits
    alpha = column.reshape(size, size).T
    return np.dstack([np.full((size, size, 3), 120, np.uint8), alpha])


# ---------------------------------------------------------------- 消し残し

def test_png_private_chunk_and_trailer_removed():
    data = _before_iend(tm._png(), md._png_chunk(b'prVt', SECRET)) + b'\x00\x00\xff\xff' + SECRET
    meta = md.read_bytes(data)
    assert 'other' in meta.groups()
    out = md.strip(data, {'other'})
    assert SECRET not in out
    assert np.array_equal(_pixels(out), _pixels(data))


def _jpeg_with(segment):
    data = tm._jpeg()
    return data[:2] + segment + data[2:]


@pytest.mark.parametrize('segment', [md._jpeg_segment(0xE5, SECRET), md._jpeg_segment(0xE1, b'unknown' + SECRET)])
def test_jpeg_unknown_app_removed(segment):
    out = md.strip(_jpeg_with(segment), {'other'})
    assert SECRET not in out


def test_jpeg_after_eoi_removed():
    out = md.strip(tm._jpeg() + SECRET, {'other'})
    assert SECRET not in out
    Image.open(io.BytesIO(out)).load()


def test_progressive_jpeg_between_scans_removed():
    buf = io.BytesIO()
    Image.fromarray(tm._pixels()).save(buf, 'JPEG', progressive=True)
    data = buf.getvalue()
    second = data.find(b'\xff\xda', data.find(b'\xff\xda') + 2)
    data = data[:second] + md._jpeg_segment(0xFE, SECRET) + data[second:]
    out = md.strip(data, ALL)
    assert SECRET not in out
    assert np.array_equal(_pixels(out), _pixels(data))


def test_extended_xmp_only_is_listed():
    plain = io.BytesIO()
    Image.fromarray(tm._pixels()).save(plain, 'JPEG')
    data = plain.getvalue()[:2] + md._jpeg_segment(0xE1, b'http://ns.adobe.com/xmp/extension/\x00' + SECRET) \
        + plain.getvalue()[2:]
    assert 'xmp' in md.read_bytes(data).groups()
    assert SECRET not in md.strip(data, set(md.read_bytes(data).groups()))


def test_webp_unknown_chunk_and_unflagged_xmp():
    buf = io.BytesIO()
    Image.fromarray(tm._pixels()).save(buf, 'WEBP', lossless=True)
    data = buf.getvalue()
    payload = data[8:] + md._webp_chunk(b'XMP ', SECRET) + md._webp_chunk(b'priv', SECRET)
    data = b'RIFF' + struct.pack('<I', len(payload)) + payload
    groups = set(md.read_bytes(data).groups())
    assert {'xmp', 'other'} <= groups
    assert SECRET not in md.strip(data, groups)


def test_duplicate_comment_with_json_is_generation():
    info = PngImagePlugin.PngInfo()
    info.add_text('Comment', 'plain')
    info.add_text('Comment', json.dumps({'prompt': SECRET.decode(), 'uc': ''}))
    buf = io.BytesIO()
    Image.fromarray(tm._pixels()).save(buf, 'PNG', pnginfo=info)
    assert md.read_bytes(buf.getvalue()).gen.prompt == SECRET.decode()
    assert SECRET not in md.strip(buf.getvalue(), {'gen'})


@pytest.mark.parametrize('tag, text', [(md.MAKE, 'workflow:{"a": "SECRET-PROMPT-TEXT"}'),
                                       (md.MODEL, 'prompt:{"1": {"inputs": {"text": "SECRET-PROMPT-TEXT"}}}')])
def test_comfy_in_exif_is_generation(tag, text):
    exif = Image.Exif()
    exif[tag] = text
    buf = io.BytesIO()
    Image.fromarray(tm._pixels()).save(buf, 'WEBP', exif=exif, lossless=True)
    assert md.read_bytes(buf.getvalue()).exif_has_gen
    assert SECRET not in md.strip(buf.getvalue(), {'gen'})


@pytest.mark.parametrize('graph', [{'1': {'class_type': 'KSampler', 'inputs': {'positive': []}}},
                                   {'1': {'class_type': 1, 'inputs': {'text': 'x'}}},
                                   {'1': {'class_type': 'KSampler', 'inputs': 'broken'}}])
def test_odd_comfy_graph_is_not_reported_as_none(graph):
    data = tm._png({'prompt': json.dumps(graph)})
    meta = md.read_bytes(data)
    assert 'gen' in meta.groups() and meta.gen is not None
    assert md.read_bytes(md.strip(data, {'gen'})).gen is None


def test_stealth_alpha_detected_and_removed_png():
    buf = io.BytesIO()
    Image.fromarray(_stealth_rgba()).save(buf, 'PNG')
    meta = md.read_bytes(buf.getvalue())
    assert meta.stealth and 'gen' in meta.groups()
    out = md.strip(buf.getvalue(), {'gen'})
    after = _pixels(out)
    assert not md.has_stealth(after[..., 3])
    assert after[..., 3].min() == 255                      # 不透明はそのまま
    assert np.array_equal(after[..., :3], _stealth_rgba()[..., :3])


def test_stealth_alpha_removed_when_saving_processed(tmp_path):
    src = tmp_path / 'a.png'
    Image.fromarray(_stealth_rgba()).save(src)
    meta, rgb, alpha = dn.load_image(src)
    for name, save in (('x.png', lambda p: dn.save_clean(rgb, alpha, meta, p, {'gen'})),
                       ('y.png', lambda p: dn.save_png(rgb, alpha, meta, p, strip_meta=True))):
        save(tmp_path / name)
        assert not md.has_stealth(_pixels((tmp_path / name).read_bytes())[..., 3])


def test_failed_strip_leaves_no_file(tmp_path, monkeypatch):
    src = tmp_path / 'a.png'
    src.write_bytes(tm._png({'parameters': SECRET.decode()}))
    meta, rgb, alpha = dn.load_image(src)
    def boom(*_):
        raise RuntimeError
    monkeypatch.setattr(dn, 'strip', boom)
    dst = tmp_path / 'out.png'
    with pytest.raises(RuntimeError):
        dn.save_clean(rgb, alpha, meta, dst, {'gen'})
    assert not dst.exists()


# ---------------------------------------------------------------- 固まらない

def test_comfy_branching_graph_is_fast():
    inputs = {f'k{i}': ['1', 0] for i in range(20)}
    graph = {'1': {'class_type': 'X', 'inputs': inputs},
             '2': {'class_type': 'KSampler', 'inputs': {'positive': ['1', 0]}}}
    start = time.perf_counter()
    md.read_bytes(tm._png({'prompt': json.dumps(graph)}))
    assert time.perf_counter() - start < 1


def test_a1111_long_settings_is_fast():
    start = time.perf_counter()
    md.parse_a1111('p\nSteps: 1,' + 'a ' * 200_000)
    assert time.perf_counter() - start < 1


def test_a1111_settings_parsing():
    gen = md.parse_a1111('p\nSteps: 20, junk, Sampler: Euler a, Lora hashes: "a: 1, b: 2", Seed: 5')
    assert gen.settings['ステップ数'] == '20' and gen.settings['サンプラー'] == 'Euler a'
    assert gen.settings['Lora hashes'] == 'a: 1, b: 2' and gen.settings['シード値'] == '5'


def test_total_text_is_capped(monkeypatch):
    monkeypatch.setattr(md, 'MAX_TOTAL_TEXT', 2 * 2 ** 20)
    bomb = zlib.compress(b'a' * 2 ** 20, 9)
    data = tm._png()
    for i in range(5):
        data = _before_iend(data, md._png_chunk(b'zTXt', f'k{i}'.encode() + b'\x00\x00' + bomb))
    items = md.png_text_items(data)
    assert len(items) == 5                                  # 上限を超えた分も、消す対象としては見える
    assert sum(len(v) for _, v in items) < 3 * 2 ** 20


def test_deeply_nested_json_does_not_raise():
    deep = '[' * 200_000
    assert md._load_json(deep) is None


# ---------------------------------------------------------------- 画像を壊さない

def _rotated_jpeg(path):
    exif = Image.Exif()
    exif[md.ORIENTATION] = 6
    exif[0x013B] = 'Author'
    Image.new('RGB', (80, 40), 'red').save(path, exif=exif)


def test_exif_orientation_is_applied(tmp_path):
    path = tmp_path / 'r.jpg'
    _rotated_jpeg(path)
    _, rgb, _ = dn.load_image(path)
    assert rgb.shape[:2] == (80, 40)


def test_strip_exif_keeps_orientation_only(tmp_path):
    path = tmp_path / 'r.jpg'
    _rotated_jpeg(path)
    out = md.strip(path.read_bytes(), ALL)
    exif = Image.open(io.BytesIO(out)).getexif()
    assert dict(exif) == {md.ORIENTATION: 6}
    assert md.read_bytes(out).groups() == {}


@pytest.mark.parametrize('mode', ['P', 'RGB'])
def test_trns_transparency_is_kept(tmp_path, mode):
    im = Image.new('RGB', (32, 32), (255, 0, 255))
    im.paste((0, 200, 0), (8, 8, 24, 24))
    path = tmp_path / 't.png'
    if mode == 'P':
        pal = im.convert('P', palette=Image.ADAPTIVE)
        pal.save(path, transparency=pal.getpixel((0, 0)))
    else:
        im.save(path, transparency=(255, 0, 255))
    _, _, alpha = dn.load_image(path)
    assert alpha is not None and alpha[0, 0] == 0 and alpha[16, 16] == 255


def test_cmyk_icc_is_not_copied_to_rgb(tmp_path):
    icc = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
    icc[16:20] = b'CMYK'
    src = tmp_path / 'c.jpg'
    Image.new('CMYK', (32, 32), (10, 20, 30, 0)).save(src, icc_profile=bytes(icc))
    meta, rgb, alpha = dn.load_image(src)
    dst = tmp_path / 'c.png'
    dn.save_png(rgb, alpha, meta, dst, strip_meta=True)
    assert 'icc_profile' not in Image.open(dst).info


def test_failed_write_keeps_existing_file(tmp_path, monkeypatch):
    path = tmp_path / 'keep.png'
    path.write_bytes(b'original')
    def fail(*_):
        raise OSError('disk full')
    monkeypatch.setattr(md.os, 'replace', fail)
    with pytest.raises(OSError):
        md.write_atomic(path, b'new')
    assert path.read_bytes() == b'original'
    assert [p.name for p in tmp_path.iterdir()] == ['keep.png']   # 一時ファイルも残らない


def test_save_png_to_other_extension_is_still_png(tmp_path):
    dst = tmp_path / 'x.jpg'
    dn.save_png(np.zeros((32, 32, 3), np.uint8), None, {}, dst)
    assert Image.open(dst).format == 'PNG'


def test_batch_does_not_overwrite_or_reprocess(tmp_path):
    from gui.dialogs import batch_output, batch_sources
    for name in ('a.png', 'a_clean.png', 'mine_clean.png'):
        Image.new('RGB', (32, 32)).save(tmp_path / name)
    assert [p.name for p in batch_sources(tmp_path)] == ['a.png', 'mine_clean.png']   # 元の無いものは対象のまま
    assert batch_output(tmp_path, tmp_path / 'a.png').name == 'a_clean (2).png'


def test_save_dialog_does_not_carry_over_to_new_groups(tmp_path):
    from PySide6.QtWidgets import QApplication
    from gui.dialogs import SaveDialog
    QApplication.instance() or QApplication([])
    meta = md.read_bytes(tm._png({'parameters': tm.A1111, 'Title': 't'}))
    dlg = SaveDialog(None, meta, True, True, frozenset({'other'}))   # 前回「その他」だけ残すと選んだ
    assert dlg.selected() == {'gen'}


def test_save_dialog_names_the_real_format():
    from PySide6.QtWidgets import QApplication
    from gui.dialogs import SaveDialog
    QApplication.instance() or QApplication([])
    buf = io.BytesIO()
    Image.fromarray(tm._pixels()).save(buf, 'GIF')
    assert 'PNG' in SaveDialog(None, md.read_bytes(buf.getvalue()), True).original.text()
    assert 'PNG形式のまま' in SaveDialog(None, md.read_bytes(tm._png()), True).original.text()
