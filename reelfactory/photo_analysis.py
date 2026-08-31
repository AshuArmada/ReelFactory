"""Gemini-powered descriptions of the photos attached to one product.

Analysis is explicit (the user presses a button), cached beside product.yaml,
and content-addressed. A cached description is never added to a script prompt
after a photo is added, removed, or replaced; the UI marks it stale instead.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import gemini
from .config import Brand, Product, read_yaml, write_yaml

FILENAME = "photo_analysis.yaml"
SAVED_FILENAME = "saved_photo_summaries.yaml"
DEFAULT_MODEL = "gemini-2.5-flash"
# Google's inline-image guide caps a complete request at 20 MB. Base64 adds
# roughly one third, so keep raw image batches comfortably below that limit.
MAX_BATCH_BYTES = 12 * 1024 * 1024
MAX_BATCH_IMAGES = 8
MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def path_for(product_dir: Path) -> Path:
    return Path(product_dir) / FILENAME


def saved_path_for(product_dir: Path) -> Path:
    return Path(product_dir) / SAVED_FILENAME


def load(product_dir: Path) -> dict:
    path = path_for(product_dir)
    if not path.exists():
        return {}
    try:
        data = read_yaml(path)
    except (ValueError, FileNotFoundError):
        return {}
    return data if isinstance(data, dict) else {}


def load_saved(product_dir: Path) -> list[dict]:
    """Named analysis snapshots stored for this product."""
    path = saved_path_for(product_dir)
    if not path.exists():
        return []
    try:
        data = read_yaml(path)
    except (ValueError, FileNotFoundError):
        return []
    rows = data.get("summaries", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def status(product_dir: Path, photo_names=None) -> dict:
    """UI/prompt-ready state for the current analyzable image set."""
    product_dir = Path(product_dir)
    photo_dir = product_dir / "photos"
    names = list(photo_names) if photo_names is not None else (
        [p.name for p in photo_dir.iterdir()] if photo_dir.is_dir() else []
    )
    paths = [photo_dir / n for n in names if (photo_dir / n).suffix.lower() in MIME_TYPES]
    data = load(product_dir)
    saved = {
        str(row.get("name")): str(row.get("sha256"))
        for row in data.get("photos", []) if isinstance(row, dict)
    }
    current = {p.name: _sha256(p) for p in paths if p.is_file()}
    group = str(data.get("group_summary") or "").strip()
    fresh = bool(group and current and saved == current)
    state = "fresh" if fresh else ("stale" if data else "missing")
    rows = data.get("photos", []) if isinstance(data.get("photos"), list) else []
    return {
        "state": state,
        "fresh": fresh,
        "group_summary": group,
        "photos": rows,
        "by_name": {
            str(row.get("name")): str(row.get("summary") or "")
            for row in rows if isinstance(row, dict)
        },
        "image_count": len(paths),
        "skipped_count": len([n for n in names if (photo_dir / n).suffix.lower() not in MIME_TYPES]),
        "model": str(data.get("model") or ""),
        "updated_at": str(data.get("updated_at") or ""),
        "saved_summaries": load_saved(product_dir),
    }


def analyze(product: Product, brand: Brand, api_key: str | None = None,
            model: str | None = None) -> dict:
    """Analyze all supported still images and save per-photo + group summaries."""
    paths = [p for p in product.photos if p.suffix.lower() in MIME_TYPES]
    if not paths:
        raise ValueError("Add at least one JPG, PNG, or WebP photo before analyzing images.")
    for path in paths:
        if path.stat().st_size > MAX_BATCH_BYTES:
            raise ValueError(
                f"{path.name} is too large for inline analysis. Export it below 12 MB and try again."
            )

    chosen_model = (model or brand.gemini_script_model or DEFAULT_MODEL).strip()
    key = gemini.resolve_key(api_key)
    backup = gemini.resolve_backup_key()
    descriptions = []
    for batch in _batches(paths):
        descriptions.extend(_analyze_batch(batch, chosen_model, key, backup))

    expected = {p.name for p in paths}
    returned = {row["name"] for row in descriptions}
    if returned != expected:
        missing = ", ".join(sorted(expected - returned)) or "none"
        raise gemini.GeminiError(
            f"Gemini did not return a description for every photo (missing: {missing}). Try again."
        )

    order = {p.name: i for i, p in enumerate(paths)}
    descriptions.sort(key=lambda row: order[row["name"]])
    group_summary = _combine(descriptions, chosen_model, key, backup)
    data = {
        "version": 1,
        "model": chosen_model,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "group_summary": group_summary,
        "photos": [
            {"name": row["name"], "sha256": _sha256(product.dir / "photos" / row["name"]),
             "summary": row["summary"]}
            for row in descriptions
        ],
    }
    write_yaml(path_for(product.dir), data)
    return data


def update_group_summary(product_dir: Path, summary: str) -> None:
    data = load(product_dir)
    if not data:
        raise ValueError("Analyze the photos once before editing their combined summary.")
    data["group_summary"] = summary.strip()
    write_yaml(path_for(product_dir), data)


def save_snapshot(product_dir: Path, name: str) -> dict:
    """Store the complete current analysis under a reusable product-local name."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Give the photo summary a name before saving it for future use.")
    if len(clean_name) > 80:
        raise ValueError("Photo summary names must be 80 characters or fewer.")
    current = load(product_dir)
    if not str(current.get("group_summary") or "").strip():
        raise ValueError("Analyze the photos before saving a photo summary.")

    entry = {
        "name": clean_name,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "version": current.get("version", 1),
        "model": str(current.get("model") or ""),
        "analysis_updated_at": str(current.get("updated_at") or ""),
        "group_summary": str(current.get("group_summary") or "").strip(),
        # Fingerprints and per-photo observations travel with the summary.
        # Restoring it can therefore never make an old description look fresh
        # against a different set of files.
        "photos": current.get("photos", []) if isinstance(current.get("photos"), list) else [],
    }
    entries = load_saved(product_dir)
    entries.append(entry)
    write_yaml(saved_path_for(product_dir), {"summaries": entries})
    return entry


