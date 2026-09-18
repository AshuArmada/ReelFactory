"""Shared HTTP plumbing for calling the Gemini API.

Used by both ai_script.py (text generation) and the 'gemini' TTS backend in
voice.py. Keys are never read from a yaml file -- only from environment
variables, a .env file next to brand.yaml, or explicit --gemini-key /
--gemini-backup-key overrides -- so they can't end up committed alongside a
client's brand.yaml.

A second, optional "backup" key can be configured. It is only ever used as an
automatic fallback when the primary key specifically hits a quota / rate
limit (HTTP 429) -- not for other kinds of failures, which would just fail
the same way again on a second key.
"""
from __future__ import annotations

import os
import math
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
ROOT = Path(__file__).resolve().parent.parent

# The TTS models in particular are prone to transient 500s and connection
# resets under load. Temporary quota errors also get a retry when Google
# supplies a short retry delay; daily/disabled quotas fail without waiting.
TRANSIENT_STATUS = {500, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_QUOTA_RETRY_DELAY = 120.0

# Accepted variable names inside .env, matched case-insensitively so people
# don't have to rename whatever they already wrote.
_ENV_KEY_NAMES = {"gemini_api_key", "gemini_key", "google_api_key", "google_gemini_api_key"}
_ENV_BACKUP_KEY_NAMES = {
    "key_backup", "gemini_key_backup", "gemini_api_key_backup",
    "backup_gemini_key", "gemini_backup_key",
}


class GeminiError(RuntimeError):
    pass


class GeminiQuotaError(GeminiError):
    """The request failed specifically because of a quota / rate limit (HTTP 429)."""


def resolve_key(explicit: str | None = None) -> str:
    key = explicit or os.environ.get("GEMINI_API_KEY") or _load_dotenv_key(_ENV_KEY_NAMES)
    if not key:
        raise GeminiError(
            "No Gemini API key found. Set the GEMINI_API_KEY environment variable "
            "(setx GEMINI_API_KEY \"...\" on Windows, then open a new terminal), "
            "add it to a .env file next to brand.yaml, or pass --gemini-key on the command line."
        )
    return key


def resolve_backup_key(explicit: str | None = None) -> str | None:
    """Optional -- returns None (never raises) when no backup key is configured."""
    return explicit or os.environ.get("GEMINI_API_KEY_BACKUP") or _load_dotenv_key(_ENV_BACKUP_KEY_NAMES)


def _load_dotenv_key(names: set) -> str | None:
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


def generate_content(
    model: str,
    api_key: str,
    payload: dict,
    timeout: int = 60,
    backup_key: str | None = None,
) -> dict:
    try:
        return _request(model, api_key, payload, timeout)
    except GeminiQuotaError:
        if not backup_key or backup_key == api_key:
            raise
        print("   primary Gemini key hit its quota, retrying with the backup key...", file=sys.stderr)
        try:
            return _request(model, backup_key, payload, timeout)
        except GeminiQuotaError as exc2:
            raise GeminiQuotaError(f"Both the primary and backup Gemini keys hit quota limits. {exc2}") from exc2


def _request(model: str, api_key: str, payload: dict, timeout: int) -> dict:
    url = f"{API_ROOT}/{model}:generateContent"
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # Keep credentials out of the URL: requests includes a prepared URL
            # in connection errors, which would otherwise surface the key in the
            # CLI or web UI when a request fails.
            resp = requests.post(
                url, headers={"x-goog-api-key": api_key}, json=payload, timeout=timeout
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(1.5 * attempt)
                continue
            raise GeminiError(
                f"Could not reach the Gemini API after {MAX_ATTEMPTS} attempts: {exc}"
            ) from exc

        if resp.status_code in TRANSIENT_STATUS and attempt < MAX_ATTEMPTS:
            time.sleep(1.5 * attempt)
            continue
        if resp.status_code == 429:
            delay = _quota_retry_delay(resp)
            if delay is not None and attempt < MAX_ATTEMPTS:
                # A small margin avoids retrying just before the quota resets.
                wait = delay + 1.0
                print(f"   Gemini rate limit: waiting {wait:.1f}s before retrying "
                      f"the same request ({attempt + 1}/{MAX_ATTEMPTS})...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise GeminiQuotaError(_friendly_error(resp))
        if resp.status_code != 200:
            raise GeminiError(_friendly_error(resp))
        try:
            return resp.json()
        except ValueError as exc:
            raise GeminiError(
                f"Gemini API returned a response that was not valid JSON: {resp.text[:300]}"
            ) from exc
    raise GeminiError(f"Could not reach the Gemini API after {MAX_ATTEMPTS} attempts: {last_exc}")


def _error_body(resp) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    error = data.get("error", data) if isinstance(data, dict) else data
    return error if isinstance(error, dict) else {"message": error} if isinstance(error, str) else {}


def _quota_retry_delay(resp) -> float | None:
    """Honor server retry hints without sleeping through daily/disabled quotas."""
    error = _error_body(resp)
    details = error.get("details", [])
    details = details if isinstance(details, list) else []
    message = str(error.get("message", ""))
    if re.search(r"per[\s_-]*day|daily|limit:\s*0\b", message, re.I):
        return None
    delays = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        violations = detail.get("violations", [])
        for violation in violations if isinstance(violations, list) else []:
            if not isinstance(violation, dict):
                continue
            quota = str(violation.get("quotaId", "")) + str(violation.get("quotaMetric", ""))
            if re.search(r"per[\s_-]*day|daily", quota, re.I) or str(violation.get("quotaValue")) == "0":
                return None
        if str(detail.get("@type", "")).endswith("google.rpc.RetryInfo"):
            delays.append(str(detail.get("retryDelay", "")).removesuffix("s"))

    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            delays.append(float(retry_after))
        except ValueError:
            try:
                when = parsedate_to_datetime(retry_after)
                delays.append(max(0.0, (when - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    match = re.search(r"retry in\s+([\d.]+)s", message, re.I)
    if match:
        delays.append(match.group(1))
    seconds = []
    for delay in delays:
        try:
            value = float(delay)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            seconds.append(value)
    wait = max(seconds) if seconds else None
    return wait if wait is not None and wait <= MAX_QUOTA_RETRY_DELAY else None


def _friendly_error(resp) -> str:
    detail = _error_body(resp).get("message") or resp.text[:300]
    if resp.status_code in (401, 403):
        return f"Gemini API rejected the key (HTTP {resp.status_code}): {detail}"
    if resp.status_code == 429:
        return (f"Gemini API rate limit or quota exceeded (HTTP 429): {detail}\n"
                "Check your project's remaining quota at https://ai.dev/rate-limit. "
                "For narration, you can choose Edge under Narration and build again. "
                "Another API key in the same project shares the same quota.")
    if resp.status_code == 404:
        return f"Gemini API model not found (HTTP 404): {detail}. Check the model name is correct."
    return f"Gemini API error (HTTP {resp.status_code}): {detail}"
