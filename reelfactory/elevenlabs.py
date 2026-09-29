"""ElevenLabs speech generation; credentials stay outside brand configuration."""
from __future__ import annotations

import os
import re
from pathlib import Path

import requests

from .gemini import _load_dotenv_key

DEFAULT_MODEL = "eleven_multilingual_v2"


class ElevenLabsError(RuntimeError):
    pass


class _RetryableError(ElevenLabsError):
    """A different key may succeed; never includes credentials or raw responses."""


def resolve_keys(explicit: str | None = None) -> list[str]:
    def configured(name):
        return (os.environ.get(name) or _load_dotenv_key({name.lower()}) or "").strip()

    primary = (explicit or "").strip() or configured("ELEVENLABS_API_KEY")
    candidates = [primary, configured("ELEVENLABS_API_KEY_BACKUP")]
    candidates.extend(re.split(r"[,;\s]+", configured("ELEVENLABS_API_KEYS")))
    keys = list(dict.fromkeys(key for key in candidates if key))
    if not keys:
        raise ElevenLabsError("Set ELEVENLABS_API_KEY in .env or your environment to use ElevenLabs narration.")
    return keys


def resolve_key(explicit: str | None = None) -> str:
    return resolve_keys(explicit)[0]


def synthesize(lines, lang: str, outdir: Path, voice_id: str,
               model: str = DEFAULT_MODEL, api_key: str | None = None):
    keys = resolve_keys(api_key)
    key_index = 0
    voice_id = str(voice_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_id):
        raise ElevenLabsError(
            f"Set a valid ElevenLabs {lang} voice ID in Brand > Voice "
            f"(elevenlabs_voice_{lang} in brand.yaml). Copy the ID from your ElevenLabs voice library."
        )
    lines = list(lines)
    paths = []
    for i, line in enumerate(lines):
        payload = {"text": line, "model_id": model or DEFAULT_MODEL}
        # Multilingual v2 detects language from the transcript; other models
        # can use an explicit ISO language code.
        if payload["model_id"] != DEFAULT_MODEL:
            payload["language_code"] = lang
        # Text context helps keep separately timed clips consistent. V3 does
        # not support these context fields.
        if payload["model_id"] == DEFAULT_MODEL:
            if i:
                payload["previous_text"] = lines[i - 1]
            if i + 1 < len(lines):
                payload["next_text"] = lines[i + 1]
        while True:
            try:
                audio = _request(voice_id, payload, keys[key_index], i + 1)
                break
            except _RetryableError as exc:
                key_index += 1
                if key_index == len(keys):
                    raise ElevenLabsError(
                        f"No working ElevenLabs keys remain ({len(keys)} configured). {exc}"
                    ) from None
                print(f"   ElevenLabs: retrying segment {i + 1} with key {key_index + 1} of {len(keys)}.")
        dest = outdir / f"seg{i:02d}.mp3"
        dest.write_bytes(audio)
        paths.append(dest)
    return paths


def _request(voice_id: str, payload: dict, key: str, segment: int) -> bytes:
    try:
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": key, "Accept": "audio/mpeg"},
            json=payload, timeout=(10, 120),
        )
    except requests.RequestException:
        raise _RetryableError(
            f"Could not reach ElevenLabs or the request timed out on segment {segment}. "
            "Check your connection and try again."
        ) from None
    with response:
        if not response.ok:
            hints = {
                401: "Check your API keys, credits and text-to-speech permissions.",
                403: "Check your API key permissions, subscription and access to this voice.",
                404: "Check the voice ID and that this voice is available to your account.",
                429: "Your ElevenLabs quota or rate limit was reached. Check your credits or try later.",
                422: "Check the model, voice and language settings.",
            }
            hint = hints.get(response.status_code, "Check the model, voice, account credits and service status.")
            retryable = response.status_code in {401, 402, 403, 404, 408, 429} or response.status_code >= 500
            error_type = _RetryableError if retryable else ElevenLabsError
            raise error_type(f"ElevenLabs failed on segment {segment} (HTTP {response.status_code}). {hint}")
        content_type = response.headers.get("Content-Type", "").lower()
        if not response.content or not (content_type.startswith("audio/") or "application/octet-stream" in content_type):
            raise _RetryableError(f"ElevenLabs returned no valid audio for segment {segment}.")
        return response.content
