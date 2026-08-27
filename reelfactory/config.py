"""Load and validate brand + product configuration."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# What this particular video is trying to achieve. Shapes the whole script --
# which beats appear, what the hook leans on, how it closes -- so it matters
# more than 'tone', which only changes the wording.
INTENTS = {
    "sell": "straight product sell: benefits, price, order now",
    "offer": "push a discount or deal -- lead with the saving and the deadline",
    "launch": "announce something new that has just arrived",
    "awareness": "introduce it to people who have never heard of it; no hard price push",
    "footfall": "get people to come to the shop or showroom in person",
    "enquiry": "collect enquiries or quote requests, for made-to-order or service work",
    "restock": "tell people a popular item is back in stock",
    "educate": "explain or demonstrate something useful, and sell softly at the end",
    "festival": "tie it to a festival, wedding season or other occasion",
}

# How the video asks the viewer to act. 'auto' keeps the old behaviour: call or
# WhatsApp when a number is set, otherwise ask for a message.
CTA_ACTIONS = {
    "auto": "call or WhatsApp if a number is set, otherwise ask for a message",
    "call": "phone the shop",
    "whatsapp": "send a WhatsApp message",
    "visit": "come to the shop or showroom in person",
    "dm": "send a direct message on the platform",
    "order_online": "order from the website",
    "book": "book a slot, appointment or site visit",
    "comment": "comment a keyword below",
}

TONES = ("value", "premium", "trust")

# Intents where quoting a price works against the goal, even when product.yaml
# has one, and intents where the deal is the news so it belongs up front.
NO_PRICE_INTENTS = {"awareness", "educate"}
OFFER_EARLY_INTENTS = {"offer", "festival", "restock"}

# The four specs this tool has always had a dedicated field for, and the label
# each one is given when the facts are handed to an LLM. Anything else goes in
# the free-form 'specs' mapping, which is what makes this work for businesses
# that are not selling hardware.
LEGACY_SPEC_LABELS = {
    "material": "material",
    "sizes": "sizes/variants",
    "warranty": "warranty",
    "delivery": "delivery",
}


@dataclass
class Brand:
    name: str = "Your Brand"
    tagline_en: str = ""
    tagline_hi: str = ""
    phone: str = ""
    whatsapp: str = ""
    website: str = ""
    city: str = ""
    address: str = ""        # used by the 'visit' / footfall call to action
    hours: str = ""          # e.g. "10am - 8pm, closed Sunday"
    instagram: str = ""      # handle, for the 'dm' call to action
    established: str = ""    # e.g. "1998" -- becomes a credibility line
    logo: str | None = None
    primary_color: str = "#E4572E"
    secondary_color: str = "#0B0B0F"
    text_color: str = "#FFFFFF"
    music: str | None = None
    music_volume: float = 0.12
    watermark: bool = True
    font_en: str | None = None
    font_hi: str | None = None
    voice_hi: str = "hi-IN-MadhurNeural"
    voice_en: str = "en-IN-PrabhatNeural"
    rate_hi: str = "+8%"
    rate_en: str = "+6%"

    # Defaults for every product, so a shop that always sells the same kind of
    # thing to the same people does not repeat itself in every product.yaml.
    # A product that sets its own value always wins.
    category: str = ""            # e.g. "furniture", "restaurant", "coaching"
    audience: str = ""            # e.g. "shop owners and warehouse managers"
    default_intent: str = "sell"  # any key of INTENTS

    # Only used with --script ai / --tts gemini. The API key itself is never
    # read from here -- only from GEMINI_API_KEY or --gemini-key -- so it
    # can't end up committed alongside this file.
    gemini_script_model: str = "gemini-2.5-flash"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_voice: str = "Kore"

    # Only used with --script grok. The API key itself is never read from
    # here -- only from GROK_API_KEY or --grok-key.
    grok_script_model: str = "grok-4-latest"

    # Only used with --script local. Points at a local, OpenAI-compatible
    # server (Ollama, LM Studio, llama.cpp server, ...) -- no cloud account,
    # no API key, no data leaving the machine. No key needed by default; see
    # --local-key / LOCAL_LLM_API_KEY if your local server requires one.
    local_script_model: str = "llama3.2:3b"
    local_base_url: str = "http://localhost:11434/v1"

    @staticmethod
    def load(path) -> "Brand":
        data = _read_yaml(path)
        known = set(Brand.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"{path}: unknown setting(s) {sorted(unknown)}. Valid: {sorted(known)}")
        b = Brand(**data)
        base = Path(path).parent
        for attr in ("logo", "music"):
            v = getattr(b, attr)
            if v:
                p = Path(v) if os.path.isabs(v) else (base / v)
                if not p.exists():
                    raise FileNotFoundError(f"brand.{attr} points at a missing file: {p}")
                setattr(b, attr, str(p.resolve()))
        return b

    def tagline(self, lang: str) -> str:
        return (self.tagline_hi if lang == "hi" else self.tagline_en) or ""

    def voice(self, lang: str) -> str:
        return self.voice_hi if lang == "hi" else self.voice_en

    def rate(self, lang: str) -> str:
        return self.rate_hi if lang == "hi" else self.rate_en


@dataclass
class Product:
    slug: str
    dir: Path
    name_en: str
    name_hi: str
    photos: list
    price: str = ""
    old_price: str = ""
    material: str = ""
    sizes: str = ""
    warranty: str = ""
    delivery: str = ""
    material_hi: str = ""
    sizes_hi: str = ""
    warranty_hi: str = ""
    delivery_hi: str = ""
    usp_en: list = field(default_factory=list)
    usp_hi: list = field(default_factory=list)
    script_en: list = field(default_factory=list)
    script_hi: list = field(default_factory=list)
    overlay_en: list = field(default_factory=list)
    overlay_hi: list = field(default_factory=list)
    hashtags: list = field(default_factory=list)
    tone: str = "value"
    seed: int | None = None
    # Filenames in the order they should appear on screen. Photos on disk but
    # missing from this list follow it, in natural filename order, so adding a
    # photo never needs the list rewritten -- and an entry naming a file that
    # has since been deleted is simply ignored rather than breaking the load.
    photo_order: list = field(default_factory=list)

    # ---- what this video is for -------------------------------------------
    intent: str = ""              # any key of INTENTS; falls back to brand.default_intent
    cta_action: str = "auto"      # any key of CTA_ACTIONS
    cta_detail: str = ""          # link, address or booking note to read out
    cta_detail_hi: str = ""
    target_seconds: int = 35      # rough length to aim for

    # ---- who it is for, and when ------------------------------------------
    category: str = ""            # e.g. "furniture", "restaurant"; also picks hashtags
    audience: str = ""            # e.g. "families setting up a new home"
    audience_hi: str = ""
    occasion: str = ""            # e.g. "Diwali", "wedding season"
    occasion_hi: str = ""

    # ---- the deal ---------------------------------------------------------
    offer: str = ""               # e.g. "Buy 2 get 1 free", "20% off"
    offer_hi: str = ""
    offer_ends: str = ""          # e.g. "31 August" -- becomes the urgency beat
    offer_ends_hi: str = ""
    urgency: str = ""             # e.g. "only 20 pieces left"
    urgency_hi: str = ""

    # ---- anything else worth saying --------------------------------------
    # Free-form facts, for the many businesses that do not have a 'warranty'
    # or a 'material'. Label -> value, e.g. {seats: 40, cuisine: "South Indian"}.
    specs: dict = field(default_factory=dict)
    specs_hi: dict = field(default_factory=dict)
    # Credibility lines: "4.8 stars from 200+ reviews", "ISO 9001 certified".
    proof_points: list = field(default_factory=list)
    proof_points_hi: list = field(default_factory=list)

    # ---- guardrails for the AI script modes -------------------------------
    must_say: list = field(default_factory=list)   # phrases to work in verbatim
    must_say_hi: list = field(default_factory=list)
    avoid: list = field(default_factory=list)      # words/claims never to use

    @staticmethod
    def load(product_dir) -> "Product":
        d = Path(product_dir).resolve()
        spec = d / "product.yaml"
        if not spec.exists():
            raise FileNotFoundError(
                f"No product.yaml in {d}. Copy products/sample-iron-shelf/product.yaml as a starting point."
            )
        data = _read_yaml(spec)
        known = set(Product.__dataclass_fields__) - {"slug", "dir", "photos"}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"{spec}: unknown field(s) {sorted(unknown)}. Valid: {sorted(known)}")

        photo_dir = d / "photos"
        if not photo_dir.is_dir():
            raise FileNotFoundError(f"Create {photo_dir} and put the product photos in it.")
        photos = order_photos(
            [p for p in photo_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS],
            data.get("photo_order") or [],
        )
        if not photos:
            raise FileNotFoundError(f"No images found in {photo_dir}.")

        for req in ("name_en", "name_hi"):
            if not data.get(req):
                raise ValueError(f"{spec}: '{req}' is required.")

        _check_choice(spec, data, "intent", INTENTS, allow_blank=True)
        _check_choice(spec, data, "cta_action", CTA_ACTIONS, allow_blank=True)
        _check_choice(spec, data, "tone", TONES, allow_blank=True)
        for key in ("specs", "specs_hi"):
            if key in data:
                if not isinstance(data[key], dict):
                    raise ValueError(
                        f"{spec}: '{key}' should be a mapping of label to value, e.g.\n"
                        f"  {key}:\n    seats: 40\n    cuisine: South Indian"
                    )
                data[key] = {str(k): str(v) for k, v in data[key].items() if v not in (None, "")}
        for key in ("usp_en", "usp_hi", "script_en", "script_hi", "overlay_en", "overlay_hi",
                    "hashtags", "proof_points", "proof_points_hi", "must_say", "must_say_hi",
                    "avoid", "photo_order"):
            if key in data and not isinstance(data[key], list):
                raise ValueError(f"{spec}: '{key}' should be a list, one item per line.")
        if "target_seconds" in data:
            try:
                data["target_seconds"] = int(data["target_seconds"])
            except (TypeError, ValueError):
                raise ValueError(f"{spec}: 'target_seconds' should be a whole number of seconds.")

        return Product(slug=d.name, dir=d, photos=photos, **data)

    def spec(self, key: str, lang: str) -> str:
        """A spec value, preferring the Hindi wording when rendering Hindi."""
        if lang == "hi":
            return getattr(self, key + "_hi", "") or getattr(self, key, "")
        return getattr(self, key, "")

    def spec_items(self, lang: str) -> dict:
        """Every factual spec as label -> value: the four built-in fields first,
        then anything from the free-form 'specs' mapping."""
        out = {}
        for key, label in LEGACY_SPEC_LABELS.items():
            val = self.spec(key, lang)
            if val:
                out[label] = str(val)
        extra = (self.specs_hi if lang == "hi" and self.specs_hi else self.specs) or {}
        for label, val in extra.items():
            if val not in (None, ""):
                out[str(label)] = str(val)
        return out

    def resolve_intent(self, brand: "Brand | None" = None) -> str:
        """What this video is for. Product wins, then brand, then 'sell'."""
        for candidate in (self.intent, getattr(brand, "default_intent", ""), "sell"):
            if candidate in INTENTS:
                return candidate
        return "sell"

    def text(self, key: str, lang: str) -> str:
        """A translatable single-line field ('offer', 'audience', ...)."""
        return self.spec(key, lang)

    def lines(self, key: str, lang: str) -> list:
        """A translatable list field ('proof_points', 'must_say', ...)."""
        if lang == "hi":
            return list(getattr(self, key + "_hi", []) or getattr(self, key, []) or [])
        return list(getattr(self, key, []) or [])

    def usps(self, lang):
        return list(self.usp_hi if lang == "hi" else self.usp_en)

    def name(self, lang):
        return self.name_hi if lang == "hi" else self.name_en

    def script_override(self, lang):
        return list(self.script_hi if lang == "hi" else self.script_en)

    def overlay_override(self, lang):
        return list(self.overlay_hi if lang == "hi" else self.overlay_en)


def order_photos(paths, wanted) -> list:
    """Photo paths in the order the video should use them.

    `wanted` is a list of bare filenames (product.yaml's `photo_order`). It is
    treated as a preference, not a contract: names it lists that are no longer
    on disk are dropped, and files on disk it does not mention are appended in
    natural filename order. That way deleting or adding a photo can never
    leave a product unloadable, and the web UI only has to write the order it
    actually knows about.
    """
    by_name = {p.name: p for p in paths}
    ordered, seen = [], set()
    for name in wanted:
        p = by_name.get(str(name))
        if p is not None and p.name not in seen:
            seen.add(p.name)
            ordered.append(p)
    rest = sorted((p for p in paths if p.name not in seen), key=lambda p: _natural_key(p.name))
    return ordered + rest


def next_photo_index(photo_dir) -> int:
    """The next free number for a photo being added to `photo_dir`.

    Photos are stored as 1.jpg, 2.jpg, ... and that number is the default
    running order, so anything arriving later -- an upload from the web UI, a
    stock photo fetched by `stock.py` -- has to continue the run. Reusing a
    number would silently overwrite a photo already in the reel.
    """
    d = Path(photo_dir)
    if not d.is_dir():
        return 1
    used = [
        int(p.stem) for p in d.iterdir()
        if p.suffix.lower() in IMAGE_EXTS and p.stem.isdigit()
    ]
    return max(used, default=0) + 1


def read_yaml(path) -> dict:
    """Raw dict read, no schema validation. Used by Brand/Product.load and by
    the web UI, which merges form edits into this dict rather than round
    tripping through the dataclasses (so it never has to guess how to
    re-serialise a resolved absolute path back to something portable)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Missing config file: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            # These files are meant to be hand-edited, so a typo in one is an
            # ordinary event, not a crash. Raised as ValueError because that
            # is what every caller already handles -- letting the raw
            # YAMLError through turned a missing quote into a 500 on the very
            # page you would go to in order to fix it.
            where = getattr(exc, "problem_mark", None)
            spot = f" (line {where.line + 1}, column {where.column + 1})" if where else ""
            detail = getattr(exc, "problem", None) or "it is not valid YAML"
            raise ValueError(f"{p.name} could not be read{spot}: {detail}.") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected a mapping of settings at the top level.")
    return data


def write_yaml(path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


_read_yaml = read_yaml  # internal alias used below


def _check_choice(where, data: dict, key: str, allowed, allow_blank: bool = False) -> None:
    val = data.get(key)
    if val in (None, "") and allow_blank:
        return
    if val is not None and val not in allowed:
        raise ValueError(
            f"{where}: '{key}' is {val!r}, which is not one of {', '.join(sorted(allowed))}."
        )


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]
