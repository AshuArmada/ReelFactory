"""Load and validate brand + product configuration."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class Brand:
    name: str = "Your Brand"
    tagline_en: str = ""
    tagline_hi: str = ""
    phone: str = ""
    whatsapp: str = ""
    website: str = ""
    city: str = ""
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

    # Only used with --script ai / --tts gemini. The API key itself is never
    # read from here -- only from GEMINI_API_KEY or --gemini-key -- so it
    # can't end up committed alongside this file.
    gemini_script_model: str = "gemini-2.5-flash"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_voice: str = "Kore"

    # Only used with --script grok. The API key itself is never read from
    # here -- only from GROK_API_KEY or --grok-key.
    grok_script_model: str = "grok-4-latest"

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
        photos = sorted(
            (p for p in photo_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS),
            key=lambda p: _natural_key(p.name),
        )
        if not photos:
            raise FileNotFoundError(f"No images found in {photo_dir}.")

        for req in ("name_en", "name_hi"):
            if not data.get(req):
                raise ValueError(f"{spec}: '{req}' is required.")

        return Product(slug=d.name, dir=d, photos=photos, **data)

    def spec(self, key: str, lang: str) -> str:
        """A spec value, preferring the Hindi wording when rendering Hindi."""
        if lang == "hi":
            return getattr(self, key + "_hi", "") or getattr(self, key, "")
        return getattr(self, key, "")

    def usps(self, lang):
        return list(self.usp_hi if lang == "hi" else self.usp_en)

    def name(self, lang):
        return self.name_hi if lang == "hi" else self.name_en

    def script_override(self, lang):
        return list(self.script_hi if lang == "hi" else self.script_en)

    def overlay_override(self, lang):
        return list(self.overlay_hi if lang == "hi" else self.overlay_en)


def read_yaml(path) -> dict:
    """Raw dict read, no schema validation. Used by Brand/Product.load and by
    the web UI, which merges form edits into this dict rather than round
    tripping through the dataclasses (so it never has to guess how to
    re-serialise a resolved absolute path back to something portable)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Missing config file: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected a mapping of settings at the top level.")
    return data


def write_yaml(path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


_read_yaml = read_yaml  # internal alias used below


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]
