"""Provider contract and failure handling without spending API credits."""
import pytest
import requests
from types import SimpleNamespace

from reelfactory import elevenlabs, gemini, voice
from reelfactory import cli
from reelfactory.config import Brand


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    monkeypatch.delenv("ELEVENLABS_API_KEY_BACKUP", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEYS", raising=False)
    monkeypatch.setattr(elevenlabs, "_load_dotenv_key", lambda names: None)


def response(status=200, content=b"ID3-audio", content_type="audio/mpeg"):
    result = requests.Response()
    result.status_code = status
    result._content = content
    result._content_consumed = True
    result.headers["Content-Type"] = content_type
    return result


def test_per_line_audio_context_and_duration(monkeypatch, tmp_path):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response()

    monkeypatch.setattr(elevenlabs.requests, "post", post)
    monkeypatch.setattr(voice, "probe_duration", lambda path: 2.7)
    clips = voice.synthesize(
        ["नमस्ते।", "Welcome home."], "hi", "unused", "+0%", tmp_path,
        backend="elevenlabs", elevenlabs_voice="my-voice",
    )
    assert len(clips) == 2
    assert all(c.duration == 2.7 and c.path.read_bytes() == b"ID3-audio" for c in clips)
    url, request = calls[0]
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/my-voice"
    assert request["headers"]["xi-api-key"] == "test-secret"
    assert request["params"] == {"output_format": "mp3_44100_128"}
    assert request["json"] == {
        "text": "नमस्ते।", "model_id": "eleven_multilingual_v2",
        "next_text": "Welcome home.",
    }
    assert calls[1][1]["json"]["previous_text"] == "नमस्ते।"


def test_v3_omits_unsupported_context(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(elevenlabs.requests, "post", lambda url, **kw: calls.append(kw) or response())
    elevenlabs.synthesize(["Hello.", "Welcome."], "en", tmp_path, "voice", "eleven_v3")
    assert all(c["json"]["model_id"] == "eleven_v3" for c in calls)
    assert all(c["json"]["language_code"] == "en" for c in calls)
    assert all("previous_text" not in c["json"] and "next_text" not in c["json"] for c in calls)


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429, 500])
def test_http_errors_are_actionable_without_leaking_secrets(monkeypatch, tmp_path, status):
    calls = []
    monkeypatch.setattr(elevenlabs.requests, "post", lambda *a, **kw: calls.append(kw) or response(status, b"test-secret"))
    with pytest.raises(voice.TTSError, match=f"HTTP {status}") as error:
        voice.synthesize(["Hello", "Next"], "en", "", "", tmp_path,
                         backend="elevenlabs", elevenlabs_voice="voice")
    assert "test-secret" not in str(error.value)
    assert len(calls) == 1
    assert not list(tmp_path.glob("*.mp3"))


@pytest.mark.parametrize("content,content_type", [(b"", "audio/mpeg"), (b'{}', "application/json")])
def test_empty_or_non_audio_response_is_rejected(monkeypatch, tmp_path, content, content_type):
    monkeypatch.setattr(elevenlabs.requests, "post", lambda *a, **k: response(content=content, content_type=content_type))
    with pytest.raises(elevenlabs.ElevenLabsError, match="no valid audio"):
        elevenlabs.synthesize(["Hello"], "en", tmp_path, "voice")
    assert not list(tmp_path.glob("*.mp3"))


