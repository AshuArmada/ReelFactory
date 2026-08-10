"""Shared prompt-building and response-parsing for LLM-written ad scripts.

Used by ai_script.py (Gemini), grok_script.py (Grok/xAI) and local_script.py
(a local model) so every provider writes to the same brief and gets validated
against the same shape. Facts (price, specs, USPs, phone...) always come from
product.yaml / brand.yaml -- the model is instructed to rephrase them, never to
invent new ones.

The shape of a script is not fixed: segment_plan() works out which beats this
particular video needs from its intent and from which facts were actually
filled in. build_prompt() turns that plan into instructions and
validate_segments() checks the response against the same plan, so the two can
never drift apart.
"""
from __future__ import annotations

import json
import re

from .config import Brand, INTENTS, NO_PRICE_INTENTS, OFFER_EARLY_INTENTS, Product
from .script import Segment

LANG_NAME = {"hi": "Hindi, written in Devanagari script", "en": "Indian English"}

TONE_NOTE = {
    "value": "a punchy, deal-focused tone that leads with the price/savings",
    "premium": "a confident, understated tone that leads with quality and craft",
    "trust": "a warm, reassuring tone that leans on reputation and reliability",
}

# How each intent should open and what it should lean on. The goal line itself
# comes from config.INTENTS, so the vocabulary stays in one place.
INTENT_GUIDE = {
    "sell": "Open on the problem the product solves. Close by asking for the order.",
    "offer": "Open on the saving itself -- the deal is the news. Repeat the deadline near the end.",
    "launch": "Open by signalling that this is new / just arrived. Build curiosity before the reveal.",
    "awareness": "Open with something relatable, not salesy. Explain what this is and who it is for. "
                 "Do not push a price or a hard sell; the aim is that people remember the name.",
    "footfall": "Open on a reason to come in person (see it, try it, get it fitted today). "
                "Make the location the point of the ending.",
    "enquiry": "Open on the custom / made-to-order nature of the work. Invite a question or a quote "
               "request rather than an instant purchase; a price here is a starting point, not final.",
    "restock": "Open by acknowledging it sold out or people were waiting. Stress that it is available again "
               "and may not last.",
    "educate": "Open with the useful thing you are about to explain, then teach it in the USP beats. "
               "Only mention the product as the answer near the end; keep the sell soft.",
    "festival": "Tie the opening to the occasion. Frame the product as the thing that makes the occasion better.",
}

ALL_ROLES = ["hook", "reveal", "offer", "usp", "proof", "price", "urgency", "cta"]

def response_schema(product: Product, brand: Brand, lang: str, usps: list[str]) -> dict:
    """Gemini's generationConfig.responseSchema, scoped to only the roles this
    particular video's plan actually uses -- not every role that exists.
    Restricting the enum this way (rather than always offering all of
    hook/reveal/offer/usp/proof/price/urgency/cta) measurably cuts down on the
    model adding a beat nobody asked for, e.g. an 'offer' segment on a video
    with no offer configured. Grok/xAI and local OpenAI-compatible servers
    only take a "json_object" response_format (no schema), so they rely on the
    same structure being spelled out in the prompt text instead."""
    roles = list(dict.fromkeys(step["role"] for step in segment_plan(product, brand, lang, usps)))
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "enum": roles},
                        "vo": {"type": "string"},
                        "overlay": {"type": "string"},
                    },
                    "required": ["role", "vo", "overlay"],
                },
            }
        },
        "required": ["segments"],
    }


# --------------------------------------------------------------------- the plan


def segment_plan(product: Product, brand: Brand, lang: str, usps: list[str]) -> list[dict]:
    """The beats this video needs, in order.

    Each entry is {role, count, required, note}. 'required' entries are enforced
    by validate_segments; the rest are asked for but tolerated if the model
    leaves them out.
    """
    intent = product.resolve_intent(brand)
    specs = product.spec_items(lang)
    proofs = product.lines("proof_points", lang)
    offer = product.text("offer", lang)
    deadline = product.text("offer_ends", lang)
    scarcity = product.text("urgency", lang)

    offer_beat = {
        "role": "offer", "count": 1, "required": True,
        "note": f"states the offer ({offer}) in plain words",
    } if offer else None

    plan: list[dict] = [
        {"role": "hook", "count": 1, "required": True,
         "note": "an attention-grabbing opening line; does not mention the product name yet"},
        {"role": "reveal", "count": 1, "required": True,
         "note": "introduces the product by name"},
    ]
    if offer_beat and intent in OFFER_EARLY_INTENTS:
        plan.append(offer_beat)
        offer_beat = None

    plan.append({
        "role": "usp", "count": len(usps), "required": True,
        "note": "one per selling point listed above, in the same order",
    })

    if specs or proofs:
        source = "the credibility points" if proofs else "one of the facts above"
        plan.append({"role": "proof", "count": 1, "required": False,
                     "note": f"a short reassurance line built from {source}"})
    if offer_beat:
        plan.append(offer_beat)
    if product.price and intent not in NO_PRICE_INTENTS:
        qualifier = " -- present it as a starting price, not a final quote" if intent == "enquiry" else ""
        plan.append({"role": "price", "count": 1, "required": True,
                     "note": f"quotes the price{qualifier}"})
    if deadline or scarcity:
        reason = " / ".join(x for x in (deadline, scarcity) if x)
        plan.append({"role": "urgency", "count": 1, "required": False,
                     "note": f"one line of genuine urgency, using only this: {reason}"})
    plan.append({"role": "cta", "count": 1, "required": True,
                 "note": _cta_note(product, brand, lang)})
    return plan


