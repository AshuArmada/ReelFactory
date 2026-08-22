"""Visual templates: the *look* of a video, kept as data instead of hardcoded.

A template decides how the camera moves over each photo, how shots cut into one
another, how the photos are graded and how strong the backdrop behind the text
is. They live in `templates/*.yaml` at the project root, so adding a new look is
adding a file -- no render code changes.

`classic` is special: it is the original hardcoded look, and it is built in, so
the tool still renders correctly even if the templates folder is missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import read_yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "templates"
DEFAULT_NAME = "classic"

# Camera moves render._motion knows how to build.
MOVES = ["in_center", "out_center", "in_left", "pan_right", "in_right", "pan_left"]

# Every transition ffmpeg's xfade filter accepts.
TRANSITIONS = {
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright",
    "slideup", "slidedown", "circlecrop", "rectcrop", "distance", "fadeblack",
    "fadewhite", "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
    "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice",
    "hrslice", "vuslice", "vdslice", "hblur", "fadegrays", "wipetl", "wipetr",
    "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin", "fadefast", "fadeslow",
    "hlwind", "hrwind", "vuwind", "vdwind", "coverleft", "coverright", "coverup",
    "coverdown", "revealleft", "revealright", "revealup", "revealdown",
}


@dataclass
class Template:
    """Defaults here are the original look, so Template() == 'classic'."""

    name: str = DEFAULT_NAME
    description: str = ""
    moves: list = field(default_factory=lambda: list(MOVES))
    zoom: float = 0.30              # total Ken Burns travel, as a fraction
    transitions: list = field(default_factory=lambda: ["fade"])
    transition_seconds: float = 0.5
    grade: str = ""                 # an ffmpeg filter string, e.g. "eq=contrast=1.1"
    scrim: float = 0.78             # darkness of the gradient behind the text, 0 = off
    crop_budget: float = 0.35       # see render.MAX_CROP_LOSS

    def move_for(self, idx: int) -> str:
        return self.moves[idx % len(self.moves)]

    def transition_for(self, idx: int) -> str:
        return self.transitions[idx % len(self.transitions)]


def available(root: Path | None = None) -> list:
    """Every template that can be asked for, the built-in one included."""
    folder = root or TEMPLATE_DIR
    names = {DEFAULT_NAME}
    if folder.is_dir():
        names |= {p.stem for p in folder.glob("*.yaml")}
    return sorted(names)


def load(name: str = "", root: Path | None = None) -> Template:
    folder = root or TEMPLATE_DIR
    name = (name or DEFAULT_NAME).strip()
    path = folder / f"{name}.yaml"
    if not path.exists():
        if name == DEFAULT_NAME:
            return Template()       # built in; works with no templates folder
        raise ValueError(
            f"Unknown template {name!r}. Available: {', '.join(available(root))}."
        )
    data = read_yaml(path)
    known = set(Template.__dataclass_fields__) - {"name"}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{path}: unknown setting(s) {sorted(unknown)}. Valid: {sorted(known)}")

    tpl = Template(name=name, **data)
    tpl.moves = [m for m in tpl.moves if m in MOVES] or list(MOVES)
    bad = [t for t in tpl.transitions if t not in TRANSITIONS]
    if bad:
        raise ValueError(
            f"{path}: ffmpeg has no transition called {', '.join(bad)}. "
            f"Try one of: fade, dissolve, slideleft, smoothright, circleopen."
        )
    tpl.transitions = list(tpl.transitions) or ["fade"]
    tpl.zoom = _clamp(tpl.zoom, 0.0, 1.0)
    tpl.scrim = _clamp(tpl.scrim, 0.0, 1.0)
    tpl.crop_budget = _clamp(tpl.crop_budget, 0.0, 0.95)
    # A transition longer than the shortest shot would swallow it whole.
    tpl.transition_seconds = _clamp(tpl.transition_seconds, 0.05, 1.5)
    return tpl


def _clamp(value, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        raise ValueError(f"expected a number between {low} and {high}, got {value!r}")
