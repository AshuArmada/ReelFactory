"""Local web UI for entering brand/product data, uploading photos, and
triggering builds -- an alternative to hand-editing yaml files.

Single-user, local-only tool: no auth, builds run synchronously in the
request (a build takes 30s-3min, which is fine for one person on localhost).
Never asks for API keys in the browser -- Gemini/Grok keys are still only
ever read from environment variables or .env, exactly as from the CLI.
"""
from __future__ import annotations

import re
import shutil
import types
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from .. import cli as rf_cli
from .. import preflight
from .. import script as copywriter
from ..ad_prompt import ALL_ROLES
from ..config import (
    Brand, CTA_ACTIONS, IMAGE_EXTS, INTENTS, Product, order_photos, read_yaml, write_yaml,
)
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
BRAND_FONT_FIELDS = [
    ("font_hi", "Hindi font", "Leave blank to auto-pick. Set only if Hindi renders as boxes."),
    ("font_en", "English font", "Leave blank to auto-pick."),
]
# Uploaded rather than typed: these are the two brand settings that are files,
# and hand-editing brand.yaml to point at one is exactly the chore the web UI
# exists to remove. Saved next to brand.yaml and stored as a relative path, so
# the whole folder stays portable.
BRAND_ASSETS = [
    ("logo", "Logo", "logo", IMAGE_EXTS,
     "A transparent PNG works best. It replaces the brand-name watermark."),
    ("music", "Background music", "music", {".mp3", ".m4a", ".wav", ".aac", ".ogg"},
     "Royalty-free tracks only. It is auto-ducked under the voice."),
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
            saved_scripts=_load_saved_scripts(products_root, slug),
            # The script editor shows the photo each line will be rendered
            # over, so it needs the same ordered list the build will use.
            product_photos=_ordered_photo_names(products_root, slug),
        )

    # ------------------------------------------------------------- dashboard

    @app.get("/")
    def index():
        brand, brand_error = _try_load_brand(brand_path)
        products, product_errors = _list_products(products_root)
        checks = preflight.run(brand)
        return render_template(
            "index.html", brand=brand, brand_error=brand_error,
            products=products, product_errors=product_errors,
            checks=checks, checks_blocking=preflight.blocking(checks),
            notice=request.args.get("notice", ""),
        )

    # ------------------------------------------------------------------ brand

    @app.get("/brand")
    def brand_edit():
        return _brand_page()

    def _brand_page(error: str = "", status: int = 200, pending: dict | None = None):
        # `pending` is the half-saved state of a submission that was rejected.
        # Showing it back instead of re-reading the file matters: this form is
        # four tabs of settings saved in one go, so re-reading disk after a
        # rejected upload would quietly discard everything else the user had
        # just typed.
        if pending is not None:
            raw = pending
        else:
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
            default_fields=BRAND_DEFAULT_FIELDS, font_fields=BRAND_FONT_FIELDS,
            assets=[
                {"key": key, "label": label, "hint": hint,
                 "value": raw.get(key) or "", "exists": _asset_exists(brand_path, raw.get(key))}
                for key, label, _sub, _exts, hint in BRAND_ASSETS
            ],
            error=error,
        ), status

    @app.post("/brand")
    def brand_save():
        raw = read_yaml(brand_path) if brand_path.exists() else {}
        for key, _ in BRAND_TEXT_FIELDS + BRAND_COLOR_FIELDS + BRAND_VOICE_FIELDS + BRAND_AI_FIELDS + BRAND_DEFAULT_FIELDS:
            raw[key] = request.form.get(key, "").strip()
        for key, _label, _hint in BRAND_FONT_FIELDS:
            raw[key] = request.form.get(key, "").strip()
        default_intent = request.form.get("default_intent", "sell").strip()
        raw["default_intent"] = default_intent if default_intent in INTENTS else "sell"
        raw["watermark"] = request.form.get("watermark") == "on"
        # Normalise on the way out too, so one bad value can't stay in the file.
        raw["music_volume"] = _as_volume(
            request.form.get("music_volume"), _as_volume(raw.get("music_volume"))
        )

        try:
            for key, label, subdir, exts, _hint in BRAND_ASSETS:
                raw[key] = _save_brand_asset(brand_path, raw.get(key), key, label, subdir, exts)
        except ValueError as exc:
            return _brand_page(str(exc), 400, pending=raw)

        write_yaml(brand_path, raw)
        return redirect(url_for("index"))

    @app.get("/brand/asset/<key>")
    def brand_asset(key):
        """Serve the logo or music file so the settings page can show/play it.

        Reads the path out of brand.yaml rather than taking one from the URL:
        the only two files reachable here are the two this app itself wrote.
        """
        if key not in {k for k, _l, _s, _e, _h in BRAND_ASSETS} or not brand_path.exists():
            return "Unknown asset.", 404
        value = read_yaml(brand_path).get(key)
        if not value:
            return "Not set.", 404
        p = Path(str(value))
        p = p if p.is_absolute() else brand_path.parent / p
        if not p.exists():
            return "Missing file.", 404
        return send_from_directory(p.parent, p.name)

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
        photos = _ordered_photo_names(products_root, slug)
        return render_template(
            "product_edit.html", is_new=False, slug=slug, data=data, photos=photos,
            tones=TONES, lang_fields=PRODUCT_LANG_FIELDS, intents=INTENTS, cta_actions=CTA_ACTIONS,
            notice=request.args.get("notice", ""),
        )

    @app.post("/products/<slug>/edit")
    def product_update(slug):
        prod_dir = products_root / slug
        spec = prod_dir / "product.yaml"
        if not spec.exists():
            return f"No product named '{slug}'.", 404
        raw = read_yaml(spec)
        raw.update(_form_to_product_dict(request.form))

        photo_dir = prod_dir / "photos"
        for name in request.form.getlist("delete_photo"):
            target = photo_dir / secure_filename(name)
            if target.exists() and target.parent == photo_dir:
                target.unlink()
        _save_uploaded_photos(photo_dir, request.files.getlist("photos"))

        # Written after the deletes and the uploads, so the order reflects
        # what is actually on disk now. order_photos() tolerates both drift
        # directions anyway, but storing a list full of ghosts would make
        # product.yaml steadily less readable.
        order = _form_photo_order(request.form, set(_list_photos(photo_dir)))
        if order:
            raw["photo_order"] = order
        else:
            raw.pop("photo_order", None)
        write_yaml(spec, raw)
        return redirect(url_for("product_edit", slug=slug))

    @app.post("/products/<slug>/duplicate")
    def product_duplicate(slug):
        source = _safe_product_dir(products_root, slug)
        if source is None or not (source / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        target_slug = _clean_slug(request.form.get("new_slug", "")) or _next_copy_slug(products_root, slug)
        target = _safe_product_dir(products_root, target_slug)
        if target is None:
            return redirect(url_for("index", notice="That copy needs a valid folder name."))
        if target.exists():
            return redirect(url_for("index", notice=f"A product called '{target_slug}' already exists."))
        # Photos and product.yaml only. Saved scripts are copied too -- they
        # are wording for this product's facts, and a duplicate starts life
        # with the same facts -- but nothing from out/ comes along: those are
        # finished videos of the *other* product.
        shutil.copytree(source, target)
        return redirect(url_for("product_edit", slug=target_slug))

    @app.post("/products/<slug>/delete")
    def product_delete(slug):
        target = _safe_product_dir(products_root, slug)
        if target is None or not target.is_dir():
            return f"No product named '{slug}'.", 404
        # Typed-name confirmation, not just a checkbox: this throws away the
        # photos and every fact entered about the product, and there is no
        # undo anywhere in the tool.
        if request.form.get("confirm_slug", "").strip() != slug:
            return redirect(url_for(
                "product_edit", slug=slug,
                notice=f"Nothing was deleted — type '{slug}' exactly to confirm.",
            ))
        shutil.rmtree(target, ignore_errors=True)
        note = f"Deleted the product '{slug}'."
        if request.form.get("delete_outputs") == "on":
            out_dir = out_root / slug
            if out_dir.is_dir() and out_dir.parent == out_root:
                shutil.rmtree(out_dir, ignore_errors=True)
                note += " Its finished videos are gone too."
        if target.exists():
            note = (f"Could not fully delete '{slug}' — a file in it is still open "
                    f"somewhere. Close any video player and try again.")
        return redirect(url_for("index", notice=note))

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

        # "lang:idx" markers left behind by picking more than one version of
        # some language on the compare screen -- one video per marker, using
        # that exact version's words, rather than the usual single script.
        multi_specs = request.form.getlist("build_versions")
        if multi_specs:
            written, error = [], None
            try:
                for spec in multi_specs:
                    lang, _, idx = spec.partition(":")
                    segs, pics = _form_rows(request.form, lang, prefix=f"ver{idx}_")
                    if not segs:
                        continue
                    written += rf_cli.build_one(
                        prod, brand, lang, aspects, out_root, args,
                        segments=segs, photo_names=pics, variant_tag=f"_v{int(idx) + 1}",
                    )
            except (TTSError, RenderError, ValueError, FileNotFoundError, GeminiError, GrokError, LocalLLMError) as exc:
                error = str(exc)
            return render_template(
                "build.html", **_build_page_ctx(slug, request.form), error=error,
                # Every variant of the same product+lang shares one caption
                # file (its text never depends on which script was used), so
                # a multi-video build "writes" it several times over --
                # dedupe rather than list it once per video in "Done.".
                **_result_ctx(out_root, slug, written),
            )

        langs = request.form.getlist("lang") or ["hi"]
        # A script edited on the page wins over the writer: render these exact
        # words. Anything not edited (a language never previewed) is written
        # fresh as before.
        edited = {lang: _form_rows(request.form, lang) for lang in langs}

        written, error = [], None
        try:
            for lang in langs:
                segs, pics = edited.get(lang, (None, None))
                written += rf_cli.build_one(
                    prod, brand, lang, aspects, out_root, args,
                    segments=segs, photo_names=pics,
                )
        except (TTSError, RenderError, ValueError, FileNotFoundError, GeminiError, GrokError, LocalLLMError) as exc:
            error = str(exc)

        # Keep the edited words on screen afterwards, so a failed or repeated
        # build doesn't cost the user their rewrite.
        previews = [
            _preview(prod, brand, lang, segs, pics)
            for lang, (segs, pics) in edited.items() if segs
        ]
        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx(previews, request.form),
            error=error, **_result_ctx(out_root, slug, written),
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
                # A fresh draft has no photo choices of its own, but the ones
                # already on screen were deliberate -- carry them across by
                # position so asking for new words doesn't silently reshuffle
                # the pictures too.
                _prev, kept = _form_rows(request.form, lang)
                previews.append(_preview(prod, brand, lang, segments, kept))
        except (ValueError, GeminiError, GrokError, LocalLLMError) as exc:
            error = str(exc)
            # A failed rewrite must not throw away the draft already on screen.
            previews = [
                _preview(prod, brand, lang, segs, pics)
                for lang in langs
                for segs, pics in [_form_rows(request.form, lang)] if segs
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
                versions[lang] = [_rows(prod, segs) for segs in drafts]
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

        # pick_<lang> is now a checkbox group, so more than one version of the
        # same language can come back -- collect everything picked before
        # deciding which of the two very different outcomes this leads to.
        langs = request.form.getlist("lang") or ["hi"]
        picks: dict[str, list] = {}
        for lang in langs:
            for idx in request.form.getlist(f"pick_{lang}"):
                if not idx.isdigit():
                    continue
                segs, pics = _form_rows(request.form, lang, prefix=f"ver{idx}_")
                if segs:
                    picks.setdefault(lang, []).append((idx, segs, pics))

        if any(len(entries) > 1 for entries in picks.values()):
            # Two or more versions of some language were picked: there is no
            # single "the script" to drop into the editor, so this becomes a
            # build-several-videos job instead -- one video per version,
            # carried through as its own set of hidden fields.
            multi = [
                {"lang": lang, "idx": idx, "version": int(idx) + 1,
                 "segments": _rows(prod, segs, pics)}
                for lang, entries in picks.items() for idx, segs, pics in entries
            ]
            return render_template(
                "build.html", **_build_page_ctx(slug, request.form),
                **_preview_ctx([], request.form), multi=multi,
            )

        # The common case -- exactly one version per language -- keeps
        # today's behaviour: it becomes the single editable working script.
        previews = [
            _preview(prod, brand, lang, segs, pics)
            for lang, entries in picks.items() for _idx, segs, pics in entries
        ]
        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx(previews, request.form),
        )

    @app.post("/products/<slug>/script/save")
    def script_save(slug):
        prod_dir = products_root / slug
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            return render_template("build.html", **_build_page_ctx(slug, request.form), error=str(exc)), 400

        langs = request.form.getlist("lang") or ["hi"]
        edited = {lang: _form_rows(request.form, lang) for lang in langs}
        name = request.form.get("save_name", "").strip()

        error = None
        if not name:
            error = "Give the script a name before saving."
        elif not any(segs for segs, _ in edited.values()):
            error = "There is nothing to save yet."
        else:
            for lang, (segs, pics) in edited.items():
                if segs:
                    _save_script(products_root, slug, lang, name, segs, pics)

        previews = [
            _preview(prod, brand, lang, segs, pics)
            for lang, (segs, pics) in edited.items() if segs
        ]
        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx(previews, request.form),
            error=error, script_saved=(name if not error else None),
        )

    @app.post("/products/<slug>/script/load")
    def script_load(slug):
        prod_dir = products_root / slug
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            return render_template("build.html", **_build_page_ctx(slug, request.form), error=str(exc)), 400

        # "lang:index" -- same shape as build_versions/pick_<lang>, so one
        # button carries both pieces the route needs.
        lang, _, idx_text = request.form.get("load_pick", "").partition(":")
        saved = _load_saved_scripts(products_root, slug).get(lang, [])
        try:
            entry = saved[int(idx_text)]
        except (ValueError, IndexError):
            return render_template(
                "build.html", **_build_page_ctx(slug, request.form),
                error="That saved script could not be found — it may already have been deleted.",
            ), 400

        rows = entry.get("segments", [])
        segs = [
            Segment(s.get("role") or "custom", s.get("vo", ""), s.get("overlay", ""))
            for s in rows
        ]
        pics = [s.get("photo", "") for s in rows]
        previews = [_preview(prod, brand, lang, segs, pics)]
        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx(previews, request.form),
        )

    @app.post("/products/<slug>/script/saved/delete")
    def script_saved_delete(slug):
        lang, _, idx_text = request.form.get("delete_pick", "").partition(":")
        if lang and idx_text.isdigit():
            _delete_saved_script(products_root, slug, lang, int(idx_text))
        return render_template("build.html", **_build_page_ctx(slug, request.form))

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