def _cta_note(product: Product, brand: Brand, lang: str) -> str:
    """What the closing line should ask for, given cta_action and the brand's
    actual contact details."""
    action = product.cta_action or "auto"
    detail = product.text("cta_detail", lang)
    contact = brand.phone or brand.whatsapp
    notes = {
        "call": f"asks viewers to phone {brand.phone or contact}" if contact else "asks viewers to phone the shop",
        "whatsapp": f"asks viewers to WhatsApp {brand.whatsapp or contact}" if contact
                    else "asks viewers to send a WhatsApp message",
        "visit": f"invites viewers to visit {brand.address or brand.city or 'the shop'}"
                 + (f" ({brand.hours})" if brand.hours else ""),
        "dm": f"asks viewers to DM {brand.instagram}" if brand.instagram else "asks viewers to send a direct message",
        "order_online": f"asks viewers to order at {brand.website}" if brand.website
                        else "asks viewers to order online",
        "book": "asks viewers to book a slot, appointment or site visit",
        "comment": "asks viewers to comment a single keyword below",
    }
    note = notes.get(action)
    if note is None:  # 'auto'
        note = (f"asks viewers to call or WhatsApp {contact}" if contact
                else "asks viewers to send a message or comment below")
    if action in ("call", "whatsapp", "auto") and contact:
        note += "; write the number exactly as given, do not reformat it"
    if detail:
        note += f"; also mention: {detail}"
    return note


# ------------------------------------------------------------------- the prompt


