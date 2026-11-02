"""metadata の読み取り・解析・消去のテスト（検証用の画像はその場で作る）"""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import metadata as md  # noqa: E402

A1111 = ('1girl, solo, smile\nNegative prompt: lowres, bad hands\n'
         'Steps: 28, Sampler: Euler a, CFG scale: 7, Seed: 12345, Size: 512x768, '
         'Model: animeModel_v1, Lora hashes: "foo: abc, bar: def", Version: v1.10.1')
XMP = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
       '<rdf:Description xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
       'xmlns:dc="http://purl.org/dc/elements/1.1/" '
       'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia">'
       '<dc:creator><rdf:Seq><rdf:li>someone</rdf:li></rdf:Seq></dc:creator>'
       '</rdf:Description></rdf:RDF></x:xmpmeta>')
C2PA_BODY = b'\x00\x00\x00\x40jumbc2paclaim_generator\x6cTestTool/1.0' + bytes(8)
ALL = set(md.GROUPS)


def _pixels():
    return np.random.default_rng(0).integers(0, 255, (24, 32, 3), dtype=np.uint8)


def _exif(user_comment=None):
    exif = Image.Exif()
    exif[0x0131] = 'TestSoftware'   # Software
    exif[0x013B] = 'Author'         # Artist
    if user_comment is not None:
        exif.get_ifd(md.EXIF_IFD)[md.USER_COMMENT] = b'UNICODE\x00' + user_comment.encode('utf-16-be')
    exif.get_ifd(md.GPS_IFD)[2] = (35.0, 39.0, 0.0)   # GPSLatitude
    return exif


def _png(texts=None, exif=None, xmp=None, c2pa=False):
    info = PngImagePlugin.PngInfo()
    for k, v in (texts or {}).items():
        info.add_text(k, v)
    if xmp:
        info.add_itxt(md.PNG_XMP_KEY, xmp)
    buf = io.BytesIO()
    Image.fromarray(_pixels()).save(buf, 'PNG', pnginfo=info, exif=exif)
    data = buf.getvalue()
    if c2pa:   # IEND の直前に差し込む
        iend = data.rfind(b'IEND') - 4
        data = data[:iend] + md._png_chunk(b'caBX', C2PA_BODY) + data[iend:]
    return data


def _jpeg():
    buf = io.BytesIO()
    Image.fromarray(_pixels()).save(buf, 'JPEG', exif=_exif(A1111), xmp=XMP.encode(), comment=b'hello',
                                    quality=90)
    data = buf.getvalue()
    app11 = md._jpeg_segment(0xEB, b'JP\x00\x00\x00\x00\x00\x01' + C2PA_BODY)
    return data[:2] + app11 + data[2:]


def _webp():
    buf = io.BytesIO()
    Image.fromarray(_pixels()).save(buf, 'WEBP', exif=_exif(A1111), xmp=XMP.encode(), lossless=True)
    return buf.getvalue()


def _full_png():
    return _png({'parameters': A1111, 'Title': 'my title'}, exif=_exif(), xmp=XMP, c2pa=True)


def _decode(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert('RGB'))


# ---------------------------------------------------------------- 読み取り・解析

def test_png_a1111_and_all_groups():
    meta = md.read_bytes(_full_png())
    assert set(meta.groups()) == ALL
    g = meta.gen
    assert g.tool == 'A1111 WebUI'
    assert g.prompt == '1girl, solo, smile'
    assert g.negative == 'lowres, bad hands'
    assert g.settings['シード値'] == '12345'
    assert g.settings['モデル'] == 'animeModel_v1'
    assert g.settings['Lora hashes'] == 'foo: abc, bar: def'   # 引用符の中のカンマで切らない
    assert any('trainedAlgorithmicMedia' in n for n in meta.notes)
    assert any('GPS' in n for n in meta.notes)
    c2pa = next(e.value for e in meta.entries if e.group == 'c2pa')
    assert 'TestTool/1.0' in c2pa
    assert ('dc:creator', 'someone') in md.xmp_fields(XMP)


def test_comfy():
    graph = {
        '3': {'class_type': 'KSampler', 'inputs': {'seed': 42, 'steps': 20, 'cfg': 6.5, 'sampler_name': 'euler',
                                                   'positive': ['6', 0], 'negative': ['7', 0], 'model': ['4', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'model.safetensors'}},
        '5': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 832, 'height': 1216, 'batch_size': 1}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'a cat', 'clip': ['4', 1]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'blurry', 'clip': ['4', 1]}},
    }
    g = md.read_bytes(_png({'prompt': json.dumps(graph), 'workflow': '{}'})).gen
    assert (g.tool, g.prompt, g.negative) == ('ComfyUI', 'a cat', 'blurry')
    assert g.settings['シード値'] == '42'
    assert g.settings['モデル'] == 'model.safetensors'
    assert g.settings['サイズ'] == '832x1216'


