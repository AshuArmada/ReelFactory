"""Inception script writer using the shared advertising brief."""
from __future__ import annotations

import os
import time
import requests

from . import ad_prompt, local_llm
from .script import Segment

PROVIDERS = {
    "inception": ("Inception", "https://api.inceptionlabs.ai/v1", "mercury-2.5"),
}


class HostedScriptError(RuntimeError):
    pass


def setting(provider, suffix, default=""):
    name = f"{provider.upper()}_{suffix}"
    return os.environ.get(name) or local_llm._load_dotenv({name.lower()}) or default


def resolve_key(provider):
    key = setting(provider, "API_KEY")
    if not key:
        raise HostedScriptError(f"Set {provider.upper()}_API_KEY in .env to use {PROVIDERS[provider][0]}.")
    return key


def completion(provider, prompt):
    label, base, default_model = PROVIDERS[provider]
    key = resolve_key(provider)
    model = setting(provider, "MODEL", default_model)
    url = setting(provider, "BASE_URL", base).rstrip("/") + "/chat/completions"
    payload = {
        "model": model, "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.75, "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(3):
        try:
            response = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                                     json=payload, timeout=120, allow_redirects=False)
        except requests.RequestException:
            if attempt < 2:
                time.sleep(attempt + 1)
                continue
            raise HostedScriptError(f"Could not reach {label}. Check your connection and {provider.upper()}_BASE_URL.") from None
        if response.status_code in (500, 502, 503, 504) and attempt < 2:
            time.sleep(attempt + 1)
            continue
        if response.status_code != 200:
            hints = {
                400: "Check the model name and request settings.",
                401: "The API key was rejected. Check the key in .env.",
                403: "The account does not have access to this model.",
                402: "Check the account's available credits or billing.",
                404: "Check the model name and API base URL in .env.",
                429: "Rate limit or quota reached. Check the account limits and retry later.",
            }
            # Never include raw provider responses or request headers in errors.
            raise HostedScriptError(f"{label} request failed (HTTP {response.status_code}). "
                                    + hints.get(response.status_code, "Try again later."))
        try:
            result = response.json()
            choice = result["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") == "length":
                raise HostedScriptError(f"{label} reached its output limit. Shorten the brief or use fewer products.")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty content")
            return content
        except (ValueError, KeyError, IndexError, TypeError):
            raise HostedScriptError(f"{label} returned no usable script text. Please retry.") from None


def build(product, brand, lang, provider, steer=""):
    override = product.script_override(lang)
    if override:
        overlays = product.overlay_override(lang)
        return [Segment("custom", line, overlays[i] if i < len(overlays) else line[:40])
                for i, line in enumerate(override)]
    usps = product.usps(lang)
    if not usps:
        raise ValueError(f"{product.slug}: add at least one selling point under 'usp_{lang}'.")
    resolve_key(provider)
    return ad_prompt.write_with_length_retry(
        product, brand, lang, usps, steer,
        lambda prompt: completion(provider, prompt), error_cls=HostedScriptError,
    )
