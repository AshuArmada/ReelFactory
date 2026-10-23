"""Optional AI clips remain drafts until explicitly chosen as scene media."""
import json
import shutil

import pytest
from werkzeug.datastructures import MultiDict

from conftest import PRODUCT, form, make_product, read_yaml
from reelfactory import ai_video as provider, cli
from reelfactory.config import Product
from reelfactory.render import RenderError
from reelfactory.web import ai_video as web_video
from reelfactory.web.app import _load_saved_scripts

BASE = '/products/test-rack/scenes/ai-video'


def values(**changes):
    return dict(allow_generation=True, request_id='test-request-token-1234', source_photo='1.jpg',
                prompt='A slow, steady orbit of this product.', **changes)


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(provider, 'credentials', lambda fingerprint=None: ('private-api-key', 'key-fingerprint'))
    monkeypatch.setattr(provider, 'create', lambda payload, key: calls.append((payload, key)) or 'remote-video')
    monkeypatch.setattr(provider, 'status', lambda *a: dict(id='remote-video', status='completed',
        content_mime_type='video/mp4', content_url='https://cdn.example/video.mp4?secret-signed-url'))
    monkeypatch.setattr(provider, 'download', lambda url, destination: destination.write_bytes(b'generated video'))
    monkeypatch.setattr(web_video, 'validate_video', lambda path: None)
    return calls


def refresh(client, job, base=BASE):
    return client.post(base + '/' + job['id'] + '/refresh', json={})


def test_generation_requires_explicit_opt_in(client, fake_provider):
    for payload in ({}, [], dict(values(), allow_generation=False), dict(values(), request_id='../bad')):
        assert client.post(BASE, json=payload).status_code == 400
    assert not fake_provider


@pytest.mark.parametrize('changes', [dict(source_photo='../brand.yaml'), dict(source_photo='missing.jpg'),
    dict(model='other'), dict(model=[]), dict(duration='10'), dict(prompt={}), dict(aspect=[]), dict(resolution=None)])
def test_invalid_scene_settings_do_not_generate(client, fake_provider, changes):
    assert client.post(BASE, json=dict(values(), **changes)).status_code == 400
    assert not fake_provider


def test_create_deduplicates_and_does_not_edit_script_or_photos(client, app, project, fake_provider):
    folder = project / 'products/test-rack'
    before = (folder / 'product.yaml').read_bytes()
    response = client.post(BASE, json=values())
    assert response.status_code == 202
    job = response.json
    assert job['status'] == 'pending' and not job['name']
    again = client.post(BASE, json=values())
    assert again.json['id'] == job['id'] and len(fake_provider) == 1
    assert len(list((folder / 'photos').iterdir())) == 3
    assert (folder / 'product.yaml').read_bytes() == before
    assert not _load_saved_scripts(project / 'products', 'test-rack')
    payload, key = fake_provider[0]
    assert payload['start_frame']['type'] == 'inline_base64'
    assert payload['generate_audio'] is False and 'consistent lighting' in payload['prompt']
    stored = app.extensions['ai_video_store'].get(job['id'])
    for secret in ('private-api-key', 'secret-signed-url'):
        assert secret not in json.dumps(stored) + json.dumps(job)
    assert 'key_fingerprint' not in job and 'prompt' not in job


def test_completion_is_imported_once_but_never_chosen_automatically(client, project, fake_provider):
    job = client.post(BASE, json=values()).json
    ready = refresh(client, job)
    assert ready.status_code == 200 and ready.json['status'] == 'completed'
    name = ready.json['name']
    assert name == 'ai-' + job['id'] + '.mp4'
    assert client.get(ready.json['url']).data == b'generated video'
    assert refresh(client, job).json['name'] == name
    product = Product.load(project / 'products/test-rack')
    assert name not in [p.name for p in cli._shot_photos(product, 10)]
    assert cli._shot_photos(product, 2, [name, '2.jpg'])[0].name == name
    assert len(fake_provider) == 1
    assert client.get(BASE).json['jobs'][0]['name'] == name
    assert 'secret-signed-url' not in json.dumps(client.get(BASE).json)


def test_reference_can_be_disabled(client, fake_provider):
    response = client.post(BASE, json=values(use_reference=False))
    assert response.status_code == 202
    assert 'start_frame' not in fake_provider[0][0]


def test_uncertain_submission_never_creates_another_paid_request(client, app, monkeypatch):
    calls = []
    def timeout(*args):
        calls.append(args)
        raise provider.VideoError('Submission not confirmed. Check ElevenLabs history.', uncertain=True)
    monkeypatch.setattr(provider, 'create', timeout)
    job = client.post(BASE, json=values()).json
    assert job['status'] == 'uncertain'
    assert client.post(BASE, json=values()).json['id'] == job['id']
    assert refresh(client, job).json['status'] == 'uncertain'
    assert len(calls) == 1


