"""Animated advertising lights drawn by libass; no stock overlays or downloads.

Effects use the same timeline as the final composite and sit below its captions.
They are deterministic so previews and exports share the same choreography.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from .subtitles import _t


PRESETS = {
    "none": ("None", ()),
    "gold_sweep": ("Gold light sweeps", ("gold_sweep",)),
    "sparks": ("Spark bursts", ("sparks",)),
    "glints": ("Soft glints", ("glints",)),
    "gold": ("Gold sweeps, sparks and glints", ("gold_sweep", "sparks", "glints")),
}


def parse_strength(value) -> float:
    try:
        strength = float(value)
    except (TypeError, ValueError):
        raise ValueError("Choose a visual effect intensity between 0 and 1.") from None
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("Choose a visual effect intensity between 0 and 1.")
    return strength


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Light,Arial,20,&H00FFFFFF,&H00FFFFFF,&H0000CFFF,&HFF000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write(path: Path, width: int, height: int, total: float, cuts, names,
          strength: float = 0.7) -> Path | None:
    """Write optional gold sweeps, spark bursts and glints at opening/cut times."""
    if not names or strength <= 0 or total <= 0:
        return None
    scale = min(width, height) / 1080.0
    alpha = max(0, min(255, round(255 * (1.0 - strength))))
    rng = random.Random(2301)
    lines = []

    def event(start, duration, tags, drawing, layer=0):
        start, end = max(0.0, start), min(total, start + duration)
        if end - start < 0.06:
            return
        # All positions are generated numbers; no user text enters override tags.
        lines.append(
            f"Dialogue: {layer},{_t(start)},{_t(end)},Light,,0,0,0,,"
            + rf"{{\an7\p1\shad0\alpha&H{alpha:02X}&{tags}}}" + drawing
        )

    anchors = sorted({0.0, *(float(c) for c in cuts if 0 < c < total)})
    for index, at in enumerate(anchors):
        # Only one reveal runs at a time, even with very short scene slots.
        span = min(1.25, (anchors[index + 1] if index + 1 < len(anchors) else total) - at)
        if "gold_sweep" in names:
            # A broad amber halo and bright narrow core cross the product on a
            # diagonal. Short fade times keep the effect from obscuring details.
            length = max(20, round(min(width, height) * 0.30))
            thickness = max(1, round(3 * scale))
            y = round(height * (0.34 if index % 2 == 0 else 0.48))
            move = rf"\move({-length},{y},{width + length},{round(y - height * .18)})"
            shape = f"m 0 0 l {length} 0 {length} {thickness} 0 {thickness}"
            event(at + .08, span, move + rf"\frz24\bord{max(2, 12 * scale):.2f}"
                  + rf"\blur{max(2, 15 * scale):.2f}\1c&H65DFFF&\3c&H16AFFF&\fad(100,280)", shape)
            event(at + .08, span, move + rf"\frz24\bord0\blur{max(.5, scale):.2f}"
                  + r"\1c&HDBF9FF&\fad(100,280)", shape, 1)

        if "sparks" in names:
            origin_x, origin_y = round(width * .52), round(height * .43)
            for _ in range(20):
                angle = rng.uniform(0, 2 * math.pi)
                distance = rng.uniform(.10, .38) * min(width, height)
                dx, dy = math.cos(angle) * distance, math.sin(angle) * distance
                length = max(2, round(rng.uniform(5, 17) * scale))
                thick = max(1, round(1.5 * scale))
                start = at + rng.uniform(.05, .17)
                event(start, min(span, rng.uniform(.42, .80)),
                      rf"\move({origin_x},{origin_y},{round(origin_x + dx)},{round(origin_y + dy)})"
                      + rf"\frz{-math.degrees(angle):.2f}\bord{max(.5, 1.2 * scale):.2f}"
                      + rf"\blur{max(.5, scale):.2f}\1c&HAAEFFF&\3c&H22BFFF&\fad(30,340)",
                      f"m 0 0 l {length} 0 {length} {thick} 0 {thick}", 1)

    if "glints" in names:
        # Four-point glints appear away from the lower caption safe area.
        at = .38
        while at < total:
            x, y = round(rng.uniform(.15, .86) * width), round(rng.uniform(.16, .57) * height)
            radius = max(3, round(rng.uniform(10, 20) * scale))
            inner = max(1, round(radius * .16))
            r = radius
            star = (f"m {r} 0 l {r+inner} {r-inner} {2*r} {r} {r+inner} {r+inner} "
                    f"{r} {2*r} {r-inner} {r+inner} 0 {r} {r-inner} {r-inner}")
            event(at, .65, rf"\pos({x},{y})\bord{max(1, 2 * scale):.2f}"
                  + rf"\blur{max(.5, scale):.2f}\1c&HDFFFFF&\3c&H37CFFF&"
                  + r"\fscx40\fscy40\t(0,230,\fscx110\fscy110)"
                  + r"\t(230,650,\fscx35\fscy35)\fad(120,300)", star, 2)
            at += 1.65

    if not lines:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER.format(w=width, h=height) + "\n".join(lines) + "\n", encoding="utf-8")
    return path
