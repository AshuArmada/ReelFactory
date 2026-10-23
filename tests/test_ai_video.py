"""ElevenLabs video contracts and paid-request handling, without API credits."""
import base64
import json
import socket

import pytest
import requests

from reelfactory import ai_video as video


def response(data=None, status=200, content_type='video/mp4'):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode() if data is not None else b'fake mp4'
    result._content_consumed = True
    result.headers['Content-Type'] = content_type
    return result


def test_creation_contract_reference_and_no_generated_audio(monkeypatch, tmp_path):
    photo = tmp_path / 'product.png'
    photo.write_bytes(b'reference picture')
    payload = video.settings({'prompt': 'A slow camera orbit', 'duration': '6'})
    payload['start_frame'] = video.image_reference(photo)
    calls = []
    monkeypatch.setattr(video.requests, 'request', lambda *a, **kw: calls.append((a, kw)) or response({'id': 'vid_123', 'status': 'pending'}))
    assert video.create(payload, 'secret') == 'vid_123'
    args, kwargs = calls[0]
    assert args == ('POST', 'https://api.elevenlabs.io/v1/flows/video')
    assert kwargs['headers'] == {'xi-api-key': 'secret'}
    assert kwargs['allow_redirects'] is False
    assert kwargs['json'] == dict(prompt='A slow camera orbit', model_id='veo-3.1-fast-generate-001',
        duration_secs=6, aspect_ratio='9:16', resolution='720p', generate_audio=False, enhance_prompt=False,
        start_frame={'type': 'inline_base64', 'mime_type': 'image/png',
                     'content_base64': base64.b64encode(photo.read_bytes()).decode()})


@pytest.mark.parametrize('values', [dict(prompt='a'), dict(prompt=None), dict(prompt={}),
    dict(model='unknown'), dict(model=[]), dict(duration=10), dict(aspect='1:1'), dict(resolution='4K')])
def test_invalid_settings_cannot_submit(values):
    with pytest.raises(video.VideoError):
        video.settings(dict({'prompt': 'A slow camera orbit'}, **values))


@pytest.mark.parametrize('http,uncertain', [(401, False), (402, False), (403, False), (422, False), (429, False), (500, True), (408, True)])
def test_http_failures_do_not_retry_or_leak_response(monkeypatch, http, uncertain):
    calls = []
    monkeypatch.setattr(video.requests, 'request', lambda *a, **kw: calls.append(kw) or response({'error': 'secret'}, http))
    with pytest.raises(video.VideoError, match=f'HTTP {http}') as error:
        video.create({}, 'secret')
    assert error.value.uncertain == uncertain
    assert 'secret' not in str(error.value)
    assert len(calls) == 1


def test_submission_timeout_is_uncertain_and_never_retried(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise requests.Timeout('private-key')
    monkeypatch.setattr(video.requests, 'request', fail)
    with pytest.raises(video.VideoError) as error:
        video.create({}, 'private-key')
    assert error.value.uncertain and len(calls) == 1
    assert 'private-key' not in str(error.value)


@pytest.mark.parametrize('data', [{}, [], {'id': '../private', 'status': 'pending'}, {'id': 2, 'status': 'pending'}])
def test_malformed_creation_is_uncertain(monkeypatch, data):
    monkeypatch.setattr(video.requests, 'request', lambda *a, **kw: response(data))
    with pytest.raises(video.VideoError) as error:
        video.create({}, 'secret')
    assert error.value.uncertain


def test_status_contract_and_sanitized_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(video.requests, 'request', lambda *a, **kw: calls.append((a, kw)) or response({
        'id': 'vid_123', 'status': 'failed', 'failure_reason': 'charging_failed', 'error_message': 'private-key'}))
    result = video.status('vid_123', 'secret')
    assert calls[0][0] == ('GET', video.BASE + '/vid_123')
    assert result['status'] == 'failed' and 'credits' in result['error']
    assert 'private' not in str(result)


@pytest.mark.parametrize('data', [{'id': 'other', 'status': 'pending'}, {'id': 'vid', 'status': []},
    {'id': 'vid', 'status': 'completed', 'content_url': 'https://cdn.example/clip.mp4', 'content_mime_type': 'text/html'}])
def test_unusable_status_is_recoverable_error(monkeypatch, data):
    monkeypatch.setattr(video.requests, 'request', lambda *a, **kw: response(data))
    with pytest.raises(video.VideoError):
        video.status('vid', 'secret')


def test_refresh_keeps_original_credential_even_if_key_order_changes(monkeypatch):
    monkeypatch.setattr(video, 'resolve_keys', lambda: ['first', 'second'])
    key, fingerprint = video.credentials()
    monkeypatch.setattr(video, 'resolve_keys', lambda: ['second', 'first'])
    assert video.credentials(fingerprint) == (key, fingerprint)
    monkeypatch.setattr(video, 'resolve_keys', lambda: ['second'])
    with pytest.raises(video.VideoError, match='Restore'):
        video.credentials(fingerprint)


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(video.socket, 'getaddrinfo', lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])


def test_download_has_no_api_key_and_disallows_redirects(monkeypatch, tmp_path, public_dns):
    calls = []
    monkeypatch.setattr(video.requests, 'get', lambda *a, **kw: calls.append(kw) or response())
    dest = tmp_path / 'clip.mp4'
    video.download('https://cdn.example/clip.mp4?temporary-token=x', dest)
    assert dest.read_bytes() == b'fake mp4'
    assert 'headers' not in calls[0] and calls[0]['allow_redirects'] is False


@pytest.mark.parametrize('url', ['http://cdn.example/a', 'https://u:p@cdn.example/a', 'https://cdn.example:80/a'])
def test_invalid_download_url_never_reaches_network(tmp_path, url):
    with pytest.raises(video.VideoError, match='address'):
        video.download(url, tmp_path / 'clip.mp4')


def test_private_download_host_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(video.socket, 'getaddrinfo', lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(video.VideoError, match='address'):
        video.download('https://localhost/a', tmp_path / 'clip.mp4')


@pytest.mark.parametrize('failure', ['oversize', 'stream_oversize', 'empty', 'html', 'redirect'])
def test_download_is_bounded_and_requires_mp4(monkeypatch, tmp_path, public_dns, failure):
    result = response()
    if failure == 'oversize': result.headers['Content-Length'] = str(video.MAX_VIDEO_BYTES + 1)
    if failure == 'stream_oversize': monkeypatch.setattr(video, 'MAX_VIDEO_BYTES', 2)
    if failure == 'empty': result._content = b''
    if failure == 'html': result.headers['Content-Type'] = 'text/html'
    if failure == 'redirect': result.status_code = 302
    monkeypatch.setattr(video.requests, 'get', lambda *a, **kw: result)
    with pytest.raises(video.VideoError):
        video.download('https://cdn.example/a', tmp_path / 'clip.mp4')
