"""Turns product facts into a spoken ad script using the Grok (xAI) API.

Same idea as ai_script.py but for Grok's OpenAI-compatible chat completions
endpoint. Prompt-building and response validation are shared via ad_prompt.py
so both providers write to the same brief and are held to the same shape.
"""
from __future__ import annotations

from . import ad_prompt, grok
from .config import Brand, Product
from .script import Segment

DEFAULT_MODEL = "grok-4-latest"


def build(
    product: Product,
    brand: Brand,
    lang: str,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    base_url: str | None = None,
) -> list[Segment]:
    """Return the ordered segments for one language, written by Grok."""
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

    key = grok.resolve_key(api_key)
    url = grok.resolve_base_url(base_url)
    prompt = ad_prompt.build_prompt(product, brand, lang, usps)
    data = grok.chat_completion(
        model, key,
        messages=[{"role": "user", "content": prompt}],
        base_url=url,
        response_format={"type": "json_object"},
        temperature=0.9,
    )
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise grok.GrokError(
            f"Grok returned no usable text. Raw response: {str(data)[:400]}"
        ) from exc

    segments = ad_prompt.parse_segments(text, error_cls=grok.GrokError)
    ad_prompt.validate_segments(segments, usps, product, brand, lang, error_cls=grok.GrokError)
    return segments
