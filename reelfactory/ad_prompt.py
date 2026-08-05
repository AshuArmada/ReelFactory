"""Shared prompt-building and response-parsing for LLM-written ad scripts.

Used by both ai_script.py (Gemini) and grok_script.py (Grok/xAI) so every
provider writes to the same brief and gets validated against the same shape.
Facts (price, warranty, USPs, phone...) always come from product.yaml /
brand.yaml -- the model is instructed to rephrase them, never invent new ones.
"""
from __future__ import annotations

import json
import re

from .config import Brand, Product
from .script import Segment

LANG_NAME = {"hi": "Hindi, written in Devanagari script", "en": "Indian English"}

TONE_NOTE = {
    "value": "a punchy, deal-focused tone that leads with the price/savings",
    "premium": "a confident, understated tone that leads with quality and craft",
    "trust": "a warm, reassuring tone that leans on reputation and reliability",
}

# Gemini's generationConfig.responseSchema uses this shape directly. Grok/xAI
# only takes a "json_object" response_format (no schema), so it relies on the
# same structure being spelled out in the prompt text instead.
GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["hook", "reveal", "usp", "proof", "price", "cta"],
                    },
                    "vo": {"type": "string"},
                    "overlay": {"type": "string"},
                },
                "required": ["role", "vo", "overlay"],
            },
        }
    },
    "required": ["segments"],
}


def build_prompt(product: Product, brand: Brand, lang: str, usps: list[str]) -> str:
    tone = product.tone if product.tone in TONE_NOTE else "value"
    facts = [f"- product name: {product.name(lang)}"]
    if product.price:
        facts.append(f"- price: {product.price}" + (f" (was {product.old_price})" if product.old_price else ""))
    for key, label in (("material", "material"), ("sizes", "sizes/variants"),
                        ("warranty", "warranty"), ("delivery", "delivery")):
        val = product.spec(key, lang)
        if val:
            facts.append(f"- {label}: {val}")
    contact = brand.phone or brand.whatsapp
    if contact:
        facts.append(f"- contact number to call out in the CTA: {contact}")
    if brand.city:
        facts.append(f"- city: {brand.city}")
    facts_block = "\n".join(facts)
    usp_block = "\n".join(f"{i+1}. {u}" for i, u in enumerate(usps))

    return f"""You are writing a 30-45 second vertical social video ad script for an Indian
small business, {brand.name}. The ad narrates over a slideshow of product photos.

Write entirely in {LANG_NAME.get(lang, lang)}. Use {TONE_NOTE[tone]}.

Use ONLY the facts below -- do not invent numbers, claims or features that
are not listed. You may rephrase them for punch, but never change them.

{facts_block}

Selling points to cover, one segment each, in this order (you may rephrase
each one but must keep its meaning and cover all {len(usps)} of them):
{usp_block}

Return ONLY a JSON object (no markdown fences, no commentary) with a
"segments" array in this exact order:
1. one "hook" segment -- an attention-grabbing opening line, does not mention the product name yet
2. one "reveal" segment -- introduces the product by name
3. exactly {len(usps)} "usp" segments -- one per selling point listed above, same order
4. one "proof" segment ONLY if warranty, delivery, sizes or material was given above -- a short reassurance line using one of those facts (omit this segment entirely if none of those facts were given)
5. one "price" segment ONLY if a price was given above (omit entirely if no price was given)
6. one "cta" segment -- a call to action; include the contact number verbatim if one was given above, otherwise just ask viewers to message/comment

Each segment is an object with exactly three string fields: "role" (one of
hook/reveal/usp/proof/price/cta), "vo" (the spoken line -- natural spoken
{LANG_NAME.get(lang, lang)}, 6-16 words, no emojis, no markdown, no quotation
marks) and "overlay" (a short on-screen caption for the same beat, at most 9
words, punchy, no trailing punctuation)."""


def parse_segments(text: str, error_cls=ValueError) -> list[Segment]:
    cleaned = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise error_cls(
            f"The script response was not valid JSON: {exc}\nRaw text: {cleaned[:400]}"
        ) from exc

    items = parsed.get("segments") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or not items:
        raise error_cls(f"The script response had no 'segments' array: {cleaned[:400]}")

    try:
        return [Segment(s["role"], s["vo"].strip(), s["overlay"].strip()) for s in items]
    except (KeyError, AttributeError, TypeError) as exc:
        raise error_cls(f"The script response had a malformed segment: {exc}") from exc


def validate_segments(segments: list[Segment], usps: list[str], product: Product, error_cls=ValueError) -> None:
    roles = [s.role for s in segments]
    n_usp = roles.count("usp")
    problems = []
    if roles.count("hook") != 1:
        problems.append("expected exactly 1 'hook' segment")
    if roles.count("reveal") != 1:
        problems.append("expected exactly 1 'reveal' segment")
    if n_usp != len(usps):
        problems.append(f"expected {len(usps)} 'usp' segments (one per product.yaml USP), got {n_usp}")
    if roles.count("cta") != 1:
        problems.append("expected exactly 1 'cta' segment")
    if product.price and roles.count("price") != 1:
        problems.append("product.yaml has a price but the script has no 'price' segment")
    if not any(s.vo.strip() for s in segments):
        problems.append("every segment came back empty")
    if problems:
        raise error_cls(
            f"{product.slug}: the AI script did not match the expected shape "
            f"({'; '.join(problems)}). Try again -- this is a generation, not a parsing, issue."
        )
