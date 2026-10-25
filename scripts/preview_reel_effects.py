"""Render a short, silent Instagram reel with optional visual effects.

Run from the project root: python scripts/preview_reel_effects.py
No cloud services are called and product/brand settings are left untouched.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reelfactory.cli import build_one
from reelfactory.config import Brand, Product
from reelfactory.script import Segment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aspect", choices=["16:9", "9:16", "1:1", "4:5"], default="9:16")
    parser.add_argument("--visual-effects", choices=["none", "gold_sweep", "sparks", "glints", "gold"], default="gold")
    args = parser.parse_args()
    product = Product.load(ROOT / "products" / "railing")
    brand = Brand.load(ROOT / "brand.yaml")
    segments = [
        Segment("hook", "आपके घर के लिए सुरक्षा और खूबसूरती।", "सुरक्षा के साथ खूबसूरती"),
        Segment("usp", "आपकी पसंद का डिज़ाइन, आपके नाप की रेलिंग।", "आपकी पसंद का डिज़ाइन"),
        Segment("usp", "बालकनी, सीढ़ियों और छत के लिए कस्टम रेलिंग।", "आपके नाप की रेलिंग"),
        Segment("cta", "डिज़ाइन और उपलब्धता जानने के लिए संपर्क कीजिए।", f"डिज़ाइन जानने के लिए संपर्क करें\n{brand.phone}"),
    ]
    settings = SimpleNamespace(tts="silent", no_music=True, script="template",
                               preset="veryfast", template="classic", preview=True,
                               visual_effects=args.visual_effects, effect_strength=0.5,
                               steer="", keep_temp=False)
    for path in build_one(product, brand, "hi", [args.aspect], ROOT / "out" / "reel-effects-preview",
                          settings, segments=segments):
        print(path)


if __name__ == "__main__":
    main()
