"""The posting queue.

`calendar.yaml` is written by hand and stays that way: comments, ordering and
formatting are never rewritten by the tool. Status lives beside it in
`queue_state.json`, keyed by a fingerprint of the entry, so a run that publishes
something can record the result without touching the file you edit.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

PLATFORMS = ("facebook", "instagram", "youtube", "folder", "dryrun")
STATUSES = ("pending", "published", "failed", "skipped")
LANGS = ("hi", "en")
ASPECTS = ("9:16", "1:1", "4:5", "16:9")
# PyYAML follows YAML 1.1 here and reads an unquoted ``9:16`` as the base-60
# integer 556. Older versions of ``reelfactory plan`` emitted that exact form,
# so translate those values while new schedules are written with quotes.
_YAML_11_ASPECTS = {
    int(hours) * 60 + int(minutes): value
    for value in ASPECTS
    for hours, minutes in [value.split(":")]
}
TIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


@dataclass
class Entry:
    product: str
    lang: str
    when: datetime
    platform: str = "dryrun"
    aspect: str = "9:16"
    note: str = ""
    line: int = 0                    # position in the file, for error messages

    @property
    def id(self) -> str:
        raw = f"{self.product}|{self.lang}|{self.platform}|{self.aspect}|{self.when.isoformat()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def video_name(self) -> str:
        return f"{self.product}_{self.lang}_{self.aspect.replace(':', 'x')}.mp4"

    @property
    def caption_name(self) -> str:
        return f"{self.product}_{self.lang}_caption.txt"

    def __str__(self) -> str:
        return f"{self.when:%a %d %b %H:%M}  {self.product} [{self.lang}] -> {self.platform}"


@dataclass
class State:
    """Status of every entry we have acted on, persisted as JSON."""

    path: Path
    data: dict = field(default_factory=dict)

    @staticmethod
    def load(path) -> "State":
        p = Path(path)
        if not p.exists():
            return State(p, {})
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{p} is corrupted ({exc}). Delete it to start the log fresh; "
                "you will lose the record of what was already posted."
            )
        if not isinstance(data, dict):
            raise ValueError(f"{p} is corrupted (expected a JSON object at the top level).")
        return State(p, data)

    def status(self, entry: Entry) -> str:
        return self.data.get(entry.id, {}).get("status", "pending")

    def record(self, entry: Entry, status: str, url: str = "", error: str = "") -> None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status {status!r}; expected one of {STATUSES}.")
        row = self.data.setdefault(entry.id, {})
        row.update(
            status=status,
            product=entry.product,
            lang=entry.lang,
            platform=entry.platform,
            scheduled=entry.when.isoformat(sep=" "),
            updated=datetime.now().isoformat(sep=" ", timespec="seconds"),
        )
        if url:
            row["url"] = url
        if error:
            row["error"] = error[:500]
        else:
            row.pop("error", None)
        self.save()

    def attempts(self, entry: Entry) -> int:
        return self.data.get(entry.id, {}).get("attempts", 0)

    def bump_attempt(self, entry: Entry) -> int:
        row = self.data.setdefault(entry.id, {})
        row["attempts"] = row.get("attempts", 0) + 1
        self.save()
        return row["attempts"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")


def load(path) -> list[Entry]:
    """Read calendar.yaml into Entry objects, sorted by time."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No calendar at {p}. Create one, or generate a starting point with:\n"
            "  python -m reelfactory plan products --start tomorrow --time 19:00"
        )
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "the YAML"
        raise ValueError(
            f"{p}: invalid calendar at {where}: {getattr(exc, 'problem', None) or exc}"
        ) from exc
    if not isinstance(raw, list):
        raise ValueError(f"{p}: expected a list of scheduled posts at the top level.")

    entries: list[Entry] = []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"{p}: entry {i} should be a mapping, got {type(item).__name__}.")
        missing = [k for k in ("product", "lang", "when") if k not in item]
        if missing:
            raise ValueError(f"{p}: entry {i} is missing {', '.join(missing)}.")
        platform = str(item.get("platform", "dryrun"))
        if platform not in PLATFORMS:
            raise ValueError(
                f"{p}: entry {i} has platform {platform!r}. Choose from {', '.join(PLATFORMS)}."
            )
        product = str(item["product"]).strip()
        if not product or product in {".", ".."} or "/" in product or "\\" in product:
            raise ValueError(f"{p}: entry {i} has an invalid product folder name {product!r}.")
        lang = str(item["lang"])
        if lang not in LANGS:
            raise ValueError(f"{p}: entry {i} has language {lang!r}. Choose from {', '.join(LANGS)}.")
        raw_aspect = item.get("aspect", "9:16")
        aspect = (_YAML_11_ASPECTS.get(raw_aspect, str(raw_aspect))
                  if type(raw_aspect) is int else str(raw_aspect))
        if aspect not in ASPECTS:
            raise ValueError(f"{p}: entry {i} has aspect {aspect!r}. Choose from {', '.join(ASPECTS)}.")
        entries.append(
            Entry(
                product=product,
                lang=lang,
                when=parse_when(item["when"], f"{p} entry {i}"),
                platform=platform,
                aspect=aspect,
                note=str(item.get("note", "")),
                line=i,
            )
        )
    entries.sort(key=lambda e: e.when)
    _warn_duplicates(entries)
    return entries


def due(entries, state: State, now: datetime, grace_hours: int = 48):
    """Entries whose time has come and which have not been dealt with yet.

    `grace_hours` stops a machine that was switched off for a week from firing
    off a burst of stale posts the moment it comes back.
    """
    out = []
    for e in entries:
        if e.when > now or state.status(e) != "pending":
            continue
        if now - e.when > timedelta(hours=grace_hours):
            state.record(e, "skipped", error=f"missed by more than {grace_hours}h")
            continue
        out.append(e)
    return out


def upcoming(entries, state: State, now: datetime, within_days: int = 3):
    """Pending entries due soon, so their videos can be rendered in advance."""
    horizon = now + timedelta(days=within_days)
    return [e for e in entries if now <= e.when <= horizon and state.status(e) == "pending"]


def parse_when(value, where: str) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"{where}: could not read the date {text!r}. Use 'YYYY-MM-DD HH:MM', e.g. 2026-08-04 19:30."
    )


def _warn_duplicates(entries) -> None:
    seen = {}
    for e in entries:
        if e.id in seen:
            raise ValueError(
                f"calendar has the same post twice: {e} appears at entries "
                f"{seen[e.id]} and {e.line}. Change the time on one of them."
            )
        seen[e.id] = e.line
