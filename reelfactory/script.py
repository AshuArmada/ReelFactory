"""Turns product facts into a spoken ad script, on-screen text, and a Facebook caption.

No network and no LLM at runtime: copy comes from tone-specific templates, and a
per-product seed keeps repeat builds identical while making different products
sound different.
"""
from __future__ import annotations

import random
import re
import zlib
from dataclasses import dataclass

from .config import Brand, Product

LANGS = ("hi", "en")


@dataclass
class Segment:
    role: str        # hook | reveal | usp | proof | price | cta
    vo: str          # what the voice says
    overlay: str     # short text burned on screen


# --------------------------------------------------------------------------- copy

HOOKS = {
    "hi": {
        "value": [
            "क्या घर में सामान रखने की जगह कम पड़ रही है?",
            "सस्ता रैक खरीदा और साल भर में ही झुक गया?",
            "दुकान हो या घर, सामान इधर-उधर फैला रहता है?",
        ],
        "premium": [
            "जो एक बार लगे, वो सालों साल चले।",
            "मज़बूती ऐसी कि देखते ही फ़र्क़ पता चले।",
        ],
        "trust": [
            "हज़ारों घरों में लगा हुआ, और आज भी उतना ही मज़बूत।",
            "जिस पर {city} के लोग आँख मूँदकर भरोसा करते हैं।",
        ],
    },
    "en": {
        "value": [
            "Running out of space to store your things?",
            "Bought a cheap rack and watched it bend in a year?",
            "Shop or home, is your stuff lying everywhere?",
        ],
        "premium": [
            "Fit it once, and forget about it for years.",
            "Built so solid, you can tell just by looking at it.",
        ],
        "trust": [
            "Already standing strong in thousands of homes.",
            "The name people in {city} trust with their eyes closed.",
        ],
    },
}

REVEALS = {
    "hi": ["पेश है {name}, {brand} की तरफ़ से।", "इसका हल है {name}।"],
    "en": ["Meet the {name}, from {brand}.", "Here is your answer, the {name}."],
}

USP_LEAD = {
    "hi": ["", "साथ में, ", "और सबसे ख़ास बात, ", "इतना ही नहीं, "],
    "en": ["", "Plus, ", "And the best part, ", "On top of that, "],
}

PRICE = {
    "hi": ["और ये सब सिर्फ़ {price} में।", "कीमत? सिर्फ़ {price}।"],
    "en": ["And all of this for just {price}.", "The price? Only {price}."],
}
PRICE_DROP = {
    "hi": ["{old_price} वाला, अब सिर्फ़ {price} में।"],
    "en": ["Was {old_price}. Now just {price}."],
}

PROOF = {
    "hi": {
        "warranty": "{warranty} की वारंटी के साथ।",
        "delivery": "{delivery}।",
        "sizes": "साइज़ मिलेंगे {sizes}।",
        "material": "बना है {material} से।",
    },
    "en": {
        "warranty": "Backed by a {warranty} warranty.",
        "delivery": "{delivery}.",
        "sizes": "Available in {sizes}.",
        "material": "Made from {material}.",
    },
}

CTA = {
    "hi": [
        "अभी ऑर्डर करें। कॉल या व्हाट्सएप कीजिए {phone} पर।",
        "आज ही मंगवाइए, {phone} पर व्हाट्सएप करें।",
    ],
    "en": [
        "Order now. Call or WhatsApp {phone}.",
        "Get yours today. WhatsApp us on {phone}.",
    ],
}
CTA_NO_PHONE = {
    "hi": ["अभी ऑर्डर करें, नीचे मैसेज कीजिए।"],
    "en": ["Order now. Just send us a message below."],
}

OVERLAY = {
    "hi": {"price": "सिर्फ़ {price}", "cta": "अभी ऑर्डर करें", "cta_phone": "{phone}"},
    "en": {"price": "Only {price}", "cta": "Order Now", "cta_phone": "{phone}"},
}

CAPTION = {
    "hi": (
        "{name}\n\n{bullets}\n\n{price_line}"
        "\n\nग्राहक सेवा: {contact}\n\n{tags}"
    ),
    "en": ("{name}\n\n{bullets}\n\n{price_line}\n\nOrder / enquire: {contact}\n\n{tags}"),
}

BASE_TAGS = {
    "hi": ["#घरसजावट", "#फर्नीचर", "#स्टोरेज"],
    "en": ["#homestorage", "#furniture", "#interiordesign", "#homedecor"],
}


# --------------------------------------------------------------------------- engine


