"""Shared HTTP plumbing for calling the Grok (xAI) API.

Mirrors gemini.py: the API key is never read from a yaml file -- only from
the GROK_API_KEY environment variable, a .env file next to brand.yaml, or an
explicit --grok-key override -- so it can't end up committed alongside a
client's brand.yaml. The base URL and model name are not secrets, so they can
also come from brand.yaml / GROK_BASE_URL.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

DEFAULT_BASE_URL = "https://api.x.ai/v1"
ROOT = Path(__file__).resolve().parent.parent

TRANSIENT_STATUS = {500, 502, 503, 504}
MAX_ATTEMPTS = 3

_ENV_KEY_NAMES = {"grok_api_key", "xai_api_key"}
_ENV_BASE_URL_NAMES = {"grok_base_url", "xai_base_url"}


class GrokError(RuntimeError):
    pass


def resolve_key(explicit: str | None = None) -> str:
    key = explicit or os.environ.get("GROK_API_KEY") or _load_dotenv(_ENV_KEY_NAMES)
    if not key:
        raise GrokError(
            "No Grok API key found. Set the GROK_API_KEY environment variable, "
            "add it to a .env file next to brand.yaml, or pass --grok-key on the command line."
        )
    return key


def resolve_base_url(explicit: str | None = None) -> str:
    return (
        explicit
        or os.environ.get("GROK_BASE_URL")
        or _load_dotenv(_ENV_BASE_URL_NAMES)
        or DEFAULT_BASE_URL
    )


def _load_dotenv(names: set) -> str | None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().strip("'\"")
        if name.lower() not in names:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            return value
    return None


def chat_completion(
    model: str,
    api_key: str,
    messages: list,
    base_url: str | None = None,
    response_format: dict | None = None,
    temperature: float = 0.9,
    timeout: int = 60,
) -> dict:
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if response_format:
        payload["response_format"] = response_format

    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                url, headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=timeout
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(1.5 * attempt)
                continue
            raise GrokError(f"Could not reach the Grok API after {MAX_ATTEMPTS} attempts: {exc}") from exc

        if resp.status_code in TRANSIENT_STATUS and attempt < MAX_ATTEMPTS:
            time.sleep(1.5 * attempt)
            continue
        if resp.status_code != 200:
            raise GrokError(_friendly_error(resp))
        try:
            return resp.json()
        except ValueError as exc:
            raise GrokError(
                f"Grok API returned a response that was not valid JSON: {resp.text[:300]}"
            ) from exc
    raise GrokError(f"Could not reach the Grok API after {MAX_ATTEMPTS} attempts: {last_exc}")


def _friendly_error(resp) -> str:
    try:
        detail = resp.json().get("error", {}).get("message", resp.text[:300])
    except ValueError:
        detail = resp.text[:300]
    if resp.status_code in (401, 403):
        return f"Grok API rejected the key (HTTP {resp.status_code}): {detail}"
    if resp.status_code == 429:
        return f"Grok API rate limit or quota exceeded (HTTP 429): {detail}"
    if resp.status_code == 404:
        return f"Grok API model not found (HTTP 404): {detail}. Check the model name is correct."
    return f"Grok API error (HTTP {resp.status_code}): {detail}"