def test_novelai():
    comment = json.dumps({'prompt': 'a dog', 'uc': 'ugly', 'seed': 7, 'steps': 28, 'scale': 5,
                          'width': 832, 'height': 1216})
    meta = md.read_bytes(_png({'Software': 'NovelAI', 'Description': 'a dog', 'Comment': comment,
                               'Source': 'NovelAI Diffusion V4'}))
    assert {e.group for e in meta.entries} == {'gen'}
    g = meta.gen
    assert (g.tool, g.prompt, g.negative) == ('NovelAI', 'a dog', 'ugly')
    assert g.settings['CFG'] == '5'


def test_jpeg_exif_user_comment():
    meta = md.read_bytes(_jpeg())
    assert meta.format == 'JPEG'
    assert meta.exif_has_gen
    assert meta.gen.prompt == '1girl, solo, smile'
    assert set(meta.groups()) == ALL


def test_webp():
    meta = md.read_bytes(_webp())
    assert meta.format == 'WEBP'
    assert meta.gen.settings['シード値'] == '12345'
    assert {'gen', 'exif', 'xmp'} <= set(meta.groups())


def test_no_metadata():
    meta = md.read_bytes(_png())
    assert meta.gen is None and meta.groups() == {}


# ---------------------------------------------------------------- 消去

@pytest.mark.parametrize('make', [_full_png, _jpeg, _webp])
def test_strip_all_keeps_pixels(make):
    data = make()
    out = md.strip(data, ALL)
    assert md.read_bytes(out).groups() == {}
    assert np.array_equal(_decode(out), _decode(data))   # 画素は1つも変わらない
    assert Image.open(io.BytesIO(out)).format == Image.open(io.BytesIO(data)).format


@pytest.mark.parametrize('make', [_jpeg, _webp, lambda: _png(exif=_exif(A1111))])
def test_strip_gen_only_keeps_rest_of_exif(make):
    data = make()
    out = md.strip(data, {'gen'})
    meta = md.read_bytes(out)
    assert meta.gen is None
    exif = {e.key: e.value for e in meta.entries if e.group == 'exif'}
    assert exif['Software'] == 'TestSoftware' and exif['Artist'] == 'Author'
    assert np.array_equal(_decode(out), _decode(data))


def test_strip_selected_groups_png():
    out = md.strip(_full_png(), {'c2pa', 'xmp'})
    assert set(md.read_bytes(out).groups()) == {'gen', 'exif', 'other'}


def test_strip_keeps_icc():
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    buf = io.BytesIO()
    Image.fromarray(_pixels()).save(buf, 'PNG', icc_profile=icc)
    out = md.strip(buf.getvalue(), ALL)
    assert Image.open(io.BytesIO(out)).info.get('icc_profile') == icc


def test_webp_vp8x_flags_cleared():
    out = md.strip(_webp(), {'exif', 'gen', 'xmp'})
    vp8x = dict(md._webp_chunks(out)).get(b'VP8X')
    assert vp8x is None or vp8x[0] & (md._VP8X_EXIF | md._VP8X_XMP) == 0
    _decode(out)


def test_unsupported_format():
    buf = io.BytesIO()
    Image.fromarray(_pixels()).save(buf, 'BMP')
    with pytest.raises(md.UnsupportedFormat):
        md.strip(buf.getvalue(), ALL)


# ---------------------------------------------------------------- ノイズ除去後の保存（save_png）

@pytest.mark.parametrize('strip_meta', [False, True])
def test_save_png_carries_jpeg_metadata(tmp_path, strip_meta):
    from PIL import ImageCms
    from denoise import load_image, save_png
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    src = tmp_path / 'src.jpg'
    Image.fromarray(_pixels()).save(src, 'JPEG', exif=_exif(A1111), xmp=XMP.encode(), icc_profile=icc)
    info, rgb, alpha = load_image(src)
    dst = tmp_path / 'out.png'
    save_png(rgb, alpha, info, dst, strip_meta)
    meta = md.read(dst)
    assert Image.open(dst).info.get('icc_profile') == icc   # 色の情報はどちらでも残す
    if strip_meta:
        assert meta.groups() == {}
    else:
        assert meta.gen.prompt == '1girl, solo, smile'
        assert {'gen', 'exif', 'xmp'} <= set(meta.groups())


def test_save_clean_removes_selected_groups(tmp_path):
    """処理後の保存で、選んだ種類だけを消す（保存ダイアログの「反映する」）"""
    from denoise import load_image, save_clean
    src = tmp_path / 'src.png'
    src.write_bytes(_full_png())
    info, rgb, alpha = load_image(src)
    dst = tmp_path / 'out.png'
    save_clean(rgb, alpha, info, dst, {'gen', 'xmp'})
    meta = md.read(dst)
    assert meta.gen is None
    assert set(meta.groups()) == {'exif', 'other'}   # C2PA は処理後の画像には引き継がない
    assert np.array_equal(_decode(dst.read_bytes()), rgb)
