"""Delivery guidance reaches the provider without changing the transcript."""
import base64
import wave

from reelfactory import voice


def test_gemini_delivery_and_exact_transcript(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(voice.gemini, "resolve_key", lambda key: "test-key")
    monkeypatch.setattr(voice.gemini, "resolve_backup_key", lambda key: None)

    def generate(model, key, payload, **kwargs):
        captured.append(payload)
        return {"candidates": [{"content": {"parts": [{"inlineData": {
            "data": base64.b64encode(b"\0\0" * 240).decode(),
            "mimeType": "audio/L16;rate=24000",
        }}]}}]}

    monkeypatch.setattr(voice.gemini, "generate_content", generate)
    paths = voice._gemini(["Hello, come take a look!"], tmp_path, "Kore", "test-model", None,
                          delivery="Warm and relaxed")
    prompt = captured[0]["contents"][0]["parts"][0]["text"]
    assert "Warm and relaxed" in prompt
    assert prompt.endswith("Transcript:\nHello, come take a look!")
    with wave.open(str(paths[0])) as audio:
        assert audio.getframerate() == 24000
        assert audio.getnframes() == 240
