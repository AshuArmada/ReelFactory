"""Turns product facts into a spoken ad script using the Gemini API.

An alternative to script.py's offline templates. Same output shape (a list of
Segment) so it drops into the existing voice/subtitle/render pipeline
unchanged. Prompt-building and response validation are shared with
grok_script.py via ad_prompt.py.
"""
from __future__ import annotations

from . import ad_prompt, gemini
from .config import Brand, Product
from .script import Segment

DEFAULT_MODEL = "gemini-2.5-flash"


def build(
    product: Product,
    brand: Brand,
    lang: str,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    backup_key: str | None = None,
) -> list[Segment]:
    """Return the ordered segments for one language, written by Gemini."""
    override = product.script_override(lang)
    if override:
        ov_text = product.overlay_override(lang)
        return [
            Segment("custom", line, ov_text[i] if i < len(ov_text) else line[:40])
            for i, line in enumerate(override)
        ]

    usps = product.usps(lang)
    if not usps:
        raise ValueError(
            f"{product.slug}: add at least one selling point under 'usp_{lang}' in product.yaml."
        )

    key = gemini.resolve_key(api_key)
    backup = gemini.resolve_backup_key(backup_key)
    payload = {
        "contents": [{"parts": [{"text": ad_prompt.build_prompt(product, brand, lang, usps)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": ad_prompt.response_schema(product, brand, lang, usps),
            "temperature": 0.9,
        },
    }
    data = gemini.generate_content(model, key, payload, backup_key=backup)
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise gemini.GeminiError(
            f"Gemini returned no usable text. Raw response: {str(data)[:400]}"
        ) from exc

    segments = ad_prompt.parse_segments(text, error_cls=gemini.GeminiError)
    ad_prompt.validate_segments(segments, usps, product, brand, lang, error_cls=gemini.GeminiError)
    return segments
