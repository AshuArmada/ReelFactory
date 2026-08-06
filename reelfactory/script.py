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

from .config import Brand, NO_PRICE_INTENTS, OFFER_EARLY_INTENTS, Product

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

# Intent-specific openings, used instead of the tone hooks when the intent
# gives us something more specific to say. 'sell' has none: it keeps the
# original tone-based hooks.
INTENT_HOOKS = {
    "hi": {
        "offer": ["रुकिए! ये ऑफ़र निकल जाएगा।", "इस कीमत पर दोबारा नहीं मिलेगा।"],
        "launch": ["नया आ गया है, और सबसे पहले आप देख रहे हैं।", "जिसका इंतज़ार था, वो आ गया।"],
        "awareness": ["एक चीज़ है जो हर घर में होनी चाहिए।", "क्या आपको ये पता है?"],
        "footfall": ["एक बार ख़ुद देखिए, फिर फ़ैसला कीजिए।", "आज दुकान पर आइए, फ़र्क़ ख़ुद दिखेगा।"],
        "enquiry": ["आपकी ज़रूरत के हिसाब से बनवाइए।", "हर नाप, हर डिज़ाइन में बन जाएगा।"],
        "restock": ["जो ख़त्म हो गया था, वो फिर आ गया है।", "आपका इंतज़ार ख़त्म, स्टॉक आ गया।"],
        "educate": ["ये छोटी सी बात बहुत काम आएगी।", "ख़रीदने से पहले ये जान लीजिए।"],
        "festival": ["{occasion} आ रहा है, तैयारी हो गई?", "इस {occasion} पर कुछ ख़ास कीजिए।"],
    },
    "en": {
        "offer": ["Hold on -- this deal will not wait.", "You will not see this price again."],
        "launch": ["It is here, and you are seeing it first.", "The one you have been waiting for."],
        "awareness": ["There is one thing every home needs.", "Here is something worth knowing."],
        "footfall": ["See it yourself, then decide.", "Come in today and spot the difference."],
        "enquiry": ["Get it made exactly the way you need.", "Any size, any design -- we build it."],
        "restock": ["The one that sold out is back.", "Your wait is over, it is back in stock."],
        "educate": ["This small tip will save you money.", "Know this before you buy."],
        "festival": ["{occasion} is coming. Are you ready?", "Make this {occasion} count."],
    },
}

OFFER = {
    "hi": ["और ऑफ़र? {offer}।", "साथ में {offer}।"],
    "en": ["And the offer? {offer}.", "Plus, {offer}."],
}

