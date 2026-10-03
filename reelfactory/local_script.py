"""Turns product facts into a spoken ad script using a local, OpenAI-compatible
LLM server (Ollama, LM Studio, llama.cpp server, etc).

Uses a local base URL with no API key required. Prompt-building and response
validation are shared via ad_prompt.py so every provider writes to the same
brief and is held to the same shape.
"""
from __future__ import annotations

from . import ad_prompt, local_llm
from .config import Brand, Product
from .script import Segment

DEFAULT_MODEL = local_llm.DEFAULT_MODEL


def _scene_schema(roles):
    """Named required slots enforce both count and role at each position.

    An array of objects with a role enum only restricts role spelling; it
    cannot stop a small model from returning seven or twelve USP scenes.
    Use basic object/required/enum constraints supported by local servers.
    """
    slots = {}
    for i, role in enumerate(roles, 1):
        slots[f"scene_{i:03}"] = {
            "type": "object", "additionalProperties": False,
            "properties": {"role": {"type": "string", "enum": [role]},
                           "vo": {"type": "string"}, "overlay": {"type": "string"}},
            "required": ["role", "vo", "overlay"],
        }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"segments": {"type": "object", "properties": slots,
                                     "required": list(slots), "additionalProperties": False}},
        "required": ["segments"],
    }


def _scene_output_prompt(prompt, roles):
    slots = "\n".join(f"- scene_{i:03}: role={role}" for i, role in enumerate(roles, 1))
    return (
        prompt + "\n\nLOCAL STRUCTURED OUTPUT FORMAT: For this request, encode segments as a JSON "
        "OBJECT keyed by the scene slots below, instead of an array. This changes only the JSON "
        "container, not the original facts, topics, order, language or rewrite instructions. "
        "Each slot holds one object with role, vo and overlay strings. Return every slot exactly "
        "once with its specified role. Do not add other slots or put multiple scenes in one slot.\n"
        + slots
        + '\nExample of the container only: {"segments":{"scene_001":{"role":"'
        + roles[0] + '","vo":"...","overlay":"..."}}}. Fill ALL listed slots, not just the example.'
    )


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
    roles = [step["role"] for step in ad_prompt.segment_plan(product, brand, lang, usps)
             for _ in range(step["count"])]

    def call_model(prompt_text: str, scene_roles=None) -> str:
        requested_roles = roles if scene_roles is None else scene_roles
        data = local_llm.chat_completion(
            model,
            messages=[{"role": "user", "content": _scene_output_prompt(prompt_text, requested_roles)}],
            base_url=url,
            api_key=key,
            response_format={"type": "json_schema", "json_schema": {
                "name": "reel_script", "strict": True, "schema": _scene_schema(requested_roles),
            }},
            temperature=0.9,
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise local_llm.LocalLLMError(
                f"The local model returned no usable text. Raw response: {str(data)[:400]}"
            ) from exc

    return ad_prompt.write_with_length_retry(
        product, brand, lang, usps, steer, call_model, error_cls=local_llm.LocalLLMError,
        edit_model=call_model,
    )
