"""壊れた画像・極端な大きさの画像・細工されたファイルを読み込んでも、想定外の動きをしないか"""
import io
import json
import struct
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from cleaner import metadata as md  # noqa: E402
from cleaner.common import psnr, ssim  # noqa: E402
from cleaner.cutout import cutout  # noqa: E402
from cleaner.denoise import MAX_PIXELS, MIN_SIDE, blend, line_mask, load_image, smooth  # noqa: E402
import test_metadata as tm  # noqa: E402

# 画像として読めないときに Pillow が出す例外（GUI ではまとめて「開けませんでした」と表示する）
IMAGE_ERRORS = (OSError, SyntaxError, ValueError, Image.DecompressionBombError)


def _save(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _png_header_only(w, h):
    """IHDR だけの PNG（画素データは無い）。大きさの確認が、画素を読む前に働くかを見る"""
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + md._png_chunk(b'IHDR', ihdr) + md._png_chunk(b'IEND', b'')


# ---------------------------------------------------------------- 画像の読み込み

@pytest.mark.parametrize('data', [b'', b'not an image', bytes(range(256)) * 4])
def test_load_rejects_non_images(tmp_path, data):
    with pytest.raises(IMAGE_ERRORS):
        load_image(_save(tmp_path, 'x.png', data))


@pytest.mark.parametrize('make', [tm._full_png, tm._jpeg])
def test_load_truncated(tmp_path, make):
    data = make()
    with pytest.raises(IMAGE_ERRORS):
        load_image(_save(tmp_path, 'x.img', data[:len(data) // 2]))


def test_load_rejects_huge_before_decoding(tmp_path):
    side = int(MAX_PIXELS ** 0.5) + 100
    path = _save(tmp_path, 'huge.png', _png_header_only(side, side))
    start = time.perf_counter()
    with pytest.raises(ValueError, match='大きすぎます'):
        load_image(path)
    assert time.perf_counter() - start < 1   # 画素を展開していない


def test_load_rejects_decompression_bomb(tmp_path):
    with pytest.raises(IMAGE_ERRORS):
        load_image(_save(tmp_path, 'bomb.png', _png_header_only(50000, 50000)))


@pytest.mark.parametrize('size', [(1, 1), (MIN_SIDE - 1, 400)])
def test_load_rejects_tiny(tmp_path, size):
    path = tmp_path / 'tiny.png'
    Image.new('RGB', size).save(path)
    with pytest.raises(ValueError, match='小さすぎます'):
        load_image(path)


def test_load_16bit_gray_keeps_tones(tmp_path):
    ramp = np.tile(np.linspace(0, 65535, 64).astype(np.uint16), (32, 1))
    path = tmp_path / 'g16.png'
    Image.fromarray(ramp).save(path)   # uint16 の配列は 16bit のグレーになる
    _, rgb, alpha = load_image(path)
    assert alpha is None
    assert rgb[0, 0, 0] == 0 and rgb[0, -1, 0] == 255   # 白く飛ばずに階調が残る
    assert 100 < rgb.mean() < 155


@pytest.mark.parametrize('mode', ['P', 'LA', 'L', 'CMYK', 'RGBA'])
def test_load_various_modes(tmp_path, mode):
    im = Image.fromarray(tm._pixels()).convert(mode)
    path = tmp_path / ('x.jpg' if mode == 'CMYK' else 'x.png')
    im.save(path)
    _, rgb, alpha = load_image(path)
    assert rgb.shape == (24, 32, 3) and rgb.dtype == np.uint8
    assert (alpha is not None) == (mode in ('LA', 'RGBA'))


def test_load_animated_gif_uses_first_frame(tmp_path):
    frames = [Image.new('RGB', (32, 32), c) for c in ('red', 'blue')]
    path = tmp_path / 'anim.gif'
    frames[0].save(path, save_all=True, append_images=frames[1:])
    _, rgb, _ = load_image(path)
    assert tuple(rgb[0, 0]) == (255, 0, 0)


# ---------------------------------------------------------------- 処理：極端な形の画像

@pytest.mark.parametrize('shape', [(MIN_SIDE, MIN_SIDE), (MIN_SIDE, 600), (600, MIN_SIDE)])
def test_processing_extreme_shapes(shape):
    rgb = np.random.default_rng(1).integers(0, 255, (*shape, 3), dtype=np.uint8)
    out = blend(rgb, smooth(rgb), line_mask(rgb))
    assert out.shape == rgb.shape
    assert np.isfinite(ssim(rgb, out))
    assert psnr(rgb, rgb) is None
    cut, alpha, _ = cutout(out, None)
    assert alpha.shape == shape


def test_ssim_smaller_than_window():
    a = np.zeros((4, 4, 3), np.uint8)
    assert ssim(a, a) == pytest.approx(1.0)


# ---------------------------------------------------------------- メタデータ：壊れたファイル・細工されたファイル

@pytest.mark.filterwarnings('ignore::UserWarning')   # 壊れた EXIF への Pillow の警告
@pytest.mark.parametrize('make', [tm._full_png, tm._jpeg, tm._webp])
def test_metadata_truncated_never_crashes(make):
    """途中で切れたファイルでも、画像として開ける限りメタデータの読み取りで例外にならない"""
    data = make()
    for cut in range(16, len(data), max(1, len(data) // 60)):
        try:
            md.read_bytes(data[:cut])
        except IMAGE_ERRORS:
            pass   # 画像として開けない（GUI では「開けませんでした」）


@pytest.mark.filterwarnings('ignore::UserWarning')   # 壊れた EXIF への Pillow の警告
@pytest.mark.parametrize('make', [tm._full_png, tm._jpeg, tm._webp])
def test_metadata_fuzz(make):
    """ランダムにバイトを書き換えても、読み取りは決まった例外か成功のどちらかで、すぐ終わる"""
    data = bytearray(make())
    rng = np.random.default_rng(7)
    start = time.perf_counter()
    for _ in range(300):
        broken = bytearray(data)
        for pos in rng.integers(0, len(broken), 8):
            broken[pos] = int(rng.integers(0, 256))
        try:
            md.read_bytes(bytes(broken))
        except IMAGE_ERRORS:
            pass
    assert time.perf_counter() - start < 30


def test_metadata_keeps_what_it_can_read():
    """後ろが壊れていても、前にある生成情報は読める"""
    data = tm._png({'parameters': tm.A1111})
    iend = data.rfind(b'IEND') - 4
    broken = data[:iend] + struct.pack('>I', 10 ** 6) + b'tEXt' + b'cut'
    assert md.read_bytes(broken).gen.prompt == '1girl, solo, smile'


def test_compressed_text_bomb_is_capped():
    """展開すると巨大になる圧縮テキストは、上限で止める"""
    bomb = zlib.compress(b'a' * (64 * 2 ** 20), 9)   # 64MB に膨らむ
    data = tm._png()
    iend = data.rfind(b'IEND') - 4
    data = data[:iend] + md._png_chunk(b'zTXt', b'parameters\x00\x00' + bomb) + data[iend:]
    start = time.perf_counter()
    texts = md.png_texts(data)
    assert len(texts['parameters']) <= md.MAX_TEXT_BYTES
    assert time.perf_counter() - start < 5


def test_huge_xmp_is_not_parsed():
    assert md.xmp_fields('<x>' + 'a' * (md.MAX_XMP_PARSE + 1) + '</x>') == []


def test_xmp_entity_expansion_is_safe():
    """実体参照を何重にも展開させる XMP（billion laughs）でも、固まったり膨らんだりしない"""
    lol = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
           + ''.join(f'<!ENTITY {chr(98 + i)} "{("&" + chr(97 + i) + ";") * 10}">' for i in range(8))
           + ']><x>&i;</x>')
    start = time.perf_counter()
    md.xmp_fields(lol)
    assert time.perf_counter() - start < 5


def test_comfy_cyclic_graph():
    graph = {'1': {'class_type': 'KSampler', 'inputs': {'positive': ['2', 0], 'negative': ['2', 0], 'seed': 1}},
             '2': {'class_type': 'ConditioningCombine', 'inputs': {'a': ['3', 0]}},
             '3': {'class_type': 'ConditioningCombine', 'inputs': {'a': ['2', 0]}}}
    gen = md.read_bytes(tm._png({'prompt': json.dumps(graph)})).gen
    assert gen.tool == 'ComfyUI' and gen.prompt == ''


def test_strip_refuses_broken_file():
    """壊れたファイルは、半端な結果を書かずにエラーにする"""
    data = tm._full_png()
    iend = data.rfind(b'IEND') - 4
    broken = data[:iend] + struct.pack('>I', 10 ** 6) + b'tEXt' + b'cut'
    with pytest.raises(md.BrokenFile):
        md.strip(broken, {'gen'})


def test_png_text_chunk_with_odd_values():
    info = PngImagePlugin.PngInfo()
    info.add_text('parameters', 'Steps: abc')   # 設定の行だけで、値も変
    info.add_text('Comment', '{not json')
    buf = io.BytesIO()
    Image.fromarray(tm._pixels()).save(buf, 'PNG', pnginfo=info)
    meta = md.read_bytes(buf.getvalue())
    assert meta.gen is not None and meta.gen.prompt == ''
