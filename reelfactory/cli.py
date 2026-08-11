"""Command line entry point.

    python -m reelfactory script  products/my-rack          preview the copy
    python -m reelfactory build   products/my-rack          render videos
    python -m reelfactory plan    products --start tomorrow generate a schedule
    python -m reelfactory queue                             see what is scheduled
    python -m reelfactory run                               render + publish what is due
"""
from __future__ import annotations

import argparse
import dataclasses
import shutil
import sys
import tempfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from . import ai_script
from . import calendar as cal
from . import grok_script
from . import local_script
from . import script as copywriter
from . import subtitles, voice
from .config import Brand, INTENTS, Product
from .gemini import GeminiError
from .grok import GrokError
from .local_llm import LocalLLMError
from .render import ASPECTS, RenderError, Shot, plan as plan_shots, render, validate_photos
from .runner import Runner
from .voice import TTSError

ROOT = Path(__file__).resolve().parent.parent
TTS_CHOICES = ["edge", "gtts", "gemini", "silent"]
SCRIPT_CHOICES = ["template", "ai", "grok", "local"]
PRESETS = ["ultrafast", "veryfast", "faster", "medium", "slow"]
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="reelfactory", description="Turn product photos into narrated social videos."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("script", help="print the generated copy without rendering")
    s.add_argument("products", nargs="+")
    s.add_argument("--brand", default=str(ROOT / "brand.yaml"))
    s.add_argument("--lang", default="hi,en")
    _script_flags(s)

    b = sub.add_parser("build", help="render videos for one or more products")
    b.add_argument("products", nargs="+", help="folder(s) with product.yaml and photos/")
    b.add_argument("--brand", default=str(ROOT / "brand.yaml"))
    b.add_argument("--lang", default="hi,en", help="comma separated: hi,en")
    b.add_argument("--aspect", default="9:16", help=f"comma separated: {','.join(ASPECTS)}")
    _render_flags(b)
    b.add_argument("--out", default=str(ROOT / "out"))
    b.add_argument("--keep-temp", action="store_true", help="keep intermediates for debugging")

    p = sub.add_parser("plan", help="generate calendar entries for a set of products")
    p.add_argument("products", nargs="+")
    p.add_argument("--start", default="tomorrow", help="'today', 'tomorrow' or YYYY-MM-DD")
    p.add_argument("--time", default="19:00", help="posting time, HH:MM")
    p.add_argument("--days", default="mon,wed,fri", help="which weekdays to post on")
    p.add_argument("--lang", default="hi")
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--platform", default="dryrun", choices=list(cal.PLATFORMS))
    p.add_argument("--repeat", type=int, default=1, help="how many passes over the products")
    p.add_argument("--write", metavar="FILE", help="append the entries to this file")

    q = sub.add_parser("queue", help="show the schedule and what has already gone out")
    q.add_argument("--calendar", default=str(ROOT / "calendar.yaml"))
    q.add_argument("--state", default=str(ROOT / "out" / "queue_state.json"))
    q.add_argument("--all", action="store_true", help="include entries already dealt with")
    q.add_argument("--grace", type=int, default=48, help="match the value used by 'run'")

    r = sub.add_parser("run", help="render and publish everything that is due")
    r.add_argument("--calendar", default=str(ROOT / "calendar.yaml"))
    r.add_argument("--state", default=str(ROOT / "out" / "queue_state.json"))
    r.add_argument("--brand", default=str(ROOT / "brand.yaml"))
    r.add_argument("--products", default=str(ROOT / "products"))
    r.add_argument("--out", default=str(ROOT / "out"))
    r.add_argument("--drop", default=str(ROOT / "to_post"), help="folder for 'folder' posts")
    r.add_argument("--now", help="pretend it is this time, for testing")
    r.add_argument("--lookahead", type=int, default=2, help="days ahead to pre-render")
    r.add_argument("--grace", type=int, default=48, help="skip posts missed by more than this")
    r.add_argument("--retries", type=int, default=3)
    r.add_argument("--prepare-only", action="store_true", help="render ahead, publish nothing")
    _render_flags(r)

    w = sub.add_parser("serve", help="start the local web UI for entering products and photos")
    w.add_argument("--brand", default=str(ROOT / "brand.yaml"))
    w.add_argument("--products", default=str(ROOT / "products"))
    w.add_argument("--out", default=str(ROOT / "out"))
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=5000)
    w.add_argument("--debug", action="store_true")

    args = ap.parse_args(argv)
    try:
        return DISPATCH[args.cmd](args)
    except (ValueError, FileNotFoundError, TTSError, RenderError, GeminiError, GrokError, LocalLLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _render_flags(parser) -> None:
    parser.add_argument("--tts", default="edge", choices=TTS_CHOICES,
                         help="'gemini' needs a Gemini API key, see --gemini-key")
    parser.add_argument("--preset", default="medium", choices=PRESETS)
    parser.add_argument("--no-music", action="store_true")
    _script_flags(parser)


def _script_flags(parser) -> None:
    parser.add_argument("--script", default="template", choices=SCRIPT_CHOICES,
                         help="'template' (offline, free), 'ai' (Gemini-written), 'grok' (Grok-written) "
                              "or 'local' (written by a local model server, e.g. Ollama/LM Studio)")
    parser.add_argument("--gemini-key", default=None,
                         help="Gemini API key; defaults to the GEMINI_API_KEY environment variable")
    parser.add_argument("--gemini-backup-key", default=None,
                         help="Second Gemini key, used automatically if the primary key hits a quota limit")
    parser.add_argument("--grok-key", default=None,
                         help="Grok API key; defaults to the GROK_API_KEY environment variable")
    parser.add_argument("--local-url", default=None,
                         help="Base URL of the local model server; defaults to brand.yaml's "
                              "local_base_url (http://localhost:11434/v1, Ollama's default)")
    parser.add_argument("--local-model", default=None,
                         help="Model name to request from the local server; defaults to brand.yaml's "
                              "local_script_model")
    parser.add_argument("--local-key", default=None,
                         help="API key for the local server, if it requires one (most don't); "
                              "defaults to the LOCAL_LLM_API_KEY environment variable")
    parser.add_argument("--intent", default=None, choices=sorted(INTENTS),
                         help="what this video is for, overriding product.yaml: "
                              + "; ".join(f"{k} ({v})" for k, v in INTENTS.items()))
    parser.add_argument("--steer", default=None, metavar="NOTE",
                         help="a plain-language note telling the writer what to change, e.g. "
                              "\"shorter, and lead with the price\". Applies to --script "
                              "ai/grok/local; the offline template writer ignores it.")


# --------------------------------------------------------------------- commands


def cmd_script(args) -> int:
    brand = Brand.load(args.brand)
    langs = _split(args.lang, copywriter.LANGS, "language")
    for prod in [Product.load(x) for x in _expand(args.products)]:
        for lang in langs:
            print("=" * 58)
            tag = _script_tag(args.script, _effective_intent(prod, brand, args))
            print(f"{prod.slug}  [{lang}]{tag}")
            print("=" * 58)
            for i, seg in enumerate(_build_segments(prod, brand, lang, args), 1):
                print(f"{i:2d}. ({seg.role}) {seg.vo}")
                print(f"     on screen: {seg.overlay}")
            print("\n--- caption ---")
            print(copywriter.caption(prod, brand, lang))
            print()
    return 0


def cmd_build(args) -> int:
    brand = Brand.load(args.brand)
    langs = _split(args.lang, copywriter.LANGS, "language")
    aspects = _split(args.aspect, ASPECTS, "aspect ratio")
    products = [Product.load(x) for x in _expand(args.products)]
    _warn_font(langs, brand)

    outroot = Path(args.out)
    outroot.mkdir(parents=True, exist_ok=True)
    made, failed = [], []
    for prod in products:
        for lang in langs:
            try:
                made += build_one(prod, brand, lang, aspects, outroot, args)
            except (TTSError, RenderError, ValueError, FileNotFoundError, GeminiError, GrokError, LocalLLMError) as exc:
                failed.append(f"{prod.slug} [{lang}]: {exc}")
                print(f"\n  FAILED {prod.slug} [{lang}]\n  {exc}\n", file=sys.stderr)

    print("\n" + "=" * 58)
    for f in made:
        print(f"  ready  {f}")
    if failed:
        print(f"\n  {len(failed)} build(s) failed:")
        for f in failed:
            print(f"    - {f}")
    print("=" * 58)
    return 1 if failed else 0


def cmd_plan(args) -> int:
    langs = _split(args.lang, copywriter.LANGS, "language")
    _split(args.aspect, ASPECTS, "aspect ratio")
    slugs = [Path(x).name for x in _expand(args.products)]
    if not slugs:
        raise ValueError("No products found to schedule.")

    days = [d.strip().lower() for d in args.days.split(",") if d.strip()]
    bad = [d for d in days if d not in WEEKDAYS]
    if bad:
        raise ValueError(f"Unknown weekday(s): {', '.join(bad)}. Use mon,tue,wed,thu,fri,sat,sun.")
    wanted = {WEEKDAYS[d] for d in days}
    hour, minute = _parse_clock(args.time)

    day = _parse_start(args.start)
    jobs = [(slug, lang) for _ in range(max(1, args.repeat)) for slug in slugs for lang in langs]
    lines = []
    for slug, lang in jobs:
        while day.weekday() not in wanted:
            day += timedelta(days=1)
        when = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        lines.append(
            f"- product: {slug}\n"
            f"  lang: {lang}\n"
            f"  aspect: {args.aspect}\n"
            f"  platform: {args.platform}\n"
            f"  when: {when:%Y-%m-%d %H:%M}\n"
        )
        day += timedelta(days=1)

    block = "\n".join(lines)
    if args.write:
        dest = Path(args.write)
        header = "" if dest.exists() else "# Posting schedule. Edit freely; status is kept elsewhere.\n\n"
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write(header + block)
        print(f"Added {len(jobs)} entries to {dest}")
        print("Review them, then:  python -m reelfactory queue")
    else:
        print(block, end="")
        print("# Redirect or use --write calendar.yaml to save these.", file=sys.stderr)
    return 0


def cmd_queue(args) -> int:
    entries = cal.load(args.calendar)
    state = cal.State.load(args.state)
    now = datetime.now()
    grace = timedelta(hours=args.grace)
    shown = 0
    tally: dict[str, int] = {}
    print(f"{'when':<22}{'product':<26}{'lang':<6}{'platform':<11}status")
    print("-" * 78)
    for e in entries:
        status = state.status(e)
        if status == "pending":
            if e.when > now:
                marker = "scheduled"
            elif now - e.when > grace:
                marker = "missed"      # 'run' will skip this rather than post it late
            else:
                marker = "due now"
        else:
            marker = status
        tally[marker] = tally.get(marker, 0) + 1
        if not args.all and status != "pending" and e.when < now:
            continue
        print(f"{e.when:%a %d %b %H:%M}     {e.product:<26}{e.lang:<6}{e.platform:<11}{marker}")
        row = state.data.get(e.id, {})
        if row.get("error"):
            print(f"{'':<22}  ! {row['error'].splitlines()[0]}")
        if row.get("url"):
            print(f"{'':<22}  -> {row['url']}")
        shown += 1
    print("-" * 78)
    if not shown:
        print("Nothing pending." + ("" if args.all else "  Use --all to see past posts."))
    else:
        print("  ".join(f"{k}: {v}" for k, v in sorted(tally.items())))
    return 0


def cmd_run(args) -> int:
    brand = Brand.load(args.brand)
    entries = cal.load(args.calendar)
    state = cal.State.load(args.state)
    now = cal.parse_when(args.now, "--now") if args.now else datetime.now()
    outroot = Path(args.out)
    products_root = Path(args.products)
    _warn_font({e.lang for e in entries}, brand)

    def build_video(slug: str, lang: str, aspect: str):
        product_dir = products_root / slug
        if not product_dir.exists():
            raise FileNotFoundError(
                f"the calendar refers to product '{slug}' but {product_dir} does not exist"
            )
        prod = Product.load(product_dir)
        return build_one(prod, brand, lang, [aspect], outroot, args)

    runner = Runner(brand, outroot, Path(args.drop), build_video, retries=args.retries)
    print(f"run at {now:%Y-%m-%d %H:%M}  ({len(entries)} entries in the calendar)")

    if args.lookahead > 0:
        runner.prepare(entries, state, now, args.lookahead)
    if args.prepare_only:
        print("   prepare-only, publishing nothing")
        return 0

    ok, bad = runner.run(entries, state, now, args.grace)
    print(f"\n   {ok} published, {bad} failed")
    return 1 if bad else 0


def cmd_serve(args) -> int:
    try:
        from .web.app import create_app
    except ImportError as exc:
        raise ValueError(
            "The web UI needs Flask. Run:  pip install Flask   (or pip install -r requirements.txt)"
        ) from exc
    app = create_app(
        brand_path=Path(args.brand), products_root=Path(args.products), out_root=Path(args.out)
    )
    print(f"Reel Factory web UI running at http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


# ---------------------------------------------------------------------- shared


def _build_segments(prod: Product, brand: Brand, lang: str, args):
    source = getattr(args, "script", "template")
    intent = getattr(args, "intent", None)
    steer = (getattr(args, "steer", "") or "").strip()
    if intent:
        # Every writer reads the intent off the product, so overriding it here
        # keeps one code path for "what is this video for".
        prod = dataclasses.replace(prod, intent=intent)
    if source == "ai":
        return ai_script.build(
            prod, brand, lang,
            model=brand.gemini_script_model,
            api_key=getattr(args, "gemini_key", None),
            backup_key=getattr(args, "gemini_backup_key", None),
            steer=steer,
        )
    if source == "grok":
        return grok_script.build(
            prod, brand, lang,
            model=brand.grok_script_model,
            api_key=getattr(args, "grok_key", None),
            steer=steer,
        )
    if source == "local":
        return local_script.build(
            prod, brand, lang,
            model=getattr(args, "local_model", None) or brand.local_script_model,
            base_url=getattr(args, "local_url", None) or brand.local_base_url,
            api_key=getattr(args, "local_key", None),
            steer=steer,
        )
    # The template writer has no model to steer; it picks a fresh hook each
    # time, so asking again is still how you get a different opening line.
    return copywriter.build(prod, brand, lang)


def _build_segment_variants(prod: Product, brand: Brand, lang: str, args, n: int = 3):
    """Up to n different drafts of the same script, for the web UI's
    write-several-and-pick view.

    The template writer is deterministic per product (repeat builds stay
    identical on purpose) so calling it n times would just return n copies of
    the same script; give each variant its own derived seed instead. The AI
    writers already sample at temperature 0.9, so calling them again is
    enough on its own -- except when a product pins an exact script via
    script_override, which is fixed text with nothing to vary.

    Duplicates (a template pool small enough to repeat, or two AI calls that
    happened to land on the same wording) are dropped, so the picker never
    shows two options that read identically -- the list can come back shorter
    than n, but never with a repeat.
    """
    if prod.script_override(lang):
        return [_build_segments(prod, brand, lang, args)]

    source = getattr(args, "script", "template")
    drafts = []
    for i in range(n):
        if source == "template":
            seed = zlib.crc32(f"{prod.slug}-variant-{i}".encode("utf-8"))
            drafts.append(_build_segments(dataclasses.replace(prod, seed=seed), brand, lang, args))
        else:
            drafts.append(_build_segments(prod, brand, lang, args))

    seen, unique = set(), []
    for segs in drafts:
        key = tuple((s.role, s.vo, s.overlay) for s in segs)
        if key not in seen:
            seen.add(key)
            unique.append(segs)
    return unique


def _effective_intent(prod: Product, brand: Brand, args) -> str:
    return getattr(args, "intent", None) or prod.resolve_intent(brand)


def _script_tag(source: str, intent: str = "") -> str:
    writer = {"ai": "Gemini script", "grok": "Grok script", "local": "local model script"}.get(source)
    bits = [b for b in (writer, f"intent: {intent}" if intent else "") if b]
    return f"  ({', '.join(bits)})" if bits else ""


def build_one(prod: Product, brand: Brand, lang: str, aspects, outroot: Path, args,
              segments=None, variant_tag: str = ""):
    """Render every requested aspect ratio of one product in one language.

    Pass `segments` to render an exact script -- the web UI does this when the
    words have been edited by hand, so the render uses what is on screen
    rather than asking the writer for a fresh (and different) draft.

    `variant_tag` (e.g. "_v2") is folded into the video filename only, so
    the web UI can build one video per script version someone picked from
    the compare view without each one overwriting the last."""
    edited = segments is not None
    print(f"\n>> {prod.slug} [{lang}]"
          + ("  (edited script)" if edited else _script_tag(getattr(args, "script", "template"))))
    # Checked before writing a script or paying for TTS: a bad photo would
    # otherwise only surface deep into the render, after that work is done.
    validate_photos(prod.photos)
    if not edited:
        segments = _build_segments(prod, brand, lang, args)
    print(f"   {len(segments)} segments, {len(prod.photos)} photo(s)")

    tmp = Path(tempfile.mkdtemp(prefix=f"rf_{prod.slug}_{lang}_"))
    outdir = outroot / prod.slug
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        voice_label = brand.gemini_voice if args.tts == "gemini" else brand.voice(lang)
        print(f"   voicing with '{args.tts}' ({voice_label})")
        clips = voice.synthesize(
            [s.vo for s in segments], lang, brand.voice(lang), brand.rate(lang),
            tmp / "vo", backend=args.tts,
            gemini_voice=brand.gemini_voice, gemini_model=brand.gemini_tts_model,
            gemini_key=getattr(args, "gemini_key", None),
            gemini_backup_key=getattr(args, "gemini_backup_key", None),
        )
        track = voice.concat(clips, tmp / "voice.wav")
        shot_lens, timings = plan_shots([c.duration for c in clips], voice.PAUSE)
        photos = [prod.photos[i % len(prod.photos)] for i in range(len(segments))]

        for aspect in aspects:
            w, h = ASPECTS[aspect]
            tag = aspect.replace(":", "x")
            ass = subtitles.write(
                tmp / f"text_{tag}.ass",
                [(s.role, s.overlay) for s in segments], timings, w, h,
                brand.primary_color, brand.text_color, lang,
                font=brand.font_hi if lang == "hi" else brand.font_en,
                kicker=brand.name if brand.watermark and not brand.logo else None,
            )
            dest = outdir / f"{prod.slug}_{lang}{variant_tag}_{tag}.mp4"
            print(f"   rendering {aspect} -> {dest.name}")
            render(
                [Shot(p, d) for p, d in zip(photos, shot_lens)],
                ass, track, dest, (w, h), tmp,
                logo=brand.logo,
                music=None if args.no_music else brand.music,
                music_volume=brand.music_volume,
                letterbox_color=brand.secondary_color,
                preset=args.preset,
            )
            written.append(dest)

        cap = outdir / f"{prod.slug}_{lang}_caption.txt"
        cap.write_text(copywriter.caption(prod, brand, lang), encoding="utf-8")
        written.append(cap)
    finally:
        if getattr(args, "keep_temp", False):
            print(f"   temp kept at {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return written


def _warn_font(langs, brand: Brand) -> None:
    if "hi" in set(langs) and subtitles.missing_devanagari() and not brand.font_hi:
        print(
            "warning: no Devanagari font found, so Hindi on-screen text will render as boxes.\n"
            "         Install Noto Sans Devanagari (fonts.google.com/noto/specimen/Noto+Sans+Devanagari),\n"
            "         or set font_hi in brand.yaml to a font you do have.",
            file=sys.stderr,
        )


def _parse_start(text: str) -> datetime:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    low = text.strip().lower()
    if low == "today":
        return today
    if low == "tomorrow":
        return today + timedelta(days=1)
    return cal.parse_when(text, "--start")


def _parse_clock(text: str):
    try:
        hh, mm = text.strip().split(":")
        hour, minute = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"--time should look like 19:00, got {text!r}")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"--time {text!r} is not a real time of day.")
    return hour, minute


def _split(raw: str, allowed, what: str):
    items = [x.strip() for x in raw.split(",") if x.strip()]
    bad = [x for x in items if x not in allowed]
    if bad:
        raise ValueError(f"Unsupported {what}: {', '.join(bad)}. Choose from {', '.join(allowed)}.")
    if not items:
        raise ValueError(f"No {what} given.")
    return items


def _expand(paths):
    out = []
    for p in paths:
        path = Path(p)
        if path.is_dir() and not (path / "product.yaml").exists():
            kids = sorted(c for c in path.iterdir() if (c / "product.yaml").exists())
            if kids:
                out += kids
                continue
        out.append(path)
    return out


DISPATCH = {
    "script": cmd_script,
    "build": cmd_build,
    "plan": cmd_plan,
    "queue": cmd_queue,
    "run": cmd_run,
    "serve": cmd_serve,
}
