import re

import pytest

from reelfactory import gemini, local_llm, hosted_script
from reelfactory.config import Brand


@pytest.fixture(autouse=True)
def isolated_config(client, project, monkeypatch):
    client.application.config['API_ENV_PATH'] = project / '.env'
    monkeypatch.setattr(gemini, 'ROOT', project)
    monkeypatch.setattr(local_llm, 'ROOT', project)
    from reelfactory.web.api_settings import PROVIDERS
    for _, _, keys, fields in PROVIDERS.values():
        for name, *_ in keys + fields:
            monkeypatch.delenv(name, raising=False)


def save(client, **data):
    page = client.get('/settings/apis')
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))[1]
    return client.post('/settings/apis', data={'csrf_token': token, **data})


def test_keys_stay_masked_and_blank_preserves_existing(client, project):
    env = project / '.env'
    env.write_text('# Keep comment\ngemini_key=private-old-key\nUNRELATED=keep\n')
    page = client.get('/settings/apis')
    assert b'private-old-key' not in page.data
    assert page.headers['Cache-Control'] == 'no-store'
    assert save(client, provider='gemini', GEMINI_API_KEY='').status_code == 302
    assert gemini.resolve_key() == 'private-old-key'
    assert 'UNRELATED=keep' in env.read_text()


def test_replacement_removes_aliases_and_is_used_immediately(client, project):
    env = project / '.env'
    env.write_text('gemini_key=old\nGOOGLE_API_KEY=other-old\n')
    response = save(client, provider='gemini', GEMINI_API_KEY='new-secret',
                    gemini_script_model='chosen-model')
    assert response.status_code == 302
    assert gemini.resolve_key() == 'new-secret'
    assert 'old' not in env.read_text()
    assert Brand.load(project / 'brand.yaml').gemini_script_model == 'chosen-model'
    assert 'new-secret' not in (project / 'brand.yaml').read_text(encoding='utf-8')
    assert b'new-secret' not in client.get('/settings/apis').data


def test_remove_key_and_keep_other_provider(client, project):
    (project / '.env').write_text('gemini_key=secret\nINCEPTION_API_KEY=keep\n')
    assert save(client, provider='gemini', clear_GEMINI_API_KEY='1').status_code == 302
    with pytest.raises(gemini.GeminiError):
        gemini.resolve_key()
    assert hosted_script.resolve_key('inception') == 'keep'


def test_model_endpoint_and_local_brand_settings(client, project):
    assert save(client, provider='inception', INCEPTION_API_KEY='secret',
                INCEPTION_MODEL='chosen-model', INCEPTION_BASE_URL='https://example.test/v1').status_code == 302
    assert hosted_script.setting('inception', 'MODEL') == 'chosen-model'
    assert hosted_script.setting('inception', 'BASE_URL') == 'https://example.test/v1'
    assert save(client, provider='local', local_script_model='my-model',
                local_base_url='http://localhost:1234/v1').status_code == 302
    brand = Brand.load(project / 'brand.yaml')
    assert brand.local_script_model == 'my-model'
    assert brand.local_base_url == 'http://localhost:1234/v1'
    assert brand.name == 'Test Steel Works'


@pytest.mark.parametrize('value', ['secret\nINJECT=bad', 'secret\u2028INJECT=bad', 'secret"bad'])
def test_invalid_secret_does_not_leak_or_write(client, project, value):
    response = save(client, provider='inception', INCEPTION_API_KEY=value)
    assert response.status_code == 400
    assert 'secret' not in response.get_data(as_text=True)
    assert not (project / '.env').exists()


def test_bad_url_prevents_key_and_model_save(client, project):
    before = (project / 'brand.yaml').read_bytes()
    response = save(client, provider='local', LOCAL_LLM_API_KEY='secret',
                    local_base_url='https://user:password@example.test/v1')
    assert response.status_code == 400
    assert (project / 'brand.yaml').read_bytes() == before
    assert not (project / '.env').exists()


def test_csrf_and_unknown_provider_are_rejected(client, project):
    assert client.post('/settings/apis', data={'provider': 'gemini', 'GEMINI_API_KEY': 'bad'}).status_code == 400
    assert save(client, provider='unknown', GEMINI_API_KEY='bad').status_code == 400
    assert not (project / '.env').exists()


def test_environment_override_is_explained_without_exposing_it(client, monkeypatch):
    monkeypatch.setenv('INCEPTION_API_KEY', 'system-secret')
    page = client.get('/settings/apis').get_data(as_text=True)
    assert 'system environment variable is active' in page
    assert 'system-secret' not in page


def test_write_failure_does_not_echo_submitted_key(client, monkeypatch):
    from reelfactory.web import api_settings
    def fail(*args):
        raise OSError('sensitive-internal-error')
    monkeypatch.setattr(api_settings, '_save_env', fail)
    response = save(client, provider='gemini', GEMINI_API_KEY='submitted-secret')
    assert response.status_code == 500
    assert b'submitted-secret' not in response.data
    assert b'sensitive-internal-error' not in response.data
