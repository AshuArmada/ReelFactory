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
import json
import secrets
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import yaml
from flask import Flask, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from .. import cli as rf_cli
from .. import preflight
from .. import photo_analysis
from .. import script as copywriter
from .. import stock
from .. import templates as rf_templates
from ..ad_prompt import ALL_ROLES
from ..config import (
    Brand, CTA_ACTIONS, IMAGE_EXTS, INTENTS, MEDIA_EXTS, Product, TONES, VIDEO_EXTS,
    next_photo_index, order_photos, read_yaml, write_yaml,
)
from ..script import Segment
from ..gemini import GeminiError
from ..grok import GrokError
from ..local_llm import LocalLLMError
from ..render import ASPECTS, RenderError, photo_advice
from ..stock import StockError
from ..voice import TTSError
from .diagnostics import configure_diagnostics, record_failure

LANGS = list(copywriter.LANGS)
# Roles an edited line may carry. The role picks the on-screen style, so it is
# a closed list -- "custom" is the neutral body style, used for hand-added lines.
SEGMENT_ROLES = list(ALL_ROLES) + ["custom"]
VARIANT_COUNT = 3
# One screenful of stock results. Enough to choose from without a wall of
# thumbnails, and small enough that both free API tiers stay comfortable.
STOCK_COUNT = 24

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

PRODUCT_FORM_FIELDS = {
    "name_en", "name_hi", "price", "old_price", "tone", "intent", "template",
    "seed", "cta_action", "cta_detail", "cta_detail_hi", "target_seconds",
    "category", "audience", "audience_hi", "occasion", "occasion_hi", "offer",
    "offer_hi", "offer_ends", "offer_ends_hi", "urgency", "urgency_hi",
    "usp_en", "usp_hi", "hashtags", "proof_points", "proof_points_hi",
    "must_say", "must_say_hi", "avoid", "specs", "specs_hi", "script_en",
    "script_hi", "overlay_en", "overlay_hi",
} | {key for key, _ in PRODUCT_LANG_FIELDS} | {
    f"{key}_hi" for key, _ in PRODUCT_LANG_FIELDS
}


