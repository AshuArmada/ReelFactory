"""Is this machine actually able to make a video right now?

Every one of these used to be discovered the expensive way: FFmpeg missing
surfaced part-way through a render, a missing Devanagari font produced a
finished video full of empty boxes, and a missing API key only announced
itself after you had picked that writer and clicked Build. The web UI shows
the whole list on the dashboard instead, before anything is started.

Checks are cheap and must stay that way -- this runs on every page load of
the dashboard. Nothing here raises: a check that cannot answer reports
"unknown" rather than taking the page down with it.
"""
from __future__ import annotations

import importlib.util
import shutil
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from . import subtitles


@dataclass
class Check:
    key: str
    label: str
    ok: bool | None      # True fine, False broken, None "not needed / unknown"
    detail: str          # one short line, shown next to the label
    fix: str = ""        # what to actually do about it, when ok is False


def run(brand=None) -> list:
    """Every check, in the order they matter to someone starting out."""
    return [
        _ffmpeg(),
        _hindi_font(brand),
        _tts(),
        _gemini(),
        _grok(),
        _local(brand),
    ]


def blocking(checks) -> list:
    """The ones that stop a video being made at all, as opposed to limiting
    which options are available. Used to decide whether the dashboard shows a
    red banner or a quiet line of ticks."""
    return [c for c in checks if c.ok is False and c.key in {"ffmpeg", "tts"}]


# ---------------------------------------------------------------- the checks


def _ffmpeg() -> Check:
    have_ffmpeg = shutil.which("ffmpeg") is not None
    have_probe = shutil.which("ffprobe") is not None
    if have_ffmpeg and have_probe:
        return Check("ffmpeg", "FFmpeg", True, "installed and on PATH")
    missing = ", ".join(n for n, ok in (("ffmpeg", have_ffmpeg), ("ffprobe", have_probe)) if not ok)
    return Check(
        "ffmpeg", "FFmpeg", False, f"{missing} not found",
        "Nothing can be rendered without it. On Windows run setup_windows.bat, "
        "or install it with:  winget install Gyan.FFmpeg",
    )


def _hindi_font(brand=None) -> Check:
    override = getattr(brand, "font_hi", None)
    if override:
        return Check("font_hi", "Hindi font", True, f"using {override} from brand settings")
    try:
        if subtitles.missing_devanagari():
            return Check(
                "font_hi", "Hindi font", False, "no Devanagari font on this PC",
                "Hindi videos will build, but every on-screen word renders as empty "
                "boxes. Install Noto Sans Devanagari (fonts.google.com/noto/specimen/"
                "Noto+Sans+Devanagari), or set a font you do have in Brand settings. "
                "English videos are unaffected.",
            )
        return Check("font_hi", "Hindi font", True, f"{subtitles.pick_font('hi')} found")
    except OSError:
        return Check("font_hi", "Hindi font", None, "could not be checked")


def _tts() -> Check:
    have_edge = importlib.util.find_spec("edge_tts") is not None
    have_gtts = importlib.util.find_spec("gtts") is not None
    if have_edge:
        return Check("tts", "Voice-over", True, "edge-tts installed — free, no key needed")
    if have_gtts:
        return Check("tts", "Voice-over", True, "gtts installed — pick 'gtts' under Advanced")
    return Check(
        "tts", "Voice-over", False, "no voice engine installed",
        "Run:  pip install edge-tts   (or build with the voice set to 'silent' "
        "to check the pictures without a voice-over).",
    )


def _gemini() -> Check:
    from . import gemini
    try:
        gemini.resolve_key()
    except Exception:
        return Check("gemini", "Gemini key", None,
                     "not set — the Gemini script writer and voice are unavailable")
    return Check("gemini", "Gemini key", True, "found")


def _grok() -> Check:
    from . import grok
    try:
        grok.resolve_key()
    except Exception:
        return Check("grok", "Grok key", None, "not set — the Grok script writer is unavailable")
    return Check("grok", "Grok key", True, "found")


def _local(brand=None) -> Check:
    url = getattr(brand, "local_base_url", "") or "http://localhost:11434/v1"
    if not _listening(url):
        return Check("local", "Local model", None,
                     f"nothing answering at {url} — the local script writer is unavailable")
    model = getattr(brand, "local_script_model", "") or "?"
    return Check("local", "Local model", True, f"server up at {url} (model {model})")


def _listening(url: str, timeout: float = 0.35) -> bool:
    """Is something accepting connections at this URL's host/port?

    A TCP connect, not an HTTP request: this runs on every dashboard load, and
    a real request to a busy local model server can block for seconds. It only
    answers "the server is up", which is exactly the question the dashboard
    is asking.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False
