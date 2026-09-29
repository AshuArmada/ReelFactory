"""Temporary quota limits resume the request; hard quotas remain bounded."""
import base64
import json
import wave

import pytest
import requests

from reelfactory import gemini, voice


def response(status, body, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result.headers.update(headers or {})
    return result


def quota(delay="45.08161551s", quota_id="RequestsPerMinute", quota_value="3"):
    return response(429, {"error": {"message": "Quota exceeded", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": quota_id, "quotaValue": quota_value},
        ]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
    ]}})


@pytest.mark.parametrize("limited,wait", [
    (quota(), 46.08161551),
    (response(429, {"error": {"message": "Please retry in 45.08161551s."}}), 46.08161551),
    (response(429, {}, {"Retry-After": "20"}), 21),
    (quota("0s"), 1),
])
def test_temporary_limit_retries_same_payload(monkeypatch, limited, wait):
    calls, sleeps = [], []
    replies = iter([limited, response(200, {"ok": True})])

    def post(*args, **kwargs):
        calls.append(kwargs)
        return next(replies)

    monkeypatch.setattr(gemini.requests, "post", post)
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    assert gemini.generate_content("test-model", "key", {"contents": []}) == {"ok": True}
    assert calls[0] == calls[1]
    assert sleeps == [pytest.approx(wait)]


@pytest.mark.parametrize("limited", [
    quota(quota_id="RequestsPerDay"), quota(quota_value="0"),
    quota("3600s"), quota("nans"), quota("-1s"),
    response(429, {"error": "Quota exceeded"}),
    response(429, {"error": {"message": "Daily quota exceeded. Please retry in 45s."}}),
])
def test_hard_or_unknown_quota_does_not_sleep(monkeypatch, limited):
    calls = []
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **kw: calls.append(kw) or limited)
    monkeypatch.setattr(gemini.time, "sleep", lambda _: pytest.fail("Must not wait"))
    with pytest.raises(gemini.GeminiQuotaError, match="same project shares the same quota"):
        gemini.generate_content("test-model", "key", {})
    assert len(calls) == 1


def test_repeated_rate_limit_stops_after_bounded_retries(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **kw: calls.append(kw) or quota("2s"))
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    with pytest.raises(gemini.GeminiQuotaError):
        gemini.generate_content("test-model", "key", {})
    assert len(calls) == 3
    assert sleeps == [3, 3]


def test_backup_still_used_after_quota_failure(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        key = kwargs["headers"]["x-goog-api-key"]
        calls.append(key)
        return quota(quota_id="RequestsPerDay") if key == "primary" else response(200, {"ok": True})

    monkeypatch.setattr(gemini.requests, "post", post)
    assert gemini.generate_content("test-model", "primary", {}, backup_key="backup") == {"ok": True}
    assert calls == ["primary", "backup"]


def test_tts_resumes_fourth_line_without_regenerating_previous_audio(monkeypatch, tmp_path):
    audio = response(200, {"candidates": [{"content": {"parts": [{"inlineData": {
        "data": base64.b64encode(b"\0\0" * 240).decode(), "mimeType": "audio/L16;rate=24000",
    }}]}}]})
    replies = iter([audio, audio, audio, quota(), audio])
    transcripts, sleeps = [], []

    def post(*args, **kwargs):
        transcripts.append(kwargs["json"]["contents"][0]["parts"][0]["text"].split("Transcript:\n")[1])
        return next(replies)

    monkeypatch.setattr(gemini.requests, "post", post)
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    monkeypatch.setattr(gemini, "resolve_backup_key", lambda _: None)
    paths = voice._gemini(["One", "Two", "Three", "Four"], tmp_path, "Kore", "test-model", "test-key")
    assert transcripts == ["One", "Two", "Three", "Four", "Four"]
    assert sleeps == [pytest.approx(46.08161551)]
    assert len(paths) == 4
    for path in paths:
        with wave.open(str(path)) as clip:
            assert clip.getnframes() == 240