def create_app(brand_path: Path, products_root: Path, out_root: Path) -> Flask:
    app = Flask(__name__)
    app.secret_key = "reel-factory-local"  # local tool only; flash messages, not real sessions
    configure_diagnostics(app, brand_path.parent)

    @app.before_request
    def reject_noncanonical_slugs():
        """Never let an encoded dot/backslash segment escape a configured root."""
        slug = (request.view_args or {}).get("slug")
        if slug is not None and (
            _safe_child_dir(products_root, slug) is None
            or _safe_child_dir(out_root, slug) is None
        ):
            return "No such product or output folder.", 404

    app.jinja_env.filters["as_lines"] = lambda v: "\n".join(v) if isinstance(v, list) else (v or "")
    app.jinja_env.filters["is_clip"] = lambda n: Path(str(n)).suffix.lower() in VIDEO_EXTS
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
            voice_rate=(form.get("voice_rate", "") if form else ""),
            voice_delivery=(form.get("voice_delivery", "") if form else ""),
            preset=(form.get("preset") if form else None) or "medium",
            template=(form.get("template") if form else None) or "",
            no_music=(form.get("no_music") == "on") if form else False,
        )

        photo_names = _ordered_photo_names(products_root, slug)
        return dict(
            slug=slug, langs=LANGS, aspects=list(ASPECTS),
            script_choices=rf_cli.SCRIPT_CHOICES, tts_choices=rf_cli.TTS_CHOICES,
            presets=rf_cli.PRESETS, outputs=_list_outputs(out_root / slug),
            chosen=chosen, roles=SEGMENT_ROLES, variant_count=VARIANT_COUNT,
            saved_scripts=_load_saved_scripts(products_root, slug),
            # The script editor shows the photo each line will be rendered
            # over, so it needs the same ordered list the build will use.
            product_photos=photo_names,
            photo_notes=_photo_notes(products_root / slug / "photos", photo_names),
            template_names=rf_templates.available(),
        )

    def _product_form_ctx(**extra) -> dict:
        return dict(
            tones=TONES, lang_fields=PRODUCT_LANG_FIELDS, intents=INTENTS,
            cta_actions=CTA_ACTIONS, template_names=rf_templates.available(),
            media_accept=",".join(sorted(MEDIA_EXTS)), **extra,
        )

    def _photo_analysis_ctx(prod_dir: Path, names) -> dict:
        info = photo_analysis.status(prod_dir, names)
        return {"photo_analysis": info}

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
            try:
                raw = read_yaml(brand_path) if brand_path.exists() else {}
            except ValueError as exc:
                return _repair_page("brand", brand_path, str(exc))
        defaults = Brand()
        for key, _label in BRAND_AI_FIELDS:
            if not raw.get(key):
                raw[key] = getattr(defaults, key)
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
            template_names=rf_templates.available(),
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

    def _repair_page(kind: str, path: Path, error: str = "", status: int = 200,
                     source: str | None = None):
        if source is None:
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                source = ""
        return render_template(
            "config_repair.html", kind=kind, source=source, error=error,
            slug=(path.parent.name if kind == "product" else ""),
        ), status

    def _repair_yaml(kind: str, path: Path, source: str):
        try:
            data = yaml.safe_load(source) or {}
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "the YAML"
            return None, f"Still not valid at {where}: {getattr(exc, 'problem', None) or exc}."
        if not isinstance(data, dict):
            return None, "The file must contain a mapping of setting names to values."
        known = set(Brand.__dataclass_fields__) if kind == "brand" else (
            set(Product.__dataclass_fields__) - {"slug", "dir", "photos"}
        )
        unknown = sorted(set(data) - known)
        if unknown:
            return None, f"Remove or correct unknown setting(s): {', '.join(unknown)}."
        if kind == "product":
            missing = [key for key in ("name_en", "name_hi") if not data.get(key)]
            if missing:
                return None, f"Required setting(s) are missing: {', '.join(missing)}."
        return data, ""

    @app.post("/brand/repair")
    def brand_repair():
        source = request.form.get("source", "")
        data, error = _repair_yaml("brand", brand_path, source)
        if error:
            return _repair_page("brand", brand_path, error, 400, source)
        # The repair editor is deliberately raw YAML; keep the user's comments
        # and layout after validation instead of normalising them away.
        brand_path.write_text(source, encoding="utf-8")
        return redirect(url_for("brand_edit"))

    @app.post("/brand")
    def brand_save():
        current = read_yaml(brand_path) if brand_path.exists() else {}
        # Saving the visible form is also the recovery path for a hand-edited
        # file with an obsolete/typo key: retain known settings only.
        raw = {k: v for k, v in current.items() if k in Brand.__dataclass_fields__}
        for key, _ in BRAND_TEXT_FIELDS + BRAND_COLOR_FIELDS + BRAND_VOICE_FIELDS + BRAND_AI_FIELDS + BRAND_DEFAULT_FIELDS:
            raw[key] = request.form.get(key, "").strip()
        defaults = Brand()
        for key, _label in BRAND_AI_FIELDS:
            if not raw[key]:
                raw[key] = getattr(defaults, key)
        for key, _label, _hint in BRAND_FONT_FIELDS:
            raw[key] = request.form.get(key, "").strip()
        default_intent = request.form.get("default_intent", "sell").strip()
        raw["default_intent"] = default_intent if default_intent in INTENTS else "sell"
        chosen = request.form.get("default_template", "").strip()
        raw["default_template"] = chosen if chosen in rf_templates.available() else ""
        raw["watermark"] = request.form.get("watermark") == "on"
        # Normalise on the way out too, so one bad value can't stay in the file.
        raw["music_volume"] = _as_volume(
            request.form.get("music_volume"), _as_volume(raw.get("music_volume"))
        )
        raw["music_bpm"] = _as_nonnegative_float(request.form.get("music_bpm"), 0.0)
        raw["music_offset"] = _as_float(request.form.get("music_offset"), 0.0)

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
        return render_template("product_edit.html", **_product_form_ctx(
            is_new=True, slug="", data={}, photos=[]))

    @app.post("/products/new")
    def product_create():
        slug = _clean_slug(request.form.get("slug", ""))
        if not slug:
            return render_template("product_edit.html", **_product_form_ctx(
                is_new=True, slug="", data=request.form, photos=[],
                error="Give the product a folder name using only lowercase letters, numbers and dashes.")), 400
        prod_dir = products_root / slug
        if prod_dir.exists():
            return render_template("product_edit.html", **_product_form_ctx(
                is_new=True, slug=slug, data=request.form, photos=[],
                error=f"A product folder named '{slug}' already exists.")), 400
        uploads = request.files.getlist("photos")
        rejected = _unsupported_uploads(uploads)
        if rejected:
            return render_template("product_edit.html", **_product_form_ctx(
                is_new=True, slug=slug, data=request.form, photos=[],
                error=_upload_error(rejected))), 400
        (prod_dir / "photos").mkdir(parents=True)
        data = _form_to_product_dict(request.form)
        write_yaml(prod_dir / "product.yaml", data)
        _save_uploaded_photos(prod_dir / "photos", uploads)
        return redirect(url_for("product_edit", slug=slug))

    @app.get("/products/<slug>/edit")
    def product_edit(slug):
        prod_dir = products_root / slug
        spec = prod_dir / "product.yaml"
        if not spec.exists():
            return f"No product named '{slug}'.", 404
        try:
            data = read_yaml(spec)
        except ValueError as exc:
            return _repair_page("product", spec, str(exc))
        photos = _ordered_photo_names(products_root, slug)
        return render_template("product_edit.html", **_product_form_ctx(
            is_new=False, slug=slug, data=data, photos=photos,
            notice=request.args.get("notice", ""),
            start_step=1 if request.args.get("step") == "photos" else 0,
            photo_notes=_photo_notes(prod_dir / "photos", photos),
            photo_credits=stock.load_credits(prod_dir),
            **_photo_analysis_ctx(prod_dir, photos),
        ))

    @app.post("/products/<slug>/repair")
    def product_repair(slug):
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None or not (prod_dir / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        spec = prod_dir / "product.yaml"
        source = request.form.get("source", "")
        data, error = _repair_yaml("product", spec, source)
        if error:
            return _repair_page("product", spec, error, 400, source)
        spec.write_text(source, encoding="utf-8")
        return redirect(url_for("product_edit", slug=slug))

    @app.post("/products/<slug>/edit")
    def product_update(slug):
        prod_dir = products_root / slug
        spec = prod_dir / "product.yaml"
        if not spec.exists():
            return f"No product named '{slug}'.", 404
        raw = {
            k: v for k, v in read_yaml(spec).items()
            if k in Product.__dataclass_fields__ and k not in {"slug", "dir", "photos"}
        }
        uploads = request.files.getlist("photos")
        rejected = _unsupported_uploads(uploads)
        if rejected:
            photos = _ordered_photo_names(products_root, slug)
            return render_template("product_edit.html", **_product_form_ctx(
                is_new=False, slug=slug, data=request.form, photos=photos,
                start_step=1,
                photo_notes=_photo_notes(prod_dir / "photos", photos),
                photo_credits=stock.load_credits(prod_dir),
                **_photo_analysis_ctx(prod_dir, photos), error=_upload_error(rejected))), 400
        # Empty controls mean "remove this override". Drop every setting the
        # form owns before merging its non-empty representation.
        for key in PRODUCT_FORM_FIELDS:
            raw.pop(key, None)
        raw.update(_form_to_product_dict(request.form))

        photo_dir = prod_dir / "photos"
        removed = []
        for name in request.form.getlist("delete_photo"):
            target = photo_dir / secure_filename(name)
            if target.exists() and target.parent == photo_dir:
                target.unlink()
                removed.append(target.name)
        _save_uploaded_photos(photo_dir, uploads)
        # A deleted photo's provenance record has nothing left to describe, and
        # the numbering reuses filenames -- a stale entry would eventually be
        # read as the credit for a different photo altogether.
        if removed:
            stock.forget_credits(prod_dir, removed)

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
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None:
            return "No such product.", 404
        return send_from_directory(prod_dir / "photos", filename)

    @app.post("/products/<slug>/photos/analyze")
    def product_photos_analyze(slug):
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None or not (prod_dir / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
            result = photo_analysis.analyze(prod, brand)
            note = f"Analyzed {len(result['photos'])} photo(s). Review the combined summary below."
        except (ValueError, FileNotFoundError, GeminiError) as exc:
            note = f"Photo analysis failed: {exc}"
        return redirect(url_for("product_edit", slug=slug, step="photos", notice=note))

    @app.post("/products/<slug>/photos/summary")
    def product_photo_summary_save(slug):
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None or not (prod_dir / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        try:
            photo_analysis.update_group_summary(
                prod_dir, request.form.get("group_summary", "")
            )
            note = "Saved the combined photo summary."
        except ValueError as exc:
            record_failure(exc)
            note = str(exc)
        return redirect(url_for("product_edit", slug=slug, step="photos", notice=note))

    @app.post("/products/<slug>/photos/summary/archive")
    def product_photo_summary_archive(slug):
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None or not (prod_dir / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        try:
            name = request.form.get("summary_name", "").strip()
            summary = request.form.get("group_summary", "").strip()
            if not name:
                raise ValueError("Give the photo summary a name before saving it for future use.")
            if len(name) > 80:
                raise ValueError("Photo summary names must be 80 characters or fewer.")
            if not summary:
                raise ValueError("The combined photo summary cannot be blank.")
            # Save the textarea first so one click preserves the correction
            # currently on screen as well as creating the named snapshot.
            photo_analysis.update_group_summary(prod_dir, summary)
            entry = photo_analysis.save_snapshot(prod_dir, name)
            note = f"Saved photo summary '{entry['name']}' for future use."
        except ValueError as exc:
            record_failure(exc)
            note = str(exc)
        return redirect(url_for("product_edit", slug=slug, step="photos", notice=note))

    @app.post("/products/<slug>/photos/summary/restore")
    def product_photo_summary_restore(slug):
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None or not (prod_dir / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        try:
            index = int(request.form.get("summary_pick", ""))
            entry = photo_analysis.restore_snapshot(prod_dir, index)
            note = f"Restored photo summary '{entry.get('name', 'saved summary')}'."
        except (TypeError, ValueError):
            note = "That saved photo summary could not be found."
        return redirect(url_for("product_edit", slug=slug, step="photos", notice=note))

    @app.post("/products/<slug>/photos/summary/delete")
    def product_photo_summary_delete(slug):
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None or not (prod_dir / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        try:
            index = int(request.form.get("summary_pick", ""))
            entry = photo_analysis.delete_snapshot(prod_dir, index)
            note = f"Deleted saved photo summary '{entry.get('name', 'saved summary')}'."
        except (TypeError, ValueError):
            note = "That saved photo summary could not be found."
        return redirect(url_for("product_edit", slug=slug, step="photos", notice=note))

    # ----------------------------------------------------------- stock photos

    def _stock_ctx(slug: str, form=None) -> dict:
        return dict(
            slug=slug,
            query=(form.get("query", "").strip() if form else ""),
            orientation=(form.get("orientation") if form else None) or stock.DEFAULT_ORIENTATION,
            orientations=list(stock.ORIENTATIONS),
            sources=list(stock.SOURCES),
            chosen_sources=(form.getlist("source") if form else None) or list(stock.SOURCES),
            sharp=(form.get("sharp") == "on") if form else True,
            available=stock.configured(),
            setup_help=stock.setup_help(),
            have_photos=len(_ordered_photo_names(products_root, slug)),
        )

    @app.get("/products/<slug>/photos/stock")
    def stock_find(slug):
        if not (products_root / slug / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        return render_template("stock_photos.html", **_stock_ctx(slug))

    @app.post("/products/<slug>/photos/stock")
    def stock_search(slug):
        if not (products_root / slug / "product.yaml").exists():
            return f"No product named '{slug}'.", 404
        ctx = _stock_ctx(slug, request.form)
        sharp = ctx["sharp"]
        results, error = [], None
        try:
            # Over-fetch when the size filter is on, so ticking "big enough"
            # still fills the screen instead of returning three photos.
            results = stock.search(
                ctx["query"], count=STOCK_COUNT * (3 if sharp else 1),
                sources=ctx["chosen_sources"], orientation=ctx["orientation"],
            )
            if sharp:
                results = stock.only_sharp(results)
            results = results[:STOCK_COUNT]
        except StockError as exc:
            record_failure(exc)
            error = str(exc)
        return render_template(
            "stock_photos.html", **ctx, results=results,
            notes=stock.review(results), searched=not error, error=error,
        )

    @app.post("/products/<slug>/photos/stock/add")
    def stock_add(slug):
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None or not (prod_dir / "product.yaml").exists():
            return f"No product named '{slug}'.", 404

        # The chosen photos travel back as the hidden fields the results page
        # rendered, and only their URLs are fetched -- never a fresh search.
        # Re-running the query here could hand back a different set than the
        # one that was ticked, and stock APIs genuinely do reorder results.
        shown = _stock_rows(request.form)
        picked = set(request.form.getlist("pick"))
        chosen = [p for p in shown if p.key in picked]
        if not chosen:
            return render_template(
                "stock_photos.html", **_stock_ctx(slug, request.form),
                results=shown, notes=stock.review(shown), searched=True,
                error="Tick the photos you want before adding them.",
            ), 400

        skipped = []
        saved = stock.download(
            chosen, prod_dir / "photos",
            on_progress=lambda photo, path, err: err and skipped.append(err),
        )
        if saved:
            stock.record_credits(prod_dir, saved, request.form.get("query", ""))
        note = (f"Added {len(saved)} photo{'s' if len(saved) != 1 else ''} to the end of the list."
                if saved else "Nothing could be downloaded.")
        if skipped:
            note += f" {len(skipped)} could not be fetched: {skipped[0]}"
        return redirect(url_for("product_edit", slug=slug, notice=note))

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
            voice_rate=request.form.get("voice_rate", "") if request.form.get("voice_rate", "") in ("", "-10%", "+0%", "+6%") else "",
            voice_delivery=request.form.get("voice_delivery", "").strip()[:1500],
            preset=request.form.get("preset", "medium"),
            no_music=request.form.get("no_music") == "on",
            script=request.form.get("script", "template"),
            steer=request.form.get("steer", ""),
            template=request.form.get("template") or None,
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
                record_failure(exc)
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
            record_failure(exc)
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
                previous, kept = _form_rows(request.form, lang)
                target_lang = request.form.get("rewrite_lang", "")
                if target_lang and target_lang != lang and previous:
                    previews.append(_preview(prod, brand, lang, previous, kept))
                    continue
                rewrite_prod = _rewrite_context(prod, lang, args, request.form, previous)
                segments = rf_cli._build_segments(rewrite_prod, brand, lang, args)
                # A fresh draft has no photo choices of its own, but the ones
                # already on screen were deliberate -- carry them across by
                # position so asking for new words doesn't silently reshuffle
                # the pictures too.
                _prev, kept = _form_rows(request.form, lang)
                previews.append(_preview(prod, brand, lang, segments, kept))
        except (ValueError, GeminiError, GrokError, LocalLLMError) as exc:
            record_failure(exc)
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
                previous, pics = _form_rows(request.form, lang)
                rewrite_prod = _rewrite_context(prod, lang, args, request.form, previous)
                drafts = rf_cli._build_segment_variants(rewrite_prod, brand, lang, args, n=VARIANT_COUNT)
                versions[lang] = [_rows(prod, segs, pics) for segs in drafts]
        except (ValueError, GeminiError, GrokError, LocalLLMError) as exc:
            record_failure(exc)
            error = str(exc)

        return render_template(
            "build.html", **_build_page_ctx(slug, request.form),
            **_preview_ctx([
                _preview(prod, brand, lang, segs, pics)
                for lang in langs
                for segs, pics in [_form_rows(request.form, lang)] if segs
            ] if error else [], request.form), versions={} if error else versions, error=error,
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
                    _save_script(
                        products_root, slug, lang, name, segs, pics,
                        writer=request.form.get("script", "template"),
                        instructions=request.form.get("steer", "").strip(),
                    )

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
        restored = request.form.copy()
        restored.setlist("lang", [lang])
        restored["script"] = entry.get("writer") or "template"
        restored["steer"] = entry.get("instructions", "")
        return render_template(
            "build.html", **_build_page_ctx(slug, restored),
            **_preview_ctx(previews, restored),
        )

    @app.post("/products/<slug>/script/saved/delete")
    def script_saved_delete(slug):
        prod_dir = _safe_product_dir(products_root, slug)
        if prod_dir is None or not (prod_dir / "product.yaml").exists():
            return "No such product.", 404
        try:
            prod = Product.load(prod_dir)
            brand = Brand.load(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            return render_template("build.html", **_build_page_ctx(slug, request.form),
                                   start_step=1, error=str(exc)), 400

        # Deleting a library entry must not discard the separate working draft.
        state = _posted_script_ctx(prod, brand, request.form)
        lang, _, idx_text = request.form.get("delete_pick", "").partition(":")
        deleted = lang in LANGS and idx_text.isdigit() and _delete_saved_script(
            products_root, slug, lang, int(idx_text))
        return render_template(
            "build.html", **_build_page_ctx(slug, request.form), **state,
            start_step=1, saved_scripts_open=True, script_deleted=bool(deleted),
            error=None if deleted else "That saved script could not be found — it may already have been deleted.",
        )

    @app.get("/out/<slug>/<path:filename>")
    def output_file(slug, filename):
        out_dir = _safe_child_dir(out_root, slug)
        if out_dir is None:
            return "No such output folder.", 404
        return send_from_directory(out_dir, filename)

    @app.post("/products/<slug>/build/delete")
    def output_delete(slug):
        out_dir = _safe_child_dir(out_root, slug)
        if out_dir is None:
            return "No such output folder.", 404
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
        (p.name for p in photo_dir.iterdir() if p.suffix.lower() in MEDIA_EXTS),
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
                 segments, photo_names=None, writer: str = "", instructions: str = "") -> None:
    p = _saved_scripts_path(products_root, slug)
    data = _load_saved_scripts(products_root, slug)
    picks = list(photo_names or [])
    data.setdefault(lang, []).append({
        "name": name,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "writer": writer,
        "instructions": instructions,
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


def _delete_saved_script(products_root: Path, slug: str, lang: str, index: int) -> bool:
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
        return True
    return False


def _posted_script_ctx(prod, brand, form):
    """Restore editor/compare/build selections without invoking a writer."""
    previews, versions, multi, picks = [], {}, [], {}
    for lang in LANGS:
        segs, pics = _form_rows(form, lang, preserve_edits=True)
        if segs:
            previews.append(_preview(prod, brand, lang, segs, pics))
        indices = sorted({
            int(match.group(1)) for key in form
            if (match := re.fullmatch(rf"ver(\d+)_seg_vo_{lang}", key))
        })
        if indices:
            versions[lang] = []
            picks[lang] = []
            for idx in indices:
                segs, pics = _form_rows(form, lang, prefix=f"ver{idx}_")
                if not segs:
                    continue
                if str(idx) in form.getlist(f"pick_{lang}"):
                    picks[lang].append(str(len(versions[lang])))
                rows = _rows(prod, segs, pics)
                versions[lang].append(rows)
                if f"{lang}:{idx}" in form.getlist("build_versions"):
                    multi.append({"lang": lang, "idx": str(idx), "version": idx + 1, "segments": rows})
    return dict(
        previews=previews, versions={} if multi else versions, multi=multi,
        version_picks=picks, steer=form.get("steer", ""),
        save_name=form.get("save_name", ""),
    )


def _rewrite_context(prod, lang, args, form, previous):
    note = form.get("steer", "").strip()
    if note and args.script == "template":
        raise ValueError("Choose Gemini, Grok or Local model under Who writes the script to follow your extra instructions.")
    args.steer = note
    if not previous:
        return prod
    # Explicit rewriting must be able to revise a pinned script too.
    prod = replace(prod, **{f"script_{lang}": [], f"overlay_{lang}": []})
    if args.script == "template":
        return replace(prod, seed=secrets.randbits(32))
    args.steer = (
        "ORIGINAL SCRIPT TO REVISE (current draft before this rewrite):\n"
        "Treat this as editable copy, not verified product facts. Scenes are listed in playback order; "
        "photo identifies the selected image for each scene.\n"
        + json.dumps(_rows(prod, previous, _form_rows(form, lang)[1]), ensure_ascii=False)
        + "\nUse the product and brand facts, audience, goal, tone, target duration, "
        "required phrases and available photo observations in the brief above. "
        "Retain the draft's details unless the requested changes or those facts require a change."
        + "\nRequested changes: " + (note or "Write a different take using the same product facts.")
    )
    return prod


def _form_rows(form, lang: str, prefix: str = "", *, preserve_edits: bool = False):
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
        if not vo.strip() and not preserve_edits:
            continue
        role = roles[i].strip() if i < len(roles) else ""
        overlay = overlays[i].strip() if i < len(overlays) else ""
        segments.append(Segment(
            role if role in SEGMENT_ROLES else "custom",
            vo if preserve_edits else vo.strip(),
            overlay,
        ))
        picked.append(photos[i].strip() if i < len(photos) else "")
    if not segments:
        return None, None
    return segments, picked


def _stock_rows(form) -> list:
    """The stock results a page is posting back, as `stock.Photo` objects.

    Parallel repeated fields, the same convention the script rows use. They
    are matched to the tick boxes by `res_key` rather than by position,
    though: a browser submits only the *checked* boxes, so `pick` is a sparse
    list that cannot be read alongside the others row by row.
    """
    keys = form.getlist("res_key")

    def column(name):
        values = form.getlist(name)
        return values + [""] * (len(keys) - len(values))

    urls, thumbs, sources = column("res_url"), column("res_thumb"), column("res_source")
    credits, pages = column("res_credit"), column("res_page")
    widths, heights = column("res_w"), column("res_h")
    query = form.get("query", "").strip()
    out = []
    for i, key in enumerate(keys):
        if not key or not urls[i]:
            continue
        out.append(stock.Photo(
            key=key, url=urls[i], thumb=thumbs[i] or urls[i], source=sources[i],
            credit=credits[i], page=pages[i],
            width=_as_int(widths[i]), height=_as_int(heights[i]), query=query,
        ))
    return out


def _as_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


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


def _photo_notes(photo_dir: Path, names) -> dict:
    """{filename: PhotoNote} for photos the render will struggle with.

    Keyed by name so a template can look one up beside its own tile. Only
    photos with something to say are included, so `{% if notes.get(name) %}`
    is all a template needs.
    """
    paths = [photo_dir / n for n in names if (photo_dir / n).suffix.lower() in IMAGE_EXTS]
    return {n.name: n for n in photo_advice(paths) if n.problems}


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
    paths = [p for p in photo_dir.iterdir() if p.suffix.lower() in MEDIA_EXTS]
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


def _safe_child_dir(root: Path, name: str):
    """Resolve one canonical slug directly below ``root``."""
    cleaned = _clean_slug(name)
    if not cleaned or cleaned != name:
        return None
    target = (root / cleaned).resolve()
    return target if target.parent == root.resolve() else None


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


def _as_float(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_nonnegative_float(value, fallback: float = 0.0) -> float:
    return max(0.0, _as_float(value, fallback))


def _clean_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", text.strip().lower()).strip("-")
    return slug


def _unsupported_uploads(files) -> list[str]:
    return [
        secure_filename(f.filename) or "unnamed file"
        for f in files if f and f.filename
        and Path(secure_filename(f.filename)).suffix.lower() not in MEDIA_EXTS
    ]


def _upload_error(names: list[str]) -> str:
    return (
        f"Unsupported photo or clip: {', '.join(names)}. "
        f"Use one of {', '.join(sorted(MEDIA_EXTS))}."
    )


def _save_uploaded_photos(photo_dir: Path, files) -> None:
    next_n = next_photo_index(photo_dir)
    for f in files:
        if not f or not f.filename:
            continue
        ext = Path(secure_filename(f.filename)).suffix.lower()
        # Callers validate the complete batch before making any changes.
        if ext not in MEDIA_EXTS:
            raise ValueError(_upload_error([secure_filename(f.filename)]))
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
        "template": form.get("template", "").strip(),
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
    if form.get("template") not in rf_templates.available():
        data["template"] = ""
    target = form.get("target_seconds", "").strip()
    if target.isdigit():
        data["target_seconds"] = int(target)
    seed = form.get("seed", "").strip()
    if re.fullmatch(r"-?\d+", seed):
        data["seed"] = int(seed)

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
    data["script_en"] = _lines(form.get("script_en", ""))
    data["script_hi"] = _lines(form.get("script_hi", ""))
    data["overlay_en"] = _lines(form.get("overlay_en", ""))
    data["overlay_hi"] = _lines(form.get("overlay_hi", ""))
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
