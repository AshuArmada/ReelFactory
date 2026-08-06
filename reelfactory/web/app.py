"""Local web UI for entering brand/product data, uploading photos, and
triggering builds -- an alternative to hand-editing yaml files.

Single-user, local-only tool: no auth, builds run synchronously in the
request (a build takes 30s-3min, which is fine for one person on localhost).
Never asks for API keys in the browser -- Gemini/Grok keys are still only
ever read from environment variables or .env, exactly as from the CLI.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

from flask import Flask, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from .. import cli as rf_cli
from .. import script as copywriter
from ..config import Brand, IMAGE_EXTS, Product, read_yaml, write_yaml
from ..gemini import GeminiError
from ..grok import GrokError
from ..local_llm import LocalLLMError
from ..render import ASPECTS, RenderError
from ..voice import TTSError

TONES = ["value", "premium", "trust"]
LANGS = ["hi", "en"]

BRAND_TEXT_FIELDS = [
    ("name", "Brand name"),
    ("tagline_en", "Tagline (English)"),
    ("tagline_hi", "Tagline (Hindi)"),
    ("city", "City"),
    ("phone", "Phone"),
    ("whatsapp", "WhatsApp"),
    ("website", "Website"),
]
BRAND_COLOR_FIELDS = [
    ("primary_color", "Primary colour"),
    ("secondary_color", "Secondary colour"),
    ("text_color", "Text colour"),
]
BRAND_VOICE_FIELDS = [
    ("voice_hi", "Hindi voice (edge-tts)"),
    ("voice_en", "English voice (edge-tts)"),
    ("rate_hi", "Hindi speaking rate"),
    ("rate_en", "English speaking rate"),
]
BRAND_AI_FIELDS = [
    ("gemini_script_model", "Gemini script model"),
    ("gemini_tts_model", "Gemini TTS model"),
    ("gemini_voice", "Gemini voice"),
    ("grok_script_model", "Grok script model"),
    ("local_script_model", "Local model name (e.g. llama3.1)"),
    ("local_base_url", "Local model server URL"),
]

PRODUCT_LANG_FIELDS = [
    ("material", "material"), ("sizes", "sizes"),
    ("warranty", "warranty"), ("delivery", "delivery"),
]


def create_app(brand_path: Path, products_root: Path, out_root: Path) -> Flask:
    app = Flask(__name__)
    app.secret_key = "reel-factory-local"  # local tool only; flash messages, not real sessions
    app.jinja_env.filters["as_lines"] = lambda v: "\n".join(v) if isinstance(v, list) else (v or "")
    products_root.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)

    def _build_page_ctx(slug: str) -> dict:
        return dict(
            slug=slug, langs=LANGS, aspects=list(ASPECTS),
            script_choices=rf_cli.SCRIPT_CHOICES, tts_choices=rf_cli.TTS_CHOICES,
            presets=rf_cli.PRESETS, outputs=_list_outputs(out_root / slug),
        )

    # ------------------------------------------------------------- dashboard

    @app.get("/")
    def index():
        brand, brand_error = _try_load_brand(brand_path)
        products, product_errors = _list_products(products_root)
        return render_template(
            "index.html", brand=brand, brand_error=brand_error,
            products=products, product_errors=product_errors,
        )

    # ------------------------------------------------------------------ brand

    @app.get("/brand")
    def brand_edit():
        raw = read_yaml(brand_path) if brand_path.exists() else {}
        return render_template(
            "brand_edit.html", raw=raw,
            text_fields=BRAND_TEXT_FIELDS, color_fields=BRAND_COLOR_FIELDS,
            voice_fields=BRAND_VOICE_FIELDS, ai_fields=BRAND_AI_FIELDS,
        )

    @app.post("/brand")
    def brand_save():
        raw = read_yaml(brand_path) if brand_path.exists() else {}
        for key, _ in BRAND_TEXT_FIELDS + BRAND_COLOR_FIELDS + BRAND_VOICE_FIELDS + BRAND_AI_FIELDS:
            raw[key] = request.form.get(key, "").strip()
        raw["watermark"] = request.form.get("watermark") == "on"
        try:
            raw["music_volume"] = float(request.form.get("music_volume") or raw.get("music_volume", 0.12))
        except ValueError:
            raw["music_volume"] = raw.get("music_volume", 0.12)
        write_yaml(brand_path, raw)
        return redirect(url_for("index"))

    # --------------------------------------------------------------- products

    @app.get("/products/new")
    def product_new():
        return render_template(
            "product_edit.html", is_new=True, slug="", data={}, photos=[],
            tones=TONES, lang_fields=PRODUCT_LANG_FIELDS,
        )

    @app.post("/products/new")
    def product_create():
        slug = _clean_slug(request.form.get("slug", ""))
        if not slug:
            return render_template(
                "product_edit.html", is_new=True, slug="", data=request.form, photos=[],
                tones=TONES, lang_fields=PRODUCT_LANG_FIELDS,
                error="Give the product a folder name using only lowercase letters, numbers and dashes.",
            ), 400
        prod_dir = products_root / slug
        if prod_dir.exists():
            return render_template(
                "product_edit.html", is_new=True, slug=slug, data=request.form, photos=[],
                tones=TONES, lang_fields=PRODUCT_LANG_FIELDS,
                error=f"A product folder named '{slug}' already exists.",
            ), 400
        (prod_dir / "photos").mkdir(parents=True)
        data = _form_to_product_dict(request.form)
        write_yaml(prod_dir / "product.yaml", data)
        _save_uploaded_photos(prod_dir / "photos", request.files.getlist("photos"))
        return redirect(url_for("product_edit", slug=slug))

    @app.get("/products/<slug>/edit")
    def product_edit(slug):
        prod_dir = products_root / slug
        spec = prod_dir / "product.yaml"
        if not spec.exists():
            return f"No product named '{slug}'.", 404
        data = read_yaml(spec)
        photos = _list_photos(prod_dir / "photos")
        return render_template(
            "product_edit.html", is_new=False, slug=slug, data=data, photos=photos,
            tones=TONES, lang_fields=PRODUCT_LANG_FIELDS,
        )

    @app.post("/products/<slug>/edit")
    def product_update(slug):
        prod_dir = products_root / slug
        spec = prod_dir / "product.yaml"
        if not spec.exists():
            return f"No product named '{slug}'.", 404
        raw = read_yaml(spec)
        raw.update(_form_to_product_dict(request.form))
        write_yaml(spec, raw)

        photo_dir = prod_dir / "photos"
        for name in request.form.getlist("delete_photo"):
            target = photo_dir / secure_filename(name)
            if target.exists() and target.parent == photo_dir:
                target.unlink()
        _save_uploaded_photos(photo_dir, request.files.getlist("photos"))
        return redirect(url_for("product_edit", slug=slug))

    @app.get("/products/<slug>/photos/<path:filename>")
    def product_photo(slug, filename):
        return send_from_directory(products_root / slug / "photos", filename)

    # ------------------------------------------------------------------ build

    @app.get("/products/<slug>/build")
    def build_form(slug):
        prod_dir = products_root / slug
        if not (prod_dir / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        return render_template("build.html", **_build_page_ctx(slug))

    @app.post("/products/<slug>/build")
    def build_run(slug):
        prod_dir = products_root / slug
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            return render_template("build.html", **_build_page_ctx(slug), error=str(exc)), 400

        langs = request.form.getlist("lang") or ["hi"]
        aspects = request.form.getlist("aspect") or ["9:16"]
        args = types.SimpleNamespace(
            tts=request.form.get("tts", "edge"),
            preset=request.form.get("preset", "medium"),
            no_music=request.form.get("no_music") == "on",
            script=request.form.get("script", "template"),
            gemini_key=None, gemini_backup_key=None, grok_key=None,
            local_url=None, local_model=None, local_key=None,
            keep_temp=False,
        )

        written, error = [], None
        try:
            for lang in langs:
                written += rf_cli.build_one(prod, brand, lang, aspects, out_root, args)
        except (TTSError, RenderError, ValueError, FileNotFoundError, GeminiError, GrokError, LocalLLMError) as exc:
            error = str(exc)

        return render_template(
            "build.html", **_build_page_ctx(slug), error=error, just_built=[p.name for p in written],
        )

    @app.post("/products/<slug>/script")
    def script_preview(slug):
        prod_dir = products_root / slug
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            return render_template("build.html", **_build_page_ctx(slug), error=str(exc)), 400

        langs = request.form.getlist("lang") or ["hi"]
        args = types.SimpleNamespace(
            script=request.form.get("script", "template"),
            gemini_key=None, gemini_backup_key=None, grok_key=None,
            local_url=None, local_model=None, local_key=None,
        )

        previews, error = [], None
        try:
            for lang in langs:
                segments = rf_cli._build_segments(prod, brand, lang, args)
                previews.append({
                    "lang": lang,
                    "segments": [{"role": s.role, "vo": s.vo, "overlay": s.overlay} for s in segments],
                    "caption": copywriter.caption(prod, brand, lang),
                })
        except (ValueError, GeminiError, GrokError, LocalLLMError) as exc:
            error = str(exc)

        return render_template("build.html", **_build_page_ctx(slug), error=error, previews=previews)

    @app.get("/out/<slug>/<path:filename>")
    def output_file(slug, filename):
        return send_from_directory(out_root / slug, filename)

    return app


# ----------------------------------------------------------------- internals


def _try_load_brand(brand_path: Path):
    if not brand_path.exists():
        return None, "No brand.yaml found yet."
    try:
        return Brand.load(brand_path), None
    except (ValueError, FileNotFoundError) as exc:
        return None, str(exc)


def _list_products(products_root: Path):
    products, errors = [], []
    if not products_root.exists():
        return products, errors
    for d in sorted(p for p in products_root.iterdir() if p.is_dir()):
        if not (d / "product.yaml").exists():
            continue
        try:
            products.append(Product.load(d))
        except (ValueError, FileNotFoundError) as exc:
            errors.append((d.name, str(exc)))
    return products, errors


def _list_photos(photo_dir: Path):
    if not photo_dir.is_dir():
        return []
    return sorted(
        (p.name for p in photo_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS),
        key=lambda n: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", n)],
    )


def _list_outputs(out_dir: Path):
    if not out_dir.is_dir():
        return []
    return sorted(p.name for p in out_dir.iterdir() if p.is_file())


def _clean_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", text.strip().lower()).strip("-")
    return slug


def _save_uploaded_photos(photo_dir: Path, files) -> None:
    existing = _list_photos(photo_dir)
    next_n = 1
    for name in existing:
        stem = Path(name).stem
        if stem.isdigit():
            next_n = max(next_n, int(stem) + 1)
    for f in files:
        if not f or not f.filename:
            continue
        ext = Path(secure_filename(f.filename)).suffix.lower()
        if ext not in IMAGE_EXTS:
            continue
        f.save(str(photo_dir / f"{next_n}{ext}"))
        next_n += 1


def _form_to_product_dict(form) -> dict:
    data = {
        "name_en": form.get("name_en", "").strip(),
        "name_hi": form.get("name_hi", "").strip(),
        "price": form.get("price", "").strip(),
        "old_price": form.get("old_price", "").strip(),
        "tone": form.get("tone", "value").strip(),
    }
    for key, _ in PRODUCT_LANG_FIELDS:
        data[key] = form.get(key, "").strip()
        data[f"{key}_hi"] = form.get(f"{key}_hi", "").strip()
    data["usp_en"] = _lines(form.get("usp_en", ""))
    data["usp_hi"] = _lines(form.get("usp_hi", ""))
    data["hashtags"] = _lines(form.get("hashtags", ""))
    return {k: v for k, v in data.items() if v not in ("", [], None)}


def _lines(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip()]
