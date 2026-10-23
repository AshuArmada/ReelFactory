"""Gemini Apps imports become local clips without calling a paid provider."""
from io import BytesIO
import json
import subprocess

import pytest

from conftest import PRODUCT, make_product
from reelfactory import ai_video as provider, cli
from reelfactory.config import Product, read_yaml
from reelfactory.render import validate_video

BASE = '/products/test-rack/scenes/ai-video'


def image_form(project, **changes):
    return dict(image=(BytesIO((project / 'products/test-rack/photos/1.jpg').read_bytes()), 'gemini.jpg'),
                request_id='gemini-import-request-1234', source_photo='1.jpg', aspect='9:16', duration='4', **changes)


@pytest.fixture(autouse=True)
def no_cloud_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Gemini Apps import must not call an API or resolve an API key')
    monkeypatch.setattr(provider, 'credentials', forbidden)
    monkeypatch.setattr(provider, 'create', forbidden)
    monkeypatch.setattr(provider.requests, 'request', forbidden)


@pytest.mark.parametrize('aspect,size', [('9:16', (720, 1280)), ('16:9', (1280, 720))])
def test_image_import_is_a_silent_playable_local_clip(client, project, app, aspect, size):
    folder = project / 'products/test-rack'
    before = (folder / 'product.yaml').read_bytes()
    response = client.post(BASE + '/gemini-import', data=dict(image_form(project), aspect=aspect))
    assert response.status_code == 201, response.json
    job = response.json
    assert job['provider'] == 'gemini' and job['status'] == 'completed'
    clip = folder / 'photos' / job['name']
    validate_video(clip)
    probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                      '-of', 'json', str(clip)], check=True, capture_output=True).stdout)
    assert len(probe['streams']) == 1
    stream = probe['streams'][0]
    assert stream['codec_type'] == 'video' and stream['codec_name'] == 'h264'
    assert (stream['width'], stream['height']) == size
    assert stream['nb_frames'] == '120'
    assert float(probe['format']['duration']) == pytest.approx(4, abs=.1)
    assert (folder / 'product.yaml').read_bytes() == before
    assert job['name'] not in [p.name for p in cli._shot_photos(Product.load(folder), 5)]
    assert client.get(job['url']).data == clip.read_bytes()
    # Retry after a lost response returns the same media without animating again.
    repeated = client.post(BASE + '/gemini-import', data=dict(image_form(project), aspect=aspect))
    assert repeated.status_code == 200 and repeated.json['id'] == job['id']
    assert len(list((folder / 'photos').iterdir())) == 4
    assert client.post(BASE + '/' + job['id'] + '/refresh', json={}).json['name'] == job['name']
    assert client.get(BASE).json['jobs'][0]['provider'] == 'gemini'
    stored = app.extensions['ai_video_store'].get(job['id'])
    assert 'key_fingerprint' not in stored and 'remote_id' not in stored
    (folder / 'photos' / job['name']).unlink()
    assert client.post(BASE + '/' + job['id'] + '/refresh', json={}).status_code == 400
    assert client.get(BASE).json['jobs'][0]['status'] == 'deleted'


@pytest.mark.parametrize('changes', [dict(source_photo='../brand.yaml'), dict(source_photo='missing.jpg'),
    dict(request_id='bad'), dict(aspect='1:1'), dict(duration='120'),
    dict(image=(BytesIO(b'<svg/>'), 'image.svg')), dict(image=(BytesIO(b'bad image'), 'image.png')),
    dict(image=(BytesIO(b''), 'image.jpg'))])
def test_invalid_import_does_not_change_library(client, project, changes):
    folder = project / 'products/test-rack'
    before = set((folder / 'photos').iterdir())
    response = client.post(BASE + '/gemini-import', data=dict(image_form(project), **changes))
    assert response.status_code == 400, response.json
    assert set((folder / 'photos').iterdir()) == before
    assert client.get(BASE).json == {'jobs': []}


def test_local_animation_failure_preserves_library_and_can_retry(client, project, monkeypatch):
    animate = provider.animate_image
    def fail(*args, **kwargs):
        raise provider.VideoError('Check FFmpeg.')
    monkeypatch.setattr(provider, 'animate_image', fail)
    assert client.post(BASE + '/gemini-import', data=image_form(project)).status_code == 400
    assert len(list((project / 'products/test-rack/photos').iterdir())) == 3
    assert client.get(BASE).json == {'jobs': []}
    monkeypatch.setattr(provider, 'animate_image', animate)
    assert client.post(BASE + '/gemini-import', data=image_form(project)).status_code == 201


def test_reference_download_is_local_and_rejects_paths(client, project):
    response = client.get(BASE + '/reference?source_photo=1.jpg')
    assert response.status_code == 200 and response.mimetype == 'image/jpeg'
    assert 'attachment' in response.headers['Content-Disposition']
    assert response.data == (project / 'products/test-rack/photos/1.jpg').read_bytes()
    for source in ('../brand.yaml', 'missing.jpg'):
        assert client.get(BASE + '/reference', query_string={'source_photo': source}).status_code == 400
    assert client.get('/products/missing/scenes/ai-video/reference?source_photo=1.jpg').status_code == 400


def test_gemini_selection_cannot_submit_to_elevenlabs(client):
    response = client.post(BASE, json=dict(allow_generation=True, provider='gemini'))
    assert response.status_code == 400 and 'Import Gemini image' in response.json['error']


def test_gemini_collection_clip_retains_product_and_reaches_build(client, project, photos, monkeypatch):
    make_product(project, 'chair', dict(PRODUCT, name_en='Chair'), photos)
    response = client.post('/collections/new', data={'name': 'Range', 'products': ['test-rack', 'chair']})
    slug = response.location.split('/')[-2]
    folder = project / 'products' / slug
    product = Product.load(folder)
    source = next(iter(product.collection_members[1]['media'].values()))
    response = client.post(f'/products/{slug}/scenes/ai-video/gemini-import',
                           data=dict(image_form(project), source_photo=source))
    assert response.status_code == 201, response.json
    job = response.json
    assert job['member_slug'] == 'chair'
    members = read_yaml(folder / 'product.yaml')['collection_members']
    assert job['name'] in members[1]['media'].values()
    assert job['name'] not in members[0]['media'].values()
    calls = []
    monkeypatch.setattr(cli, 'build_one', lambda *a, **kw: calls.append(kw) or [])
    assert client.post(f'/products/{slug}/build', data=dict(lang='en', tts='silent',
        seg_role_en='usp', seg_vo_en='Chair stays comfortable.', seg_overlay_en='Chair',
        seg_photo_en=job['name'])).status_code == 200
    assert calls[0]['photo_names'] == [job['name']]
    assert calls[0]['segments'][0].vo == 'Chair stays comfortable.'