URGENCY = {
    "hi": {"ends": "ऑफ़र {offer_ends} तक ही।", "stock": "{urgency}।"},
    "en": {"ends": "Only till {offer_ends}.", "stock": "{urgency}."},
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
# Any spec that is not one of the four above, and any credibility line, is
# already written as a fact -- just speak it as its own sentence.
PROOF_GENERIC = {"hi": "{value}।", "en": "{value}."}

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

# One closing line per cta_action. Each entry names the context key it needs;
# when that key is empty we fall back to the plain phone/message CTA above so a
# half-filled brand.yaml can never produce "Visit us at ." on screen.
CTA_BY_ACTION = {
    "call": ("phone", {"hi": "अभी कॉल कीजिए {phone} पर।", "en": "Call us now on {phone}."}),
    "whatsapp": ("whatsapp", {"hi": "व्हाट्सएप कीजिए {whatsapp} पर।",
                               "en": "WhatsApp us on {whatsapp}."}),
    "visit": ("address", {"hi": "आज ही आइए — {address}।", "en": "Come and see us at {address}."}),
    "dm": (None, {"hi": "हमें डीएम कीजिए, तुरंत जवाब मिलेगा।", "en": "DM us and we will reply right away."}),
    "order_online": ("website", {"hi": "ऑर्डर कीजिए {website} पर।", "en": "Order online at {website}."}),
    "book": (None, {"hi": "अपना स्लॉट अभी बुक कीजिए।", "en": "Book your slot today."}),
    "comment": (None, {"hi": "नीचे कमेंट कीजिए, हम आपको बता देंगे।",
                        "en": "Comment below and we will get back to you."}),
}

OVERLAY = {
    "hi": {
        "price": "सिर्फ़ {price}", "cta": "अभी ऑर्डर करें", "cta_phone": "{phone}",
        "offer": "{offer}", "ends": "{offer_ends} तक",
        "visit": "दुकान पर आइए", "dm": "डीएम कीजिए", "book": "स्लॉट बुक करें",
        "comment": "नीचे कमेंट कीजिए", "order_online": "ऑनलाइन ऑर्डर करें",
    },
    "en": {
        "price": "Only {price}", "cta": "Order Now", "cta_phone": "{phone}",
        "offer": "{offer}", "ends": "Till {offer_ends}",
        "visit": "Visit Our Shop", "dm": "DM Us", "book": "Book Your Slot",
        "comment": "Comment Below", "order_online": "Order Online",
    },
}

CAPTION = {
    "hi": (
        "{name}\n\n{bullets}\n\n{offer_line}\n{price_line}"
        "\n\nग्राहक सेवा: {contact}\n\n{tags}"
    ),
    "en": ("{name}\n\n{bullets}\n\n{offer_line}\n{price_line}"
            "\n\nOrder / enquire: {contact}\n\n{tags}"),
}

# Fallback hashtags when no category is set. Deliberately generic -- a local
# business selling anything can post these.
BASE_TAGS = {
    "hi": ["#लोकलबिज़नेस", "#दुकान"],
    "en": ["#smallbusiness", "#shoplocal", "#madeinindia"],
}

# Category -> hashtags, so a restaurant does not get furniture tags. The key is
# matched loosely (substring, case-insensitive) against product/brand category,
# so "steel furniture" still finds "furniture".
CATEGORY_TAGS = {
    "furniture": ["#furniture", "#homedecor", "#interiordesign"],
    "storage": ["#homestorage", "#organisation", "#homedecor"],
    "hardware": ["#hardware", "#construction", "#building"],
    "steel": ["#steel", "#fabrication", "#construction"],
    "roofing": ["#roofing", "#construction", "#homebuilding"],
    "food": ["#foodie", "#homemade", "#foodlover"],
    "restaurant": ["#restaurant", "#foodie", "#dineout"],
    "sweets": ["#sweets", "#mithai", "#festivetreats"],
    "apparel": ["#fashion", "#ootd", "#style"],
    "saree": ["#saree", "#ethnicwear", "#indianfashion"],
    "jewellery": ["#jewellery", "#jewelry", "#accessories"],
    "electronics": ["#electronics", "#gadgets", "#tech"],
    "mobile": ["#smartphone", "#mobiles", "#gadgets"],
    "beauty": ["#beauty", "#skincare", "#selfcare"],
    "salon": ["#salon", "#hairstyle", "#makeover"],
    "fitness": ["#fitness", "#gym", "#healthylifestyle"],
    "education": ["#education", "#coaching", "#students"],
    "coaching": ["#coaching", "#exampreparation", "#students"],
    "realestate": ["#realestate", "#property", "#dreamhome"],
    "automobile": ["#cars", "#automobile", "#autolove"],
    "services": ["#services", "#localservices", "#trustedservice"],
    "medical": ["#healthcare", "#clinic", "#health"],
    "agriculture": ["#farming", "#agriculture", "#kisan"],
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
    intent = product.resolve_intent(brand)
    tone = product.tone if product.tone in HOOKS[lang] else "value"
    segs: list[Segment] = []

    hook = _fmt(rng.choice(_hook_pool(intent, tone, lang, ctx)), ctx)
    segs.append(Segment("hook", hook, _shorten(hook, 9)))

    reveal = _fmt(rng.choice(REVEALS[lang]), ctx)
    segs.append(Segment("reveal", reveal, product.name(lang)))

    # The deal is the news for some intents, a sweetener for the rest, so it
    # either goes straight after the reveal or down next to the price.
    offer_seg = None
    if product.text("offer", lang):
        offer_line = _fmt(rng.choice(OFFER[lang]), ctx)
        offer_seg = Segment("offer", offer_line, _fmt(OVERLAY[lang]["offer"], ctx))
    if offer_seg and intent in OFFER_EARLY_INTENTS:
        segs.append(offer_seg)
        offer_seg = None

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

    if offer_seg:
        segs.append(offer_seg)

    if product.price and intent not in NO_PRICE_INTENTS:
        tmpl = PRICE_DROP[lang] if product.old_price else PRICE[lang]
        line = _fmt(rng.choice(tmpl), ctx)
        segs.append(Segment("price", line, _fmt(OVERLAY[lang]["price"], ctx)))

    ends, scarcity = product.text("offer_ends", lang), product.text("urgency", lang)
    if ends or scarcity:
        line = _fmt(URGENCY[lang]["ends" if ends else "stock"], ctx)
        badge = _fmt(OVERLAY[lang]["ends"], ctx) if ends else _shorten(line)
        segs.append(Segment("urgency", line, badge))

    cta, badge = _cta_line(product, brand, lang, ctx, rng)
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
    offer_text, ends = product.text("offer", lang), product.text("offer_ends", lang)
    offer_line = f"🎁 {offer_text}" if offer_text else ""
    if offer_line and ends:
        offer_line += f" — {ends}"
    contact_parts = [brand.phone, brand.whatsapp, brand.website]
    if product.cta_action == "visit" and brand.address:
        contact_parts.insert(0, brand.address)
    contact = " | ".join(dict.fromkeys(x for x in contact_parts if x))
    text = CAPTION[lang].format(
        name=product.name(lang),
        bullets=bullets,
        offer_line=offer_line,
        price_line=price_line,
        contact=contact or brand.name,
        tags=" ".join(_hashtags(product, brand, lang)),
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- helpers


def _hook_pool(intent: str, tone: str, lang: str, ctx: dict) -> list:
    """Intent-specific openings where we have them, tone-based ones otherwise."""
    pool = INTENT_HOOKS[lang].get(intent)
    if intent == "festival" and not ctx.get("occasion"):
        pool = None  # "{occasion} is coming" with no occasion set reads broken
    return pool or HOOKS[lang][tone]


def _cta_line(product: Product, brand: Brand, lang: str, ctx: dict, rng) -> tuple[str, str]:
    """The closing line and its on-screen badge, from cta_action."""
    action = product.cta_action or "auto"
    entry = CTA_BY_ACTION.get(action)
    # Fall back to the plain phone/message CTA when the detail this action needs
    # (an address, a website...) was never filled in.
    if entry and (entry[0] is None or ctx.get(entry[0])):
        line = _fmt(entry[1][lang], ctx)
        detail = product.text("cta_detail", lang)
        if detail:
            line = f"{line} {detail}"
        badge_key = {"call": "cta_phone", "whatsapp": "cta_phone"}.get(action, action)
        badge = _fmt(OVERLAY[lang].get(badge_key, OVERLAY[lang]["cta"]), ctx)
        return line, badge
    if brand.phone or brand.whatsapp:
        return _fmt(rng.choice(CTA[lang]), ctx), _fmt(OVERLAY[lang]["cta_phone"], ctx)
    return _fmt(rng.choice(CTA_NO_PHONE[lang]), ctx), OVERLAY[lang]["cta"]


def _proof_line(product: Product, lang: str) -> str:
    """One line of reassurance, using whichever fact the product actually has."""
    # A credibility point is already written as a finished claim, so it beats
    # anything we could assemble from a spec.
    proofs = product.lines("proof_points", lang)
    if proofs:
        return PROOF_GENERIC[lang].replace("{value}", str(proofs[0]).strip(" ।.!?"))
    facts = PROOF[lang]
    for key in ("warranty", "delivery", "sizes", "material"):
        val = product.spec(key, lang)
        if val:
            return facts[key].replace("{" + key + "}", str(val))
    # Free-form specs have no template to slot into, so speak the value on its
    # own -- but only when it is already a phrase. "40" (from seats: 40) means
    # nothing spoken aloud, while "Free delivery within 10 km" reads perfectly.
    for value in product.spec_items(lang).values():
        if len(value.split()) >= 3:
            return PROOF_GENERIC[lang].replace("{value}", value.strip(" ।.!?"))
    return ""


def _hashtags(product: Product, brand: Brand, lang: str) -> list:
    """The product's own tags first, then category tags, generic tags and the
    city -- deduped and capped so the caption does not turn into tag soup."""
    category = (product.category or brand.category or "").lower()
    matched = [t for key, tags in CATEGORY_TAGS.items() if key in category for t in tags]
    tags = list(product.hashtags) + matched + BASE_TAGS[lang]
    if brand.city:
        slug = re.sub(r"[^a-z0-9]+", "", brand.city.lower())
        if slug:
            tags.append(f"#{slug}")
    return list(dict.fromkeys(tags))[:12]


def _context(product: Product, brand: Brand, lang: str = "en") -> dict:
    return {
        "name": product.name(lang),
        "price": product.price,
        "old_price": product.old_price,
        "material": product.spec("material", lang),
        "sizes": product.spec("sizes", lang),
        "warranty": product.spec("warranty", lang),
        "delivery": product.spec("delivery", lang),
        "offer": product.text("offer", lang),
        "offer_ends": product.text("offer_ends", lang),
        "urgency": product.text("urgency", lang),
        "occasion": product.text("occasion", lang),
        "brand": brand.name,
        "city": brand.city or "",
        "phone": brand.phone or brand.whatsapp,
        "whatsapp": brand.whatsapp or brand.phone,
        "address": brand.address or "",
        "hours": brand.hours or "",
        "website": brand.website or "",
        "instagram": brand.instagram or "",
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