# ------------------------------------------------------------- saved scripts
#
# A separate file, not a product.yaml field: Product.load() rejects any YAML
# key it doesn't have a dataclass field for, and a script someone saved from
# the editor is web-UI state, not part of the product's own facts -- keeping
# it in its own file means it can never trip that validation or get mixed up
# with what build_prompt() reads back out of the product.


def _saved_scripts_path(products_root: Path, slug: str) -> Path:
    return products_root / slug / "saved_scripts.yaml"


def _load_saved_scripts(products_root: Path, slug: str) -> dict:
    """{lang: [{"name", "saved_at", "segments": [{"role","vo","overlay"}]}]}"""
    p = _saved_scripts_path(products_root, slug)
    if not p.exists():
        return {}
    try:
        data = read_yaml(p)
    except (ValueError, FileNotFoundError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_script(products_root: Path, slug: str, lang: str, name: str,
                 segments, photo_names=None) -> None:
    p = _saved_scripts_path(products_root, slug)
    data = _load_saved_scripts(products_root, slug)
    picks = list(photo_names or [])
    data.setdefault(lang, []).append({
        "name": name,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        # The photo each line was paired with is saved alongside the words:
        # reusing a script means reusing the whole thing, not the text with a
        # fresh set of pictures under it.
        "segments": [
            {"role": s.role, "vo": s.vo, "overlay": s.overlay,
             "photo": picks[i] if i < len(picks) else ""}
            for i, s in enumerate(segments)
        ],
    })
    write_yaml(p, data)


def _delete_saved_script(products_root: Path, slug: str, lang: str, index: int) -> None:
    p = _saved_scripts_path(products_root, slug)
    data = _load_saved_scripts(products_root, slug)
    entries = data.get(lang, [])
    if 0 <= index < len(entries):
        del entries[index]
        if entries:
            data[lang] = entries
        else:
            data.pop(lang, None)
        write_yaml(p, data)


def _form_rows(form, lang: str, prefix: str = ""):
    """`(segments, photo_names)` for one language, or `(None, None)` if the
    script wasn't edited on this page.

    The four lists come from repeated fields, which a browser submits in
    document order, so row N of each list belongs to the same segment. Rows
    with nothing to say are dropped: an empty line would still cost a photo
    and a silent beat in the finished video. The photo list is filtered in
    lockstep with them -- returning it unfiltered would shift every picture
    up by one the moment a line was blanked.

    `prefix` reads a different field set on the same page without collision
    -- the version picker embeds several scripts at once (`ver0_seg_vo_hi`,
    `ver1_seg_vo_hi`, ...) so picking one is a plain form submit carrying
    that version's exact words, never a re-generation that could hand back
    different wording than what was on screen.
    """
    vos = form.getlist(f"{prefix}seg_vo_{lang}")
    if not vos:
        return None, None
    roles = form.getlist(f"{prefix}seg_role_{lang}")
    overlays = form.getlist(f"{prefix}seg_overlay_{lang}")
    photos = form.getlist(f"{prefix}seg_photo_{lang}")
    segments, picked = [], []
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
        picked.append(photos[i].strip() if i < len(photos) else "")
    if not segments:
        return None, None
    return segments, picked


def _rows(prod: Product, segments, photo_names=None):
    """Segments as plain dicts for the templates, each carrying the photo it
    will actually be rendered over -- the same assignment `build_one` will
    make, so what the editor shows beside a line is what that line gets."""
    chosen = rf_cli._shot_photos(prod, len(segments), photo_names)
    return [
        {"role": s.role, "vo": s.vo, "overlay": s.overlay, "photo": p.name}
        for s, p in zip(segments, chosen)
    ]


def _preview(prod: Product, brand: Brand, lang: str, segments, photo_names=None) -> dict:
    return {
        "lang": lang,
        "segments": _rows(prod, segments, photo_names),
        "caption": copywriter.caption(prod, brand, lang),
    }


def _result_ctx(out_root: Path, slug: str, written) -> dict:
    """What to show on the "done" panel: the videos to play, and the caption
    text itself rather than a filename you would have to go and open.

    Every variant of the same product+lang shares one caption file (its text
    never depends on which script was used), so a multi-video build "writes"
    it several times over -- dedupe before counting anything.
    """
    names = list(dict.fromkeys(p.name for p in written))
    captions = []
    for name in (n for n in names if not n.endswith(".mp4")):
        try:
            text = (out_root / slug / name).read_text(encoding="utf-8")
        except OSError:
            continue
        captions.append({"name": name, "text": text})
    return {
        "just_built": names,
        "built_videos": [n for n in names if n.endswith(".mp4")],
        "built_captions": captions,
    }


def _form_photo_order(form, on_disk: set) -> list:
    """The photo order the user set, as a list of filenames.

    Each tile posts its filename and a position number. The number is what
    makes this work with JavaScript off -- you can simply type 1, 2, 3 and
    save -- while the drag-and-drop and the arrow buttons are just a nicer
    way of writing the same numbers. Anything unparseable keeps its current
    position rather than jumping to the front, and ties break on the order
    the tiles appear in, so a half-edited set of numbers still behaves.
    """
    names = form.getlist("photo_name")
    positions = form.getlist("photo_pos")
    ranked = []
    for i, raw_name in enumerate(names):
        name = secure_filename(raw_name)
        if name not in on_disk:
            continue
        try:
            pos = float(positions[i]) if i < len(positions) and positions[i].strip() else float(i)
        except ValueError:
            pos = float(i)
        ranked.append((pos, i, name))
    return [name for _pos, _i, name in sorted(ranked)]


def _ordered_photo_names(products_root: Path, slug: str) -> list:
    """Photo filenames in the order the video will use them.

    Reads `photo_order` straight out of the yaml rather than going through
    `Product.load`, because both callers need to work on a product that
    currently fails validation -- the build page still has to render so the
    error can be read, and the edit page is where it gets fixed.
    """
    photo_dir = products_root / slug / "photos"
    if not photo_dir.is_dir():
        return []
    try:
        wanted = read_yaml(products_root / slug / "product.yaml").get("photo_order") or []
    except (ValueError, FileNotFoundError):
        wanted = []
    if not isinstance(wanted, list):
        wanted = []
    paths = [p for p in photo_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS]
    return [p.name for p in order_photos(paths, wanted)]


def _safe_product_dir(products_root: Path, slug: str):
    """The folder for `slug`, or None if it isn't a plain product name.

    Guards the two routes that copy or delete whole directories. Only the
    exact canonical form is accepted -- every link in the app is built from a
    real slug, so anything that merely *normalises* to one (different casing,
    a trailing slash, a path segment) is a request that didn't come from this
    UI and is refused rather than quietly resolved.
    """
    cleaned = _clean_slug(slug)
    if not cleaned or cleaned != slug:
        return None
    target = (products_root / cleaned).resolve()
    if target.parent != products_root.resolve():
        return None
    return target


def _next_copy_slug(products_root: Path, slug: str) -> str:
    base = f"{slug}-copy"
    candidate, n = base, 2
    while (products_root / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _asset_exists(brand_path: Path, value) -> bool:
    if not value:
        return False
    p = Path(str(value))
    return (p if p.is_absolute() else brand_path.parent / p).exists()


def _save_brand_asset(brand_path: Path, current, key: str, label: str,
                      subdir: str, exts) -> str:
    """Handle the upload/remove pair for one file-shaped brand setting.

    Returns the value to store in brand.yaml: a path relative to brand.yaml
    itself, so the folder can be moved or handed to someone else and still
    work. `Brand.load` refuses to load a brand pointing at a missing file, so
    an upload that didn't land must never be recorded.
    """
    if request.form.get(f"remove_{key}") == "on":
        return ""
    upload = request.files.get(f"{key}_file")
    if not upload or not upload.filename:
        return current or ""

    name = secure_filename(upload.filename)
    ext = Path(name).suffix.lower()
    if ext not in exts:
        raise ValueError(
            f"{label}: {ext or 'that file'} isn't a supported format. "
            f"Use one of {', '.join(sorted(exts))}."
        )
    dest_dir = brand_path.parent / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    upload.save(str(dest))
    return f"{subdir}/{name}"


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
