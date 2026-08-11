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
from ..ad_prompt import ALL_ROLES
from ..config import Brand, CTA_ACTIONS, IMAGE_EXTS, INTENTS, Product, read_yaml, write_yaml
from ..script import Segment
from ..gemini import GeminiError
from ..grok import GrokError
from ..local_llm import LocalLLMError
from ..render import ASPECTS, RenderError
from ..voice import TTSError

TONES = ["value", "premium", "trust"]
LANGS = ["hi", "en"]
# Roles an edited line may carry. The role picks the on-screen style, so it is
# a closed list -- "custom" is the neutral body style, used for hand-added lines.
SEGMENT_ROLES = list(ALL_ROLES) + ["custom"]
VARIANT_COUNT = 3

BRAND_TEXT_FIELDS = [
    ("name", "Brand name"),
    ("tagline_en", "Tagline (English)"),
    ("tagline_hi", "Tagline (Hindi)"),
    ("city", "City"),
    ("phone", "Phone"),
    ("whatsapp", "WhatsApp"),
    ("website", "Website"),
    ("address", "Shop / showroom address"),
    ("hours", "Opening hours"),
    ("instagram", "Instagram handle"),
    ("established", "In business since (year)"),
]
BRAND_DEFAULT_FIELDS = [
    ("category", "Business category (e.g. furniture, restaurant, coaching)"),
    ("audience", "Default audience"),
]
BRAND_COLOR_FIELDS = [
    ("primary_color", "Primary colour"),
    ("secondary_color", "Secondary colour"),
    ("text_color", "Text colour"),
]
# edge-tts's full Hindi/Indian-English roster (`edge-tts --list-voices`), so
# picking a voice is choosing from a list of names that are known to work
# rather than typing one from memory and finding out it's wrong at build time.
# Expressive is a distinct model tuned for livelier, ad-read delivery rather
# than the flatter default -- worth surfacing since nothing else names it.
EDGE_VOICES = {
    "hi": [
        ("hi-IN-MadhurNeural", "Madhur — male"),
        ("hi-IN-SwaraNeural", "Swara — female"),
    ],
    "en": [
        ("en-IN-NeerjaNeural", "Neerja — female"),
        ("en-IN-NeerjaExpressiveNeural", "Neerja Expressive — female, livelier ad-read delivery"),
        ("en-IN-PrabhatNeural", "Prabhat — male"),
    ],
}

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
    app.jinja_env.filters["as_kv"] = (
        lambda v: "\n".join(f"{k}: {val}" for k, val in v.items()) if isinstance(v, dict) else (v or "")
    )
    products_root.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)

    def _preview_ctx(previews, form=None) -> dict:
        return dict(previews=previews, steer=(form.get("steer", "").strip() if form else ""))

    def _build_page_ctx(slug: str, form=None) -> dict:
        # After a build the page re-renders, so echo back what was actually
        # submitted -- otherwise every option silently resets to the default
        # and the second build of the day is built with the wrong settings.
        chosen = dict(
            lang=(form.getlist("lang") or ["hi"]) if form else list(LANGS),
            aspect=(form.getlist("aspect") or ["9:16"]) if form else ["9:16"],
            script=(form.get("script") if form else None) or "template",
            tts=(form.get("tts") if form else None) or "edge",
            preset=(form.get("preset") if form else None) or "medium",
            no_music=(form.get("no_music") == "on") if form else False,
        )
        return dict(
            slug=slug, langs=LANGS, aspects=list(ASPECTS),
            script_choices=rf_cli.SCRIPT_CHOICES, tts_choices=rf_cli.TTS_CHOICES,
            presets=rf_cli.PRESETS, outputs=_list_outputs(out_root / slug),
            chosen=chosen, roles=SEGMENT_ROLES, variant_count=VARIANT_COUNT,
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
        # brand.yaml is hand-editable, so it can hold anything: an explicit
        # `null` (the file ships several), a colour without its #, a volume
        # typed as "0.2". Coerce here rather than in the template -- a
        # template that does arithmetic on whatever YAML handed it turns a
        # typo in a config file into a 500 on the page you'd fix it from.
        return render_template(
            "brand_edit.html",
            raw={k: ("" if v is None else v) for k, v in raw.items()},
            swatches={key: _as_hex(raw.get(key)) for key, _ in BRAND_COLOR_FIELDS},
            music_volume=_as_volume(raw.get("music_volume")),
            intents=INTENTS, edge_voices=EDGE_VOICES,
            text_fields=BRAND_TEXT_FIELDS, color_fields=BRAND_COLOR_FIELDS,
            voice_fields=BRAND_VOICE_FIELDS, ai_fields=BRAND_AI_FIELDS,
            default_fields=BRAND_DEFAULT_FIELDS,
        )

    @app.post("/brand")
    def brand_save():
        raw = read_yaml(brand_path) if brand_path.exists() else {}
        for key, _ in BRAND_TEXT_FIELDS + BRAND_COLOR_FIELDS + BRAND_VOICE_FIELDS + BRAND_AI_FIELDS + BRAND_DEFAULT_FIELDS:
            raw[key] = request.form.get(key, "").strip()
        default_intent = request.form.get("default_intent", "sell").strip()
        raw["default_intent"] = default_intent if default_intent in INTENTS else "sell"
        raw["watermark"] = request.form.get("watermark") == "on"
        # Normalise on the way out too, so one bad value can't stay in the file.
        raw["music_volume"] = _as_volume(
            request.form.get("music_volume"), _as_volume(raw.get("music_volume"))
        )
        write_yaml(brand_path, raw)
        return redirect(url_for("index"))

    # --------------------------------------------------------------- products

    @app.get("/products/new")
    def product_new():
        return render_template(
            "product_edit.html", is_new=True, slug="", data={}, photos=[],
            tones=TONES, lang_fields=PRODUCT_LANG_FIELDS, intents=INTENTS, cta_actions=CTA_ACTIONS,
        )

    @app.post("/products/new")
    def product_create():
        slug = _clean_slug(request.form.get("slug", ""))
        if not slug:
            return render_template(
                "product_edit.html", is_new=True, slug="", data=request.form, photos=[],
                tones=TONES, lang_fields=PRODUCT_LANG_FIELDS, intents=INTENTS, cta_actions=CTA_ACTIONS,
                error="Give the product a folder name using only lowercase letters, numbers and dashes.",
            ), 400
        prod_dir = products_root / slug
        if prod_dir.exists():
            return render_template(
                "product_edit.html", is_new=True, slug=slug, data=request.form, photos=[],
                tones=TONES, lang_fields=PRODUCT_LANG_FIELDS, intents=INTENTS, cta_actions=CTA_ACTIONS,
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
            tones=TONES, lang_fields=PRODUCT_LANG_FIELDS, intents=INTENTS, cta_actions=CTA_ACTIONS,
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
            return render_template("build.html", **_build_page_ctx(slug, request.form), error=str(exc)), 400

        langs = request.form.getlist("lang") or ["hi"]
        aspects = request.form.getlist("aspect") or ["9:16"]
        args = types.SimpleNamespace(
            tts=request.form.get("tts", "edge"),
            preset=request.form.get("preset", "medium"),
            no_music=request.form.get("no_music") == "on",
            script=request.form.get("script", "template"),
            steer=request.form.get("steer", ""),
            gemini_key=None, gemini_backup_key=None, grok_key=None,
            local_url=None, local_model=None, local_key=None,
            keep_temp=False,
        )

        # A script edited on the page wins over the writer: render these exact
        # words. Anything not edited (a language never previewed) is written
        # fresh as before.
        edited = {lang: _form_segments(request.form, lang) for lang in langs}

        written, error = [], None
        try:
            for lang in langs:
                written += rf_cli.build_one(
                    prod, brand, lang, aspects, out_root, args, segments=edited.get(lang),
                )
        except (TTSError, RenderError, ValueError, FileNotFoundError, GeminiError, GrokError, LocalLLMError) as exc:
            error = str(exc)

        # Keep the edited words on screen afterwards, so a failed or repeated
        # build doesn't cost the user their rewrite.
        previews = [
            {"lang": lang, "segments": [vars(s) for s in segs],
             "caption": copywriter.caption(prod, brand, lang)}
            for lang, segs in edited.items() if segs
        ]
        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx(previews, request.form),
            error=error, just_built=[p.name for p in written],
        )

    @app.post("/products/<slug>/script")
    def script_preview(slug):
        prod_dir = products_root / slug
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            return render_template("build.html", **_build_page_ctx(slug, request.form), error=str(exc)), 400

        langs = request.form.getlist("lang") or ["hi"]
        args = types.SimpleNamespace(
            script=request.form.get("script", "template"),
            steer=request.form.get("steer", ""),
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
            # A failed rewrite must not throw away the draft already on screen.
            previews = [
                {"lang": lang, "segments": [vars(s) for s in segs],
                 "caption": copywriter.caption(prod, brand, lang)}
                for lang in langs
                for segs in [_form_segments(request.form, lang)] if segs
            ]

        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx(previews, request.form), error=error,
        )

    @app.post("/products/<slug>/script/variants")
    def script_variants(slug):
        prod_dir = products_root / slug
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            return render_template("build.html", **_build_page_ctx(slug, request.form), error=str(exc)), 400

        langs = request.form.getlist("lang") or ["hi"]
        args = types.SimpleNamespace(
            script=request.form.get("script", "template"),
            steer=request.form.get("steer", ""),
            gemini_key=None, gemini_backup_key=None, grok_key=None,
            local_url=None, local_model=None, local_key=None,
        )

        versions, error = {}, None
        try:
            for lang in langs:
                drafts = rf_cli._build_segment_variants(prod, brand, lang, args, n=VARIANT_COUNT)
                versions[lang] = [
                    [{"role": s.role, "vo": s.vo, "overlay": s.overlay} for s in segs]
                    for segs in drafts
                ]
        except (ValueError, GeminiError, GrokError, LocalLLMError) as exc:
            error = str(exc)

        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx([], request.form), versions=versions, error=error,
        )

    @app.post("/products/<slug>/script/pick")
    def script_pick(slug):
        prod_dir = products_root / slug
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            return render_template("build.html", **_build_page_ctx(slug, request.form), error=str(exc)), 400

        langs = request.form.getlist("lang") or ["hi"]
        previews = []
        for lang in langs:
            # No pick submitted for this language (e.g. only one version came
            # back and there was nothing to choose from) -- leave it unwritten
            # rather than guess; the build step falls back to writing it fresh.
            idx = request.form.get(f"pick_{lang}", "").strip()
            if not idx.isdigit():
                continue
            segs = _form_segments(request.form, lang, prefix=f"ver{idx}_")
            if segs:
                previews.append({
                    "lang": lang, "segments": [vars(s) for s in segs],
                    "caption": copywriter.caption(prod, brand, lang),
                })

        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx(previews, request.form),
        )

    @app.get("/out/<slug>/<path:filename>")
    def output_file(slug, filename):
        return send_from_directory(out_root / slug, filename)

    @app.post("/products/<slug>/build/delete")
    def output_delete(slug):
        out_dir = out_root / slug
        deleted, failed = [], []
        for name in request.form.getlist("delete_file"):
            # Same containment check as deleting a product photo: resolve
            # against the safe filename, then require it actually lands
            # inside this product's own output folder before touching disk.
            target = out_dir / secure_filename(name)
            if not (target.exists() and target.is_file() and target.parent == out_dir):
                continue
            try:
                target.unlink()
                deleted.append(target.name)
            except OSError:
                # Windows refuses to delete a file that something still has
                # open -- most often the video itself, still loaded in a
                # player or a browser tab that streamed it a moment ago.
                # That's routine, not a bug, so it gets a plain message
                # here rather than a 500.
                failed.append(target.name)
        return render_template(
            "build.html", **_build_page_ctx(slug), just_deleted=deleted, delete_failed=failed,
        )

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


