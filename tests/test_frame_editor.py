"""Frame replacements must affect exact indices and preserve clip timing."""
from io import BytesIO
import json
import shutil
import subprocess

import pytest

from conftest import PRODUCT, make_product, needs_ffmpeg
from reelfactory import frame_editor as frames, cli
from reelfactory.config import Product, read_yaml

BASE = '/products/test-rack/scenes/frames'
pytestmark = needs_ffmpeg


@pytest.fixture
def source(project):
    path = project / 'products/test-rack/photos/clip.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=lime:s=160x240:r=10:d=1',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)], check=True, capture_output=True)
    return path


def png(color, size='160x240'):
    return subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s={size}',
                           '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'],
                          check=True, capture_output=True).stdout


def payload(client, **changes):
    metadata = client.get(BASE, query_string={'source_photo': 'clip.mp4'}).json
    return dict(source_photo='clip.mp4', version=metadata['version'], frames='[0,5,9]',
                frame_0=(BytesIO(png('blue')), '0.png'), frame_5=(BytesIO(png('red')), '5.png'),
                frame_9=(BytesIO(png('yellow')), '9.png'), **changes)


def channels(path):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', 'scale=1:1',
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], check=True, capture_output=True).stdout
    return [tuple(raw[offset:offset + 3]) for offset in range(0, len(raw), 3)]


def test_exact_first_middle_last_frames_and_audio_are_saved_as_new_media(client, project, source, monkeypatch):
    before = source.read_bytes()
    metadata = client.get(BASE, query_string={'source_photo': 'clip.mp4'})
    assert metadata.status_code == 200 and metadata.json['frame_count'] == 10
    assert metadata.json['width'] == 160 and metadata.json['height'] == 240
    original_frame = client.get(BASE + '/5?source_photo=clip.mp4')
    assert original_frame.status_code == 200 and original_frame.mimetype == 'image/png'
    result = client.post(BASE, data=payload(client))
    assert result.status_code == 201, result.json
    output = project / 'products/test-rack/photos' / result.json['name']
    values = channels(output)
    assert len(values) == 10
    for i, (red, green, blue) in enumerate(values):
        if i == 0:
            assert blue > 180 and red < 40 and green < 40, (i, values)
        elif i == 5:
            assert red > 180 and green < 40 and blue < 40, (i, values)
        elif i == 9:
            assert red > 180 and green > 180 and blue < 40, (i, values)
        else:
            assert green > 180 and red < 40 and blue < 40, (i, values)
    probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                      '-of', 'json', str(output)], check=True, capture_output=True).stdout)
    assert [s['codec_type'] for s in probe['streams']] == ['video', 'audio']
    assert float(probe['format']['duration']) == pytest.approx(1, abs=.1)
    assert source.read_bytes() == before
    assert client.get(result.json['url']).status_code == 200
    product = Product.load(source.parent.parent)
    assert result.json['name'] not in [p.name for p in cli._shot_photos(product, 6)]
    # Explicit choice survives save/load and reaches the ordinary build pipeline.
    data = dict(lang='en', tts='silent', save_name='Edited frames', seg_role_en='hook',
                seg_vo_en='Keep these words.', seg_overlay_en='Caption', seg_photo_en=result.json['name'])
    assert client.post('/products/test-rack/script/save', data=data).status_code == 200
    from reelfactory.web.app import _load_saved_scripts
    assert _load_saved_scripts(project / 'products', 'test-rack')['en'][0]['segments'][0]['photo'] == result.json['name']
    calls = []
    monkeypatch.setattr(cli, 'build_one', lambda *a, **kw: calls.append(kw) or [])
    assert client.post('/products/test-rack/build', data=data).status_code == 200
    assert calls[0]['photo_names'] == [result.json['name']]
    assert calls[0]['segments'][0].vo == 'Keep these words.'


@pytest.mark.parametrize('changes', [dict(frames='[-1]'), dict(frames='[10]'), dict(frames='[true]'),
    dict(frames='[0,0]'), dict(frames='{}'), dict(frames='oops'), dict(version='old'),
    dict(frame_5=(BytesIO(b'bad'), '5.png')), dict(source_photo='../brand.yaml')])
def test_invalid_edits_leave_original_and_library_unchanged(client, source, changes):
    before = set(source.parent.iterdir())
    result = client.post(BASE, data=dict(payload(client), **changes))
    assert result.status_code == 400, result.json
    assert set(source.parent.iterdir()) == before


def test_replacement_size_must_match_and_failed_encode_can_retry(client, source, monkeypatch):
    result = client.post(BASE, data=dict(payload(client), frame_5=(BytesIO(png('red', '32x32')), '5.png')))
    assert result.status_code == 400 and 'dimensions' in result.json['error']
    original = frames.replace_frames
    def fail(*args):
        from reelfactory.render import RenderError
        raise RenderError('Could not save clip.')
    monkeypatch.setattr(frames, 'replace_frames', fail)
    assert client.post(BASE, data=payload(client)).status_code == 400
    assert not list(source.parent.glob('frames-*.mp4'))
    monkeypatch.setattr(frames, 'replace_frames', original)
    assert client.post(BASE, data=payload(client)).status_code == 201


def test_frame_access_is_scoped_and_invalid_frames_are_rejected(client, source, project, photos):
    make_product(project, 'other', PRODUCT, photos)
    assert client.get('/products/other/scenes/frames?source_photo=clip.mp4').status_code == 400
    assert client.get(BASE + '?source_photo=../brand.yaml').status_code == 400
    assert client.get(BASE + '?source_photo=1.jpg').status_code == 400
    assert client.get(BASE + '/10?source_photo=clip.mp4').status_code == 400


def test_collection_frame_edit_stays_with_source_product(client, project, photos, source):
    make_product(project, 'chair', dict(PRODUCT, name_en='Chair'), photos)
    shutil.copy(source, project / 'products/chair/photos/chair.mp4')
    response = client.post('/collections/new', data={'products': ['test-rack', 'chair']})
    slug = response.location.split('/')[-2]
    folder = project / 'products' / slug
    product = Product.load(folder)
    member = next(m for m in product.collection_members if m['slug'] == 'chair')
    name = next(name for name in member['media'].values() if name.endswith('.mp4'))
    base = f'/products/{slug}/scenes/frames'
    details = client.get(base, query_string={'source_photo': name}).json
    response = client.post(base, data=dict(source_photo=name, version=details['version'], frames='[5]',
                                          frame_5=(BytesIO(png('red')), 'frame.png')))
    assert response.status_code == 201, response.json
    assert response.json['member_slug'] == 'chair'
    members = read_yaml(folder / 'product.yaml')['collection_members']
    assert response.json['name'] in next(m for m in members if m['slug'] == 'chair')['media'].values()
    assert response.json['name'] not in next(m for m in members if m['slug'] == 'test-rack')['media'].values()
