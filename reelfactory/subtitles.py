"""Build an ASS subtitle file for the on-screen text.

ASS is used rather than ffmpeg's drawtext because libass shapes Devanagari
correctly (conjuncts and matras), wraps long lines, and supports the fade and
scale animations that make the text feel like a real reel rather than a
slideshow caption.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import os
import platform
import re
import shutil
import subprocess

# Candidate families, best first. ASS allows exactly ONE family per style, so we
# probe the system and pick the first that is really installed -- a comma list
# here would corrupt the style line and silently disable all on-screen text.
CANDIDATES = {
    "hi": ["Nirmala UI", "Noto Sans Devanagari", "Mangal", "Sarala", "Kalam",
           "Lohit Devanagari", "Arial Unicode MS"],
    "en": ["Montserrat SemiBold", "Montserrat", "Poppins", "Segoe UI Semibold",
           "Segoe UI", "Helvetica Neue", "DejaVu Sans", "Arial"],
}
# Last resort per platform; these ship with the OS.
DEFAULT = {"Windows": {"hi": "Nirmala UI", "en": "Segoe UI"},
           "Darwin": {"hi": "Devanagari Sangam MN", "en": "Helvetica Neue"},
           "Linux": {"hi": "Noto Sans Devanagari", "en": "DejaVu Sans"}}

_cache: dict = {}


def pick_font(lang: str, override: str | None = None) -> str:
    """Return one installed font family name suitable for the language."""
    if override:
        return override.split(",")[0].strip()
    if lang in _cache:
        return _cache[lang]
    chosen = None
    for family in CANDIDATES.get(lang, CANDIDATES["en"]):
        if _installed(family):
            chosen = family
            break
    if chosen is None:
        chosen = DEFAULT.get(platform.system(), DEFAULT["Linux"]).get(lang, "Arial")
    _cache[lang] = chosen
    return chosen


def missing_devanagari() -> bool:
    """True when nothing on this machine can draw Hindi text."""
    return not any(_installed(f) for f in CANDIDATES["hi"])


_families: set | None = None


def _system_families() -> set:
    """All font family names on this machine, lowercased. One fc-list call."""
    global _families
    if _families is not None:
        return _families
    _families = set()
    if shutil.which("fc-list"):
        try:
            out = subprocess.run(["fc-list", "--format=%{family}\n"],
                                 capture_output=True, text=True, timeout=20).stdout
            for line in out.splitlines():
                for name in line.split(","):
                    if name.strip():
                        _families.add(name.strip().lower())
        except (OSError, subprocess.SubprocessError):
            pass
    return _families


_win_names: set | None = None


def _windows_font_names() -> set:
    """Installed family names from the Windows font registry, lowercased with
    spaces stripped.

    Matching on filenames alone misses any font whose file is not named after
    its family -- Nirmala UI ships as Nirmala.ttc -- which made this warn that
    no Devanagari font existed on machines that render Hindi perfectly well.
    """
    global _win_names
    if _win_names is not None:
        return _win_names
    _win_names = set()
    try:
        import winreg
    except ImportError:
        return _win_names
    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key_path) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    value_name = winreg.EnumValue(key, i)[0]
                    # "Nirmala UI & Nirmala UI Bold & Nirmala Text (TrueType)"
                    # is one value holding several families.
                    value_name = re.sub(r"\s*\([^)]*\)\s*$", "", value_name)
                    for fam in value_name.split("&"):
                        fam = fam.strip().lower().replace(" ", "")
                        if fam:
                            _win_names.add(fam)
        except OSError:
            continue
    return _win_names


def _installed(family: str) -> bool:
    needle = family.lower().replace(" ", "")
    if platform.system() == "Windows":
        # startswith, not equality: a family is often registered with its
        # weight appended ("Noto Sans Devanagari Regular").
        if any(n.startswith(needle) for n in _windows_font_names()):
            return True
        root = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        try:
            return any(needle in f.lower().replace(" ", "") for f in os.listdir(root))
        except OSError:
            return False
    return family.lower() in _system_families()


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Body,{font},{fs_body},{white},{white},{shadow},{shadow},-1,0,0,0,100,100,0,0,1,{outline},2,2,{margin},{margin},{mv_body},1
Style: Big,{font},{fs_big},{white},{white},{shadow},{shadow},-1,0,0,0,100,100,0,0,1,{outline},2,2,{margin},{margin},{mv_body},1
Style: Accent,{font},{fs_big},{accent},{accent},{shadow},{shadow},-1,0,0,0,100,100,0,0,1,{outline},2,2,{margin},{margin},{mv_body},1
Style: Karaoke,{font},{fs_kara},{white},{dim},{shadow},{shadow},-1,0,0,0,100,100,0,0,1,{outline},2,2,{margin},{margin},{mv_body},1
Style: Kicker,{font},{fs_kick},{white},{white},{shadow},{shadow},-1,0,0,0,100,100,2,0,1,{outline_s},1,8,{margin},{margin},{mv_kick},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# role -> style name
ROLE_STYLE = {
    "hook": "Big",
    "reveal": "Big",
    "offer": "Accent",     # the deal reads like the price: brand colour, big
    "usp": "Body",
    "proof": "Body",
    "price": "Accent",
    "urgency": "Accent",
    "cta": "Accent",
    "custom": "Body",
}

POP_IN = r"{\fad(220,220)\fscx88\fscy88\t(0,220,\fscx100\fscy100)}"
# Karaoke lines are already animating word by word, so they get a plain fade --
# a scale pop on top of that reads as busy.
KARA_IN = r"{\fad(180,200)}"

# Conversational beats are spoken as sentences, so they read well word by word.
# The rest -- a price, an offer, the closing CTA -- work better as a short,
# static card that stays put and can be read at a glance.
KARAOKE_ROLES = {"hook", "reveal", "usp", "proof"}


@dataclass
class Cue:
    """One beat's on-screen text: the short overlay, plus the word timings that
    let it be drawn as karaoke instead when the role calls for it."""

    role: str
    overlay: str
    words: list = field(default_factory=list)   # voice.Word; empty = static only


def write(
    path: Path,
    cues,
    timings,
    width: int,
    height: int,
    accent: str,
    text_color: str,
    lang: str,
    font: str | None = None,
    kicker: str | None = None,
) -> Path:
    """cues: list of Cue. timings: list of (start, end, speech_start) in seconds."""
    if len(cues) != len(timings):
        raise ValueError(
            f"Got {len(cues)} text cues but {len(timings)} time slots; they must match."
        )
    # Type is sized off the short edge so it stays readable at every aspect
    # ratio; vertical margins follow the height so text sits in the same
    # relative place on a tall reel and a square post.
    scale = min(width, height) / 1080.0
    vscale = height / 1920.0
    body = HEADER.format(
        w=width,
        h=height,
        font=pick_font(lang, font),
        fs_body=int(84 * scale),
        fs_big=int(106 * scale),
        fs_kick=int(44 * scale),
        # Karaoke shows the whole spoken sentence, not a trimmed headline, so it
        # is set a step smaller to keep long lines down to three rows.
        fs_kara=int(76 * scale),
        white=_ass_color(text_color),
        dim=_ass_color(text_color, alpha=0x78),
        accent=_ass_color(accent),
        shadow="&H90000000",
        outline=round(3.4 * scale, 1),
        outline_s=round(2.0 * scale, 1),
        margin=int(96 * (width / 1080.0)),
        mv_body=max(int(200 * scale), int(300 * vscale)),
        mv_kick=max(int(90 * scale), int(150 * vscale)),
    )

    lines = []
    if kicker:
        end = timings[-1][1]
        lines.append(
            f"Dialogue: 0,{_t(0)},{_t(end)},Kicker,,0,0,0,,"
            + r"{\fad(400,400)\alpha&H40&}"
            + _escape(kicker)
        )
    for cue, (start, end, speech_start) in zip(cues, timings):
        if cue.role in KARAOKE_ROLES and cue.words:
            text = _karaoke(cue.words, start, end, speech_start)
            if text:
                lines.append(
                    f"Dialogue: 0,{_t(start)},{_t(end)},Karaoke,,0,0,0,,{KARA_IN}{text}"
                )
                continue
        if not cue.overlay.strip():
            continue
        style = ROLE_STYLE.get(cue.role, "Body")
        lines.append(
            f"Dialogue: 0,{_t(start)},{_t(end)},{style},,0,0,0,,{POP_IN}{_escape(cue.overlay)}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def _cs(seconds: float) -> int:
    """Seconds to centiseconds, the unit ASS karaoke timing is written in."""
    return int(round(max(0.0, seconds) * 100))


def _karaoke(words, start: float, end: float, speech_start: float) -> str:
    r"""One line of \k-timed syllables, each word lighting up as it is spoken.

    ASS runs the karaoke clock from the line's own Start time, so every word is
    placed relative to `start` -- not to when the audio begins. Durations are
    accumulated in centiseconds so that rounding cannot drift across a line.
    """
    parts, cursor = [], 0
    limit = _cs(end - start)
    for w in words:
        begin = _cs(speech_start + w.start - start)
        if begin > cursor:                      # silence before this word
            parts.append(r"{\k%d}" % (begin - cursor))
            cursor = begin
        stop = min(_cs(speech_start + w.start + w.duration - start), limit)
        span = max(1, stop - cursor)
        parts.append(r"{\k%d}%s " % (span, _escape(w.text)))
        cursor += span
    return "".join(parts).rstrip()


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _t(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS uses BGR, with alpha first; 0 = opaque)."""
    h = hex_rgb.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"Colour must look like #RRGGBB, got {hex_rgb!r}")
    try:
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"Colour must look like #RRGGBB, got {hex_rgb!r}")
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"