def _form_segments(form, lang: str, prefix: str = ""):
    """The hand-edited script for one language, or None if it wasn't edited.

    The three lists come from repeated fields, which a browser submits in
    document order, so row N of each list belongs to the same segment. Rows
    with nothing to say are dropped: an empty line would still cost a photo
    and a silent beat in the finished video.

    `prefix` reads a different field set on the same page without collision
    -- the version picker embeds several scripts at once (`ver0_seg_vo_hi`,
    `ver1_seg_vo_hi`, ...) so picking one is a plain form submit carrying
    that version's exact words, never a re-generation that could hand back
    different wording than what was on screen.
    """
    vos = form.getlist(f"{prefix}seg_vo_{lang}")
    if not vos:
        return None
    roles = form.getlist(f"{prefix}seg_role_{lang}")
    overlays = form.getlist(f"{prefix}seg_overlay_{lang}")
    segments = []
    for i, vo in enumerate(vos):
        if not vo.strip():
            continue
        role = roles[i].strip() if i < len(roles) else ""
        overlay = overlays[i].strip() if i < len(overlays) else ""
        segments.append(Segment(
            role if role in SEGMENT_ROLES else "custom",
            vo.strip(),
            overlay,
        ))
    return segments or None


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _as_hex(value, fallback: str = "#000000") -> str:
    """A value the <input type=color> swatch can accept, or a safe stand-in.
    The text field beside it still shows whatever is really in the file."""
    text = str(value or "").strip()
    return text if _HEX_COLOR.match(text) else fallback


