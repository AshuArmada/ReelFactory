"""Turns product facts into a spoken ad script using a local, OpenAI-compatible
LLM server (Ollama, LM Studio, llama.cpp server, etc).

Same idea as grok_script.py, just pointed at a local base URL instead of a
cloud API, and with no API key required. Prompt-building and response
validation are shared via ad_prompt.py so every provider writes to the same
brief and is held to the same shape.
"""
from __future__ import annotations

from . import ad_prompt, local_llm
from .config import Brand, Product
from .script import Segment

DEFAULT_MODEL = local_llm.DEFAULT_MODEL


def build(
    product: Product,
    brand: Brand,
    lang: str,
    model: str = DEFAULT_MODEL,
    base_url: str | None = None,
    api_key: str | None = None,
    steer: str = "",
) -> list[Segment]:
    """Return the ordered segments for one language, written by a local model."""
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

    url = local_llm.resolve_base_url(base_url)
    key = local_llm.resolve_key(api_key)
    prompt = ad_prompt.build_prompt(product, brand, lang, usps, steer)
    data = local_llm.chat_completion(
        model,
        messages=[{"role": "user", "content": prompt}],
        base_url=url,
        api_key=key,
        response_format={"type": "json_object"},
        temperature=0.9,
    )
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise local_llm.LocalLLMError(
            f"The local model returned no usable text. Raw response: {str(data)[:400]}"
        ) from exc

    segments = ad_prompt.parse_segments(text, error_cls=local_llm.LocalLLMError)
    ad_prompt.validate_segments(segments, usps, product, brand, lang, error_cls=local_llm.LocalLLMError)
    return segments
