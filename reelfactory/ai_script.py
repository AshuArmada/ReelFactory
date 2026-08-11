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
    steer: str = "",
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
    schema = ad_prompt.response_schema(product, brand, lang, usps)

    def call_model(prompt_text: str) -> str:
        payload = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "temperature": 0.9,
            },
        }
        data = gemini.generate_content(model, key, payload, backup_key=backup)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise gemini.GeminiError(
                f"Gemini returned no usable text. Raw response: {str(data)[:400]}"
            ) from exc

    return ad_prompt.write_with_length_retry(
        product, brand, lang, usps, steer, call_model, error_cls=gemini.GeminiError,
    )