def test_pending_generation_resumes_with_new_app(client, project, monkeypatch):
    from reelfactory.web.app import create_app
    from conftest import CSRFClient
    job = client.post(BASE, json=values()).json
    new_app = create_app(project / 'brand.yaml', project / 'products', project / 'out')
    new_app.test_client_class = CSRFClient
    try:
        with new_app.test_client() as other:
            assert other.get(BASE).json['jobs'][0]['id'] == job['id']
            assert refresh(other, job).json['status'] == 'completed'
    finally:
        for handler in new_app.extensions['error_logger'].handlers:
            handler.close()


def test_polling_uses_saved_key_fingerprint(client, monkeypatch):
    job = client.post(BASE, json=values()).json
    credentials = []
    monkeypatch.setattr(provider, 'credentials', lambda fingerprint: credentials.append(fingerprint) or ('original-key', fingerprint))
    monkeypatch.setattr(provider, 'status', lambda remote, key: {'status': 'generating'})
    assert refresh(client, job).json['status'] == 'generating'
    assert credentials == ['key-fingerprint']


@pytest.mark.parametrize('failure', ['download', 'decode'])
def test_import_failure_preserves_scene_and_allows_refresh_without_regeneration(client, project, monkeypatch, fake_provider, failure):
    job = client.post(BASE, json=values()).json
    if failure == 'download':
        def fail(*args): raise provider.VideoError('Download interrupted; refresh.')
        monkeypatch.setattr(provider, 'download', fail)
    else:
        def fail(*args): raise RenderError('The generated video could not be read.')
        monkeypatch.setattr(web_video, 'validate_video', fail)
    assert refresh(client, job).status_code == 400
    assert len(list((project / 'products/test-rack/photos').iterdir())) == 3
    assert client.get(BASE).json['jobs'][0]['status'] == 'pending'
    assert len(fake_provider) == 1
    monkeypatch.setattr(provider, 'download', lambda url, destination: destination.write_bytes(b'generated video'))
    monkeypatch.setattr(web_video, 'validate_video', lambda path: None)
    assert refresh(client, job).json['status'] == 'completed'
    assert len(fake_provider) == 1


def test_deleted_clip_is_not_restored_by_refresh(client, project):
    job = client.post(BASE, json=values()).json
    completed = refresh(client, job).json
    (project / 'products/test-rack/photos' / completed['name']).unlink()
    result = refresh(client, job)
    assert result.status_code == 400 and 'deleted' in result.json['error']
    history = client.get(BASE).json['jobs'][0]
    assert history['status'] == 'deleted' and not history['name'] and 'url' not in history
    assert len(list((project / 'products/test-rack/photos').iterdir())) == 3


def test_deleted_product_is_not_recreated_by_pending_generation(client, project):
    job = client.post(BASE, json=values()).json
    folder = project / 'products/test-rack'
    shutil.rmtree(folder)
    assert refresh(client, job).status_code == 400
    assert not folder.exists()


def test_jobs_are_scoped_to_product_and_invalid_ids_are_rejected(client, project, photos):
    job = client.post(BASE, json=values()).json
    make_product(project, 'other', PRODUCT, photos)
    other = '/products/other/scenes/ai-video'
    assert client.get(other).json == {'jobs': []}
    assert refresh(client, job, other).status_code == 404
    assert client.post(BASE + '/bad-id/refresh', json={}).status_code == 400


def test_chosen_ai_clip_survives_script_save_and_reaches_build(client, project, monkeypatch):
    job = client.post(BASE, json=values()).json
    name = refresh(client, job).json['name']
    data = form(('lang', 'en'), ('tts', 'silent'), ('aspect', '9:16'), ('save_name', 'AI choice'),
        ('seg_role_en', 'hook'), ('seg_vo_en', 'The original narration stays.'),
        ('seg_overlay_en', 'Original caption'), ('seg_photo_en', name))
    assert client.post('/products/test-rack/script/save', data=data).status_code == 200
    assert _load_saved_scripts(project / 'products', 'test-rack')['en'][0]['segments'][0]['photo'] == name
    calls = []
    monkeypatch.setattr(cli, 'build_one', lambda *a, **kw: calls.append(kw) or [])
    assert client.post('/products/test-rack/build', data=data).status_code == 200
    assert calls[0]['photo_names'] == [name]
    assert calls[0]['segments'][0].vo == 'The original narration stays.'


def test_collection_clip_stays_with_its_source_product(client, project, photos):
    make_product(project, 'chair', dict(PRODUCT, name_en='Chair'), photos)
    response = client.post('/collections/new', data=MultiDict([
        ('name', 'Range'), ('products', 'test-rack'), ('products', 'chair')]))
    slug = response.location.split('/')[-2]
    folder = project / 'products' / slug
    product = Product.load(folder)
    source = next(iter(product.collection_members[1]['media'].values()))
    base = f'/products/{slug}/scenes/ai-video'
    job = client.post(base, json=dict(values(), source_photo=source)).json
    assert job['member_slug'] == 'chair'
    ready = refresh(client, job, base).json
    members = read_yaml(folder / 'product.yaml')['collection_members']
    assert ready['name'] in members[1]['media'].values()
    assert ready['name'] not in members[0]['media'].values()
    assert ready['name'] not in [p.name for p in cli._shot_photos(Product.load(folder), 4)]
