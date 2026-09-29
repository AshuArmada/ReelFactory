from types import SimpleNamespace

import pytest

from reelfactory import hosted_script as hosted, local_llm
from reelfactory.config import Product, Brand


def test_inception_hindi_is_blocked_before_any_api_request(monkeypatch, tmp_path):
    product = Product(slug='roof', dir=tmp_path, photos=[], name_en='Roof', name_hi='छत',
                      usp_hi=['पसंद का रंग'])
    monkeypatch.setattr(hosted.requests, 'post', lambda *a, **k: pytest.fail('Unexpected API call'))
    with pytest.raises(hosted.HostedScriptError, match='Choose Gemini'):
        hosted.build(product, Brand(), 'hi', 'inception')


def test_inception_does_not_block_existing_hindi_override(tmp_path):
    product = Product(slug='roof', dir=tmp_path, photos=[], name_en='Roof', name_hi='छत',
                      script_hi=['छत के लिए अपनी पसंद का रंग चुनिए।'])
    assert hosted.build(product, Brand(), 'hi', 'inception')[0].vo == product.script_hi[0]


@pytest.fixture(autouse=True)
def isolated_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(local_llm, "ROOT", tmp_path)
    for provider in hosted.PROVIDERS:
        for suffix in ("API_KEY", "MODEL", "BASE_URL"):
            monkeypatch.delenv(f"{provider.upper()}_{suffix}", raising=False)
    monkeypatch.setattr(hosted.time, "sleep", lambda _: None)


@pytest.mark.parametrize("provider", hosted.PROVIDERS)
def test_missing_key_is_actionable(provider):
    with pytest.raises(hosted.HostedScriptError, match=f"{provider.upper()}_API_KEY"):
        hosted.completion(provider, "brief")


@pytest.mark.parametrize("provider", hosted.PROVIDERS)
def test_request_configuration_and_json_response(provider, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"{provider.upper()}_API_KEY=dummy-secret\n")
    monkeypatch.setenv(f"{provider.upper()}_MODEL", "chosen-model")
    monkeypatch.setenv(f"{provider.upper()}_BASE_URL", "https://example.test/v1/")
    def post(url, **kwargs):
        assert url == "https://example.test/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer dummy-secret"
        assert kwargs["allow_redirects"] is False
        assert kwargs["json"]["model"] == "chosen-model"
        assert kwargs["json"]["messages"][0]["content"] == "product brief"
        assert kwargs["json"]["response_format"] == {"type": "json_object"}
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"message": {"content": '{"segments": []}'}}]})
    monkeypatch.setattr(hosted.requests, "post", post)
    assert hosted.completion(provider, "product brief") == '{"segments": []}'


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_http_errors_do_not_expose_response_or_key(status, monkeypatch):
    monkeypatch.setenv("INCEPTION_API_KEY", "dummy-secret")
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=status, text="dummy-secret private server details")
    monkeypatch.setattr(hosted.requests, "post", post)
    with pytest.raises(hosted.HostedScriptError, match=f"HTTP {status}") as error:
        hosted.completion("inception", "brief")
    assert "dummy-secret" not in str(error.value)
    assert len(calls) == (3 if status == 500 else 1)


@pytest.mark.parametrize("choice", [None, {}, {"message": {"content": ""}},
    {"message": {"content": "partial"}, "finish_reason": "length"}])
def test_unusable_responses_are_rejected(choice, monkeypatch):
    monkeypatch.setenv("INCEPTION_API_KEY", "dummy-secret")
    monkeypatch.setattr(hosted.requests, "post", lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: {"choices": [choice]}))
    with pytest.raises(hosted.HostedScriptError):
        hosted.completion("inception", "brief")