def _as_volume(value, fallback: float = 0.12) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return fallback


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
        "intent": form.get("intent", "").strip(),
        "cta_action": form.get("cta_action", "auto").strip(),
        "cta_detail": form.get("cta_detail", "").strip(),
        "cta_detail_hi": form.get("cta_detail_hi", "").strip(),
        "category": form.get("category", "").strip(),
        "audience": form.get("audience", "").strip(),
        "audience_hi": form.get("audience_hi", "").strip(),
        "occasion": form.get("occasion", "").strip(),
        "occasion_hi": form.get("occasion_hi", "").strip(),
        "offer": form.get("offer", "").strip(),
        "offer_hi": form.get("offer_hi", "").strip(),
        "offer_ends": form.get("offer_ends", "").strip(),
        "offer_ends_hi": form.get("offer_ends_hi", "").strip(),
        "urgency": form.get("urgency", "").strip(),
        "urgency_hi": form.get("urgency_hi", "").strip(),
    }
    if form.get("intent") not in INTENTS:
        data["intent"] = ""
    if form.get("cta_action") not in CTA_ACTIONS:
        data["cta_action"] = "auto"
    target = form.get("target_seconds", "").strip()
    if target.isdigit():
        data["target_seconds"] = int(target)

    for key, _ in PRODUCT_LANG_FIELDS:
        data[key] = form.get(key, "").strip()
        data[f"{key}_hi"] = form.get(f"{key}_hi", "").strip()
    data["usp_en"] = _lines(form.get("usp_en", ""))
    data["usp_hi"] = _lines(form.get("usp_hi", ""))
    data["hashtags"] = _lines(form.get("hashtags", ""))
    data["proof_points"] = _lines(form.get("proof_points", ""))
    data["proof_points_hi"] = _lines(form.get("proof_points_hi", ""))
    data["must_say"] = _lines(form.get("must_say", ""))
    data["must_say_hi"] = _lines(form.get("must_say_hi", ""))
    data["avoid"] = _lines(form.get("avoid", ""))
    data["specs"] = _kv(form.get("specs", ""))
    data["specs_hi"] = _kv(form.get("specs_hi", ""))
    return {k: v for k, v in data.items() if v not in ("", [], {}, None)}


def _lines(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _kv(text: str) -> dict:
    """Parse a 'label: value' per line textarea into a dict, e.g.
    'seats: 40\\ncuisine: South Indian' -> {"seats": "40", "cuisine": "South Indian"}."""
    out = {}
    for line in text.splitlines():
        label, sep, value = line.partition(":")
        if sep and label.strip() and value.strip():
            out[label.strip()] = value.strip()
    return out