def restore_snapshot(product_dir: Path, index: int) -> dict:
    entries = load_saved(product_dir)
    if index < 0 or index >= len(entries):
        raise ValueError("That saved photo summary could not be found.")
    entry = entries[index]
    summary = str(entry.get("group_summary") or "").strip()
    if not summary:
        raise ValueError("That saved photo summary is empty and cannot be restored.")
    write_yaml(path_for(product_dir), {
        "version": entry.get("version", 1),
        "model": str(entry.get("model") or ""),
        "updated_at": str(entry.get("analysis_updated_at") or entry.get("saved_at") or ""),
        "group_summary": summary,
        "photos": entry.get("photos", []) if isinstance(entry.get("photos"), list) else [],
    })
    return entry


def delete_snapshot(product_dir: Path, index: int) -> dict:
    entries = load_saved(product_dir)
    if index < 0 or index >= len(entries):
        raise ValueError("That saved photo summary could not be found.")
    removed = entries.pop(index)
    write_yaml(saved_path_for(product_dir), {"summaries": entries})
    return removed


def prompt_block(product: Product) -> str:
    """Fresh visual observations for an AI prompt, or blank when unavailable/stale."""
    info = status(product.dir, [p.name for p in product.photos])
    if not info["fresh"]:
        return ""
    details = "\n".join(
        f"- {row.get('name')}: {str(row.get('summary') or '').strip()}"
        for row in info["photos"] if isinstance(row, dict) and row.get("summary")
    )
    return "\n".join([
        "VISUAL OBSERVATIONS FROM THE UPLOADED PHOTOS:",
        details,
        f"Combined visual summary: {info['group_summary']}",
        "Use this only to describe what a viewer can visibly see. Treat the verified product",
        "facts above as authoritative. Never infer price, material, capacity, warranty,",
        "performance, or another factual claim from an image.",
    ])


def _analyze_batch(paths: list[Path], model: str, key: str, backup: str | None) -> list[dict]:
    parts = [{"text": (
        "Describe each attached product photo for an advertising script. Report only directly "
        "visible details: object type, color, shape, viewpoint, setting, visible construction, "
        "and visible use. Do not guess material, measurements, capacity, price, quality, warranty, "
        "location, or performance. Return one concise 15-35 word description per filename."
    )}]
    for path in paths:
        parts.extend([
            {"text": f"Filename: {path.name}"},
            {"inlineData": {
                "mimeType": MIME_TYPES[path.suffix.lower()],
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }},
        ])
    schema = {
        "type": "OBJECT",
        "properties": {"photos": {
            "type": "ARRAY",
            "items": {"type": "OBJECT", "properties": {
                "name": {"type": "STRING"}, "summary": {"type": "STRING"},
            }, "required": ["name", "summary"]},
        }},
        "required": ["photos"],
    }
    data = gemini.generate_content(model, key, {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json", "responseSchema": schema,
            "temperature": 0.2,
        },
    }, backup_key=backup)
    parsed = _json_response(data)
    rows = parsed.get("photos") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise gemini.GeminiError("Gemini returned no usable per-photo summaries.")
    expected = {p.name for p in paths}
    return [
        {"name": str(row.get("name")), "summary": str(row.get("summary") or "").strip()}
        for row in rows if isinstance(row, dict) and row.get("name") in expected and row.get("summary")
    ]


def _combine(rows: list[dict], model: str, key: str, backup: str | None) -> str:
    facts = "\n".join(f"- {row['name']}: {row['summary']}" for row in rows)
    schema = {
        "type": "OBJECT",
        "properties": {"group_summary": {"type": "STRING"}},
        "required": ["group_summary"],
    }
    data = gemini.generate_content(model, key, {
        "contents": [{"parts": [{"text": (
            "Combine these photo descriptions into a concise 35-70 word visual overview for an "
            "advertising script. Mention recurring appearance, useful viewpoints, setting, and "
            "visible use. Do not add claims or facts absent from the descriptions.\n\n" + facts
        )}]}],
        "generationConfig": {
            "responseMimeType": "application/json", "responseSchema": schema,
            "temperature": 0.2,
        },
    }, backup_key=backup)
    summary = str(_json_response(data).get("group_summary") or "").strip()
    if not summary:
        raise gemini.GeminiError("Gemini returned no usable combined photo summary.")
    return summary


def _json_response(data: dict) -> dict:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = next(part["text"] for part in reversed(parts) if part.get("text"))
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, StopIteration, json.JSONDecodeError) as exc:
        raise gemini.GeminiError(
            f"Gemini returned no usable photo-analysis JSON. Raw response: {str(data)[:300]}"
        ) from exc
    return parsed if isinstance(parsed, dict) else {}


def _batches(paths: list[Path]) -> list[list[Path]]:
    batches, current, size = [], [], 0
    for path in paths:
        file_size = path.stat().st_size
        if current and (len(current) >= MAX_BATCH_IMAGES or size + file_size > MAX_BATCH_BYTES):
            batches.append(current)
            current, size = [], 0
        current.append(path)
        size += file_size
    if current:
        batches.append(current)
    return batches


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
