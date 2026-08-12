"""Shared fixtures.

Every test runs against a throwaway project in tmp_path -- its own brand.yaml,
products/ and out/ -- so nothing here can touch the real products, and tests
never depend on what happens to be sitting in out/ from yesterday's build.

Photos are generated as solid colours by ffmpeg rather than committed as
fixtures. That keeps the repo small, but the real reason is that a known
colour per photo is what lets `test_end_to_end.py` prove which photo actually
reached the screen at a given second of the finished video.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from werkzeug.datastructures import MultiDict

from reelfactory.web.app import create_app

# Distinct, fully saturated colours: after Ken Burns, the letterbox, the text
# scrim and JPEG, the *dominant channel* is still unambiguous.
COLORS = {
    "red": "0xFF0000",
    "green": "0x00FF00",
    "blue": "0x0000FF",
    "yellow": "0xFFFF00",
    "magenta": "0xFF00FF",
}
COLOR_NAMES = list(COLORS)


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


needs_ffmpeg = pytest.mark.skipif(
    not (have("ffmpeg") and have("ffprobe")),
    reason="needs FFmpeg + ffprobe on PATH",
)


# --------------------------------------------------------------------- photos


@pytest.fixture(scope="session")
def photo_cache(tmp_path_factory):
    """One solid-colour JPEG per colour, made once for the whole session."""
    if not have("ffmpeg"):
        pytest.skip("needs FFmpeg on PATH")
    cache = tmp_path_factory.mktemp("photo_cache")
    for name, hexcode in COLORS.items():
        dest = cache / f"{name}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"color=c={hexcode}:s=720x1280", "-frames:v", "1", str(dest)],
            check=True, capture_output=True,
        )
    return cache


@pytest.fixture
def photos(photo_cache):
    """Copy n colour photos into a folder as 1.jpg, 2.jpg, ... in colour order.

    Returns the list of colour names, so a test can say "shot 3 should be
    blue" without repeating the mapping.
    """
    def _install(photo_dir: Path, n: int = 3):
        photo_dir.mkdir(parents=True, exist_ok=True)
        used = COLOR_NAMES[:n]
        for i, colour in enumerate(used, start=1):
            shutil.copy(photo_cache / f"{colour}.jpg", photo_dir / f"{i}.jpg")
        return used
    return _install


# -------------------------------------------------------------------- project


BRAND = {
    "name": "Test Steel Works",
    "tagline_en": "Iron that lasts",
    "tagline_hi": "लोहा जो चले",
    "city": "kanpur",
    "phone": "+91 90000 00000",
    "whatsapp": "+91 90000 00000",
    "logo": None,
    "music": None,
    "music_volume": 0.12,
    "watermark": True,
    "primary_color": "#E4572E",
    "secondary_color": "#0B0B0F",
    "text_color": "#FFFFFF",
}

PRODUCT = {
    "name_en": "Test Rack",
    "name_hi": "टेस्ट रैक",
    "price": "Rs 4,499",
    "tone": "value",
    "usp_en": ["Holds 150 kilos per shelf", "Does not rust", "Fits in ten minutes"],
    "usp_hi": ["हर शेल्फ़ पर 150 किलो", "ज़ंग नहीं लगेगी", "दस मिनट में फिट"],
}


@pytest.fixture
def project(tmp_path, photos):
    """A complete, valid project directory. Returns its root Path."""
    root = tmp_path / "proj"
    (root / "out").mkdir(parents=True)
    write_yaml(root / "brand.yaml", BRAND)
    make_product(root, "test-rack", PRODUCT, photos, n_photos=3)
    return root


def make_product(root: Path, slug: str, data: dict, photos, n_photos: int = 3):
    """Add another product to an existing project. Returns its folder."""
    prod = root / "products" / slug
    prod.mkdir(parents=True)
    write_yaml(prod / "product.yaml", data)
    photos(prod / "photos", n_photos)
    return prod


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


def read_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ------------------------------------------------------------------ the app


@pytest.fixture
def app(project):
    app = create_app(
        brand_path=project / "brand.yaml",
        products_root=project / "products",
        out_root=project / "out",
    )
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ------------------------------------------------------------------- helpers


def form(*pairs, **single):
    """A MultiDict from (name, value) pairs -- repeated names are the whole
    point of every script and photo-order form in this app."""
    data = MultiDict(list(pairs))
    for k, v in single.items():
        data.add(k, v)
    return data


def live_html(html: str) -> str:
    """The page with <template> contents removed.

    The script editor ships an inert <template> row that JS clones for "Add a
    line". It is real markup with real name= attributes but is never
    submitted, so any assertion counting fields has to drop it first or it
    silently inflates every count by one.
    """
    return re.sub(r"<template.*?</template>", "", html, flags=re.S)


def selected_photos(html: str) -> list:
    """The photo chosen for each script line, in order."""
    return re.findall(r'<option value="([^"]+)" selected>\d+</option>', live_html(html))


def field_values(html: str, name: str) -> list:
    """Values of every <input name="..."> on the page, in document order."""
    return re.findall(rf'name="{re.escape(name)}" value="([^"]*)"', live_html(html))


def textarea_values(html: str, name: str) -> list:
    return re.findall(
        rf'<textarea name="{re.escape(name)}"[^>]*>(.*?)</textarea>', live_html(html), re.S
    )