def build(product: Product, brand: Brand, lang: str) -> list[Segment]:
    """Return the ordered segments for one language."""
    if lang not in LANGS:
        raise ValueError(f"Unsupported language {lang!r}; expected one of {LANGS}.")

    override = product.script_override(lang)
    ov_text = product.overlay_override(lang)
    if override:
        return [
            Segment("custom", line, ov_text[i] if i < len(ov_text) else _shorten(line))
            for i, line in enumerate(override)
        ]

    rng = random.Random(
        product.seed
        if product.seed is not None
        else zlib.crc32(product.slug.encode("utf-8"))
    )
    ctx = _context(product, brand, lang)
    tone = product.tone if product.tone in HOOKS[lang] else "value"
    segs: list[Segment] = []

    hook = _fmt(rng.choice(HOOKS[lang][tone]), ctx)
    segs.append(Segment("hook", hook, _shorten(hook, 9)))

    reveal = _fmt(rng.choice(REVEALS[lang]), ctx)
    segs.append(Segment("reveal", reveal, product.name(lang)))

    usps = product.usps(lang)
    if not usps:
        raise ValueError(
            f"{product.slug}: add at least one selling point under 'usp_{lang}' in product.yaml."
        )
    leads = USP_LEAD[lang]
    for i, usp in enumerate(usps):
        lead = leads[i % len(leads)] if i else ""
        segs.append(Segment("usp", _join(lead, usp), _shorten(usp)))

    proof = _proof_line(product, lang)
    if proof:
        segs.append(Segment("proof", proof, _shorten(proof)))

    if product.price:
        tmpl = PRICE_DROP[lang] if product.old_price else PRICE[lang]
        line = _fmt(rng.choice(tmpl), ctx)
        segs.append(Segment("price", line, _fmt(OVERLAY[lang]["price"], ctx)))

    if brand.phone or brand.whatsapp:
        cta = _fmt(rng.choice(CTA[lang]), ctx)
        badge = _fmt(OVERLAY[lang]["cta_phone"], ctx)
    else:
        cta = _fmt(rng.choice(CTA_NO_PHONE[lang]), ctx)
        badge = OVERLAY[lang]["cta"]
    segs.append(Segment("cta", cta, badge))
    return segs


def caption(product: Product, brand: Brand, lang: str) -> str:
    """Facebook post caption with hashtags."""
    usps = product.usps(lang) or product.usps("en")
    bullets = "\n".join(f"✓ {u}" for u in usps)
    if product.price and product.old_price:
        price_line = f"{product.old_price} → {product.price}"
    elif product.price:
        price_line = product.price
    else:
        price_line = ""
    contact = " | ".join(dict.fromkeys(x for x in (brand.phone, brand.whatsapp, brand.website) if x))
    tags = " ".join(dict.fromkeys(list(product.hashtags) + BASE_TAGS[lang]))
    text = CAPTION[lang].format(
        name=product.name(lang),
        bullets=bullets,
        price_line=price_line,
        contact=contact or brand.name,
        tags=tags,
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- helpers


def _proof_line(product: Product, lang: str) -> str:
    """One line of reassurance, using whichever fact the product actually has."""
    facts = PROOF[lang]
    for key in ("warranty", "delivery", "sizes", "material"):
        val = product.spec(key, lang)
        if val:
            return facts[key].replace("{" + key + "}", str(val))
    return ""


def _context(product: Product, brand: Brand, lang: str = "en") -> dict:
    return {
        "name": product.name(lang),
        "price": product.price,
        "old_price": product.old_price,
        "material": product.spec("material", lang),
        "sizes": product.spec("sizes", lang),
        "warranty": product.spec("warranty", lang),
        "delivery": product.spec("delivery", lang),
        "brand": brand.name,
        "city": brand.city or "",
        "phone": brand.phone or brand.whatsapp,
    }


def _fmt(template: str, ctx: dict) -> str:
    out = template
    for key, val in ctx.items():
        out = out.replace("{" + key + "}", str(val))
    return re.sub(r"\s{2,}", " ", out).strip()


def _join(lead: str, body: str) -> str:
    if not lead:
        return body
    return lead + body[0].lower() + body[1:] if body[:1].isascii() else lead + body


def _shorten(text: str, keep: int = 7) -> str:
    """Trim to a length that still reads in a second, cutting on a word break."""
    clean = re.sub(r"[।.!?]+$", "", text.strip())
    clean = re.sub(r"^(साथ में|और सबसे ख़ास बात|इतना ही नहीं|Plus|And the best part|On top of that)[,\s]+",
                   "", clean, flags=re.IGNORECASE)
    parts = clean.split()
    if len(parts) <= keep + 2:
        return clean
    return " ".join(parts[:keep])


def name_for(product: Product, lang: str) -> str:
    return product.name(lang)
