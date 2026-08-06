"""Shared HTTP plumbing for calling a local, OpenAI-compatible LLM server.

Mirrors grok.py, but for models running on the user's own machine -- Ollama
(http://localhost:11434/v1), LM Studio (http://localhost:1234/v1), llama.cpp's
server, etc. No API key is required by default since these servers are local
and unauthenticated; one can still be supplied (some setups gate access with
a dummy token) via LOCAL_LLM_API_KEY, a .env entry, or --local-key.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

DEFAULT_BASE_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
DEFAULT_MODEL = "llama3.2:3b"  # small enough for a 4GB laptop GPU
ROOT = Path(__file__).resolve().parent.parent

TRANSIENT_STATUS = {500, 502, 503, 504}
MAX_ATTEMPTS = 3

_ENV_KEY_NAMES = {"local_llm_api_key", "local_api_key", "local_llm_key"}
_ENV_BASE_URL_NAMES = {"local_llm_base_url", "local_base_url", "local_llm_url"}


class LocalLLMError(RuntimeError):
    pass


def resolve_base_url(explicit: str | None = None) -> str:
    return (
        explicit
        or os.environ.get("LOCAL_LLM_BASE_URL")
        or _load_dotenv(_ENV_BASE_URL_NAMES)
        or DEFAULT_BASE_URL
    )


def resolve_key(explicit: str | None = None) -> str | None:
    """Optional -- returns None (never raises) when no key is configured."""
    return explicit or os.environ.get("LOCAL_LLM_API_KEY") or _load_dotenv(_ENV_KEY_NAMES)


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
    messages: list,
    base_url: str | None = None,
    api_key: str | None = None,
    response_format: dict | None = None,
    temperature: float = 0.9,
    timeout: int = 120,
) -> dict:
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if response_format:
        payload["response_format"] = response_format
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(1.5 * attempt)
                continue
            raise LocalLLMError(
                f"Could not reach the local model server at {url}: {exc}\n"
                "Is it running? (e.g. 'ollama serve', or start LM Studio's local server)"
            ) from exc

        if resp.status_code in TRANSIENT_STATUS and attempt < MAX_ATTEMPTS:
            time.sleep(1.5 * attempt)
            continue
        if resp.status_code != 200:
            raise LocalLLMError(_friendly_error(resp, model))
        try:
            return resp.json()
        except ValueError as exc:
            raise LocalLLMError(
                f"The local model server returned a response that was not valid JSON: {resp.text[:300]}"
            ) from exc
    raise LocalLLMError(f"Could not reach the local model server after {MAX_ATTEMPTS} attempts: {last_exc}")


def _friendly_error(resp, model: str) -> str:
    try:
        detail = resp.json().get("error", {}).get("message", resp.text[:300])
    except ValueError:
        detail = resp.text[:300]
    if resp.status_code == 404:
        return (
            f"Local model server error (HTTP 404): {detail}. "
            f"Is '{model}' pulled/loaded? (e.g. 'ollama pull {model}')"
        )
    if resp.status_code in (401, 403):
        return f"Local model server rejected the request (HTTP {resp.status_code}): {detail}"
    return f"Local model server error (HTTP {resp.status_code}): {detail}"