def build_prompt(product: Product, brand: Brand, lang: str, usps: list[str], steer: str = "") -> str:
    intent = product.resolve_intent(brand)
    tone = product.tone if product.tone in TONE_NOTE else "value"
    plan = segment_plan(product, brand, lang, usps)

    seconds = max(10, int(product.target_seconds or 35))
    words = int(seconds * 2.2)  # rough spoken-ad pace, both languages

    facts = [f"- product name: {product.name(lang)}"]
    category = product.category or brand.category
    if category:
        facts.append(f"- category: {category}")
    if product.price:
        facts.append(f"- price: {product.price}" + (f" (was {product.old_price})" if product.old_price else ""))
    for label, value in product.spec_items(lang).items():
        facts.append(f"- {label}: {value}")
    for key, label in (("offer", "offer"), ("offer_ends", "offer ends"), ("urgency", "urgency"),
                       ("occasion", "occasion")):
        val = product.text(key, lang)
        if val:
            facts.append(f"- {label}: {val}")
    for line in product.lines("proof_points", lang):
        facts.append(f"- credibility point: {line}")
    if brand.established:
        facts.append(f"- in business since: {brand.established}")
    contact = brand.phone or brand.whatsapp
    if contact:
        facts.append(f"- contact number to call out in the CTA: {contact}")
    for attr, label in (("address", "shop address"), ("hours", "opening hours"),
                        ("website", "website"), ("instagram", "instagram handle")):
        val = getattr(brand, attr, "")
        if val:
            facts.append(f"- {label}: {val}")
    if brand.city:
        facts.append(f"- city: {brand.city}")

    audience = product.text("audience", lang) or brand.audience
    usp_block = "\n".join(f"{i+1}. {u}" for i, u in enumerate(usps))
    plan_block = "\n".join(
        f"{i+1}. {_count_phrase(step)} \"{step['role']}\" segment{'s' if step['count'] != 1 else ''}"
        f" -- {step['note']}"
        for i, step in enumerate(plan)
    )

    lines = [
        f"You are writing a {seconds}-second vertical social video ad script for an Indian",
        f"small business, {brand.name}. The ad narrates over a slideshow of product photos.",
        "",
        f"GOAL OF THIS VIDEO: {INTENTS[intent]}.",
        INTENT_GUIDE[intent],
        "",
        f"Write entirely in {LANG_NAME.get(lang, lang)}. Use {TONE_NOTE[tone]}.",
    ]
    if audience:
        lines.append(f"Speak to this audience: {audience}.")
    lines += [
        f"Keep the whole script to roughly {words} words so it reads in about {seconds} seconds.",
        "",
        "Use ONLY the facts below -- do not invent numbers, claims or features that",
        "are not listed. You may rephrase them for punch, but never change them.",
        "",
        "\n".join(facts),
        "",
        f"Selling points to cover, one segment each, in this order (you may rephrase",
        f"each one but must keep its meaning and cover all {len(usps)} of them):",
        usp_block,
    ]

    must_say = product.lines("must_say", lang)
    if must_say:
        lines += ["", "These phrases must appear in the script, worked in naturally:",
                  "\n".join(f"- {m}" for m in must_say)]
    if product.avoid:
        lines += ["", "Never use these words, phrases or claims anywhere in the script:",
                  "\n".join(f"- {a}" for a in product.avoid)]

    # A rewrite note from the person at the keyboard. It goes last of the
    # content instructions so it reads as the most recent word on the subject,
    # but it deliberately sits *inside* the fact rules above -- "make it
    # cheaper sounding" must never license inventing a discount.
    if steer.strip():
        lines += [
            "",
            "REWRITE REQUEST -- an earlier draft was rejected. Write a fresh script",
            "that answers this note, while still obeying every rule above:",
            steer.strip(),
        ]

    plan_roles = list(dict.fromkeys(step["role"] for step in plan))
    lines += [
        "",
        "Return ONLY a JSON object (no markdown fences, no commentary) with a",
        "\"segments\" array holding exactly these segments, in this order --",
        "no more, no fewer, and no roles beyond the ones listed below:",
        plan_block,
        "",
        "Each segment is an object with exactly three string fields: \"role\" (one of",
        f"{'/'.join(plan_roles)} -- exactly as spelled, never numbered or suffixed,",
        f"even when a role repeats), \"vo\" (the spoken line -- natural spoken",
        f"{LANG_NAME.get(lang, lang)}, 6-16 words, no emojis, no markdown, no quotation",
        "marks) and \"overlay\" (a short on-screen caption for the same beat, at most 9",
        "words, punchy, no trailing punctuation).",
    ]
    return "\n".join(lines)


def _count_phrase(step: dict) -> str:
    if step["count"] == 1:
        return "one" if step["required"] else "one optional"
    return f"exactly {step['count']}"


# ------------------------------------------------------------------ the response


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
        return [Segment(_normalize_role(s["role"]), s["vo"].strip(), s["overlay"].strip()) for s in items]
    except (KeyError, AttributeError, TypeError) as exc:
        raise error_cls(f"The script response had a malformed segment: {exc}") from exc


def _normalize_role(role: str) -> str:
    """Smaller models sometimes disambiguate repeated roles by numbering them
    -- 'usp1', 'usp-2', 'usp_3' -- instead of repeating the bare role name as
    asked. Strip a trailing number so validation still recognises them."""
    role = str(role).strip()
    base = re.sub(r"[-_]?\d+$", "", role)
    return base if base in ALL_ROLES else role


def validate_segments(
    segments: list[Segment],
    usps: list[str],
    product: Product,
    brand: Brand | None = None,
    lang: str = "en",
    error_cls=ValueError,
) -> None:
    """Check the response against the same plan the prompt was built from."""
    plan = segment_plan(product, brand or Brand(), lang, usps)
    roles = [s.role for s in segments]
    problems = []
    for step in plan:
        got = roles.count(step["role"])
        if step["required"] and got != step["count"]:
            problems.append(
                f"expected {step['count']} '{step['role']}' segment(s), got {got}"
            )
        elif not step["required"] and got > step["count"]:
            problems.append(
                f"expected at most {step['count']} '{step['role']}' segment(s), got {got}"
            )
    planned = {step["role"] for step in plan}
    for extra in sorted(set(roles) - planned):
        problems.append(f"unexpected '{extra}' segment -- this video does not have that beat")
    if not any(s.vo.strip() for s in segments):
        problems.append("every segment came back empty")
    if problems:
        raise error_cls(
            f"{product.slug}: the AI script did not match the expected shape "
            f"({'; '.join(problems)}). Try again -- this is a generation, not a parsing, issue."
        )