def test_timeout_becomes_provider_error(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise requests.Timeout("test-secret")
    monkeypatch.setattr(elevenlabs.requests, "post", fail)
    with pytest.raises(elevenlabs.ElevenLabsError, match="timed out") as error:
        elevenlabs.synthesize(["Hello"], "en", tmp_path, "voice")
    assert "test-secret" not in str(error.value)


def test_missing_key_fails_before_network(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    with pytest.raises(elevenlabs.ElevenLabsError, match="ELEVENLABS_API_KEY"):
        elevenlabs.synthesize(["Hello"], "en", tmp_path, "voice")


@pytest.mark.parametrize("voice_id", ["", "../voice", "https://example.com"])
def test_invalid_voice_fails_before_network(tmp_path, voice_id):
    with pytest.raises(elevenlabs.ElevenLabsError, match="voice ID"):
        elevenlabs.synthesize(["Hello"], "en", tmp_path, voice_id)


def test_dotenv_and_environment_precedence(monkeypatch, tmp_path):
    monkeypatch.setattr(elevenlabs, "_load_dotenv_key", gemini._load_dotenv_key)
    monkeypatch.setattr(gemini, "ROOT", tmp_path)
    (tmp_path / ".env").write_text('ELEVENLABS_API_KEY="file-key"\n', encoding="utf-8")
    assert elevenlabs.resolve_key() == "test-secret"
    assert elevenlabs.resolve_key("explicit-key") == "explicit-key"
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    assert elevenlabs.resolve_key() == "file-key"


@pytest.mark.parametrize("lang,expected", [("hi", "hindi-id"), ("en", "english-id")])
def test_renderer_passes_language_specific_voice_and_model(monkeypatch, tmp_path, lang, expected):
    class ReachedVoice(Exception):
        pass

    def capture(lines, selected_lang, *args, **kwargs):
        assert lines == ["My narration"]
        assert selected_lang == lang
        assert kwargs["backend"] == "elevenlabs"
        assert kwargs["elevenlabs_voice"] == expected
        assert kwargs["elevenlabs_model"] == "eleven_v3"
        raise ReachedVoice

    monkeypatch.setattr(voice, "synthesize", capture)
    brand = Brand(elevenlabs_voice_hi="hindi-id", elevenlabs_voice_en="english-id",
                  elevenlabs_model="eleven_v3")
    with pytest.raises(ReachedVoice):
        cli._render_variant(SimpleNamespace(slug="sample"), brand, lang, ["9:16"],
                            tmp_path, SimpleNamespace(tts="elevenlabs"), None,
                            [SimpleNamespace(vo="My narration")])


def test_key_order_deduplication_and_backup_only(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY_BACKUP", "backup-secret")
    monkeypatch.setenv("ELEVENLABS_API_KEYS", "test-secret, third-secret;backup-secret\n fourth-secret ")
    assert elevenlabs.resolve_keys() == ["test-secret", "backup-secret", "third-secret", "fourth-secret"]
    assert elevenlabs.resolve_keys("explicit") == ["explicit", "backup-secret", "test-secret", "third-secret", "fourth-secret"]
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEYS")
    assert elevenlabs.resolve_key() == "backup-secret"


def test_multiple_dotenv_keys_with_environment_override(monkeypatch, tmp_path):
    monkeypatch.setattr(elevenlabs, "_load_dotenv_key", gemini._load_dotenv_key)
    monkeypatch.setattr(gemini, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        'ELEVENLABS_API_KEY=file-primary\n'
        'ELEVENLABS_API_KEY_BACKUP="file-backup"\n'
        'elevenlabs_api_keys="file-third, file-fourth"\n', encoding="utf-8",
    )
    assert elevenlabs.resolve_keys() == ["test-secret", "file-backup", "file-third", "file-fourth"]
    monkeypatch.setenv("ELEVENLABS_API_KEYS", "env-extra")
    assert elevenlabs.resolve_keys() == ["test-secret", "file-backup", "env-extra"]


@pytest.mark.parametrize("failure", [401, 402, 403, 404, 408, 429, 500, 503, "timeout", "empty"])
def test_failover_retries_only_failed_segment_and_keeps_working_key(monkeypatch, tmp_path, capsys, failure):
    monkeypatch.setenv("ELEVENLABS_API_KEY_BACKUP", "backup-secret")
    calls = []

    def post(url, **kwargs):
        key, text = kwargs["headers"]["xi-api-key"], kwargs["json"]["text"]
        calls.append((key, text))
        if key == "test-secret" and text == "Second":
            if failure == "timeout":
                raise requests.Timeout("test-secret")
            if failure == "empty":
                return response(content=b"")
            return response(failure, b"test-secret")
        return response(content=text.encode())

    monkeypatch.setattr(elevenlabs.requests, "post", post)
    paths = elevenlabs.synthesize(["First", "Second", "Third"], "en", tmp_path, "same-voice")
    assert calls == [("test-secret", "First"), ("test-secret", "Second"),
                     ("backup-secret", "Second"), ("backup-secret", "Third")]
    assert [path.read_bytes() for path in paths] == [b"First", b"Second", b"Third"]
    log = capsys.readouterr().out
    assert "segment 2" in log and "key 2 of 2" in log
    assert "test-secret" not in log and "backup-secret" not in log


def test_three_keys_exhausted_once_each_without_secret_leak(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEYS", "second-secret,third-secret,test-secret")
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["headers"]["xi-api-key"])
        return response(429, b"second-secret third-secret")

    monkeypatch.setattr(elevenlabs.requests, "post", post)
    with pytest.raises(elevenlabs.ElevenLabsError, match="3 configured") as error:
        elevenlabs.synthesize(["First", "Second"], "en", tmp_path, "voice")
    assert calls == ["test-secret", "second-secret", "third-secret"]
    assert "HTTP 429" in str(error.value)
    assert not list(tmp_path.glob("*.mp3"))
    visible = str(error.value) + capsys.readouterr().out
    assert all(key not in visible for key in calls)


@pytest.mark.parametrize("status", [400, 422])
def test_invalid_request_does_not_burn_through_backup_keys(monkeypatch, tmp_path, status):
    monkeypatch.setenv("ELEVENLABS_API_KEY_BACKUP", "backup-secret")
    calls = []
    monkeypatch.setattr(elevenlabs.requests, "post", lambda *a, **kw: calls.append(kw) or response(status))
    with pytest.raises(elevenlabs.ElevenLabsError, match=f"HTTP {status}"):
        elevenlabs.synthesize(["First"], "en", tmp_path, "voice")
    assert len(calls) == 1
