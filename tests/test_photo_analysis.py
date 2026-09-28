"""Cached Gemini photo descriptions and prompt grounding (no network/FFmpeg)."""
from __future__ import annotations

import hashlib
import json
import pytest

from reelfactory import ad_prompt, photo_analysis
from reelfactory.config import Brand, Product, write_yaml


def product_with_photos(tmp_path, names=("1.jpg", "2.png")):
    product_dir = tmp_path / "products" / "chair"
    photo_dir = product_dir / "photos"
    photo_dir.mkdir(parents=True)
    paths = []
    for i, name in enumerate(names):
        path = photo_dir / name
        path.write_bytes((f"fake-image-{i}" * 20).encode())
        paths.append(path)
    return Product(
        slug="chair", dir=product_dir, photos=paths,
        name_en="Blue rack", name_hi="नीला रैक", usp_en=["Five open shelves"],
    )


def response(value):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(value)}]}}]}


def test_analysis_is_saved_and_added_to_the_shared_prompt(tmp_path, monkeypatch):
    product = product_with_photos(tmp_path)
    calls = []

    def generate(model, key, payload, **kwargs):
        calls.append(payload)
        if len(calls) == 1:
            return response({"photos": [
                {"name": "1.jpg", "summary": "Front view of a blue five-shelf rack."},
                {"name": "2.png", "summary": "Close view of the shelf joints."},
            ]})
        return response({"group_summary": (
            "A blue five-shelf rack shown from the front and in a close view of its joints."
        )})

    monkeypatch.setattr(photo_analysis.gemini, "resolve_key", lambda explicit=None: "test-key")
    monkeypatch.setattr(photo_analysis.gemini, "resolve_backup_key", lambda explicit=None: None)
    monkeypatch.setattr(photo_analysis.gemini, "generate_content", generate)

    saved = photo_analysis.analyze(product, Brand(), model="gemini-2.5-flash")
    assert saved["group_summary"].startswith("A blue five-shelf rack")
    assert photo_analysis.path_for(product.dir).exists()
    assert photo_analysis.status(product.dir, [p.name for p in product.photos])["fresh"]
    image_parts = [part for part in calls[0]["contents"][0]["parts"] if "inlineData" in part]
    assert len(image_parts) == 2
    assert image_parts[0]["inlineData"]["mimeType"] == "image/jpeg"

    prompt = ad_prompt.build_prompt(product, Brand(), "en", product.usp_en)
    assert "VISUAL OBSERVATIONS FROM THE UPLOADED PHOTOS" in prompt
    assert "Front view of a blue five-shelf rack" in prompt
    assert "Never infer price, material, capacity, warranty" in prompt
    assert "Close view of the shelf joints" in prompt
    combine_prompt = calls[1]["contents"][0]["parts"][0]["text"]
    assert "Front view of a blue five-shelf rack" in combine_prompt
    assert "Close view of the shelf joints" in combine_prompt
    image_prompt = calls[0]["contents"][0]["parts"][0]["text"]
    for prompt in (image_prompt, combine_prompt):
        assert "PRODUCT ADVERTISING BRIEF" in prompt
        assert "Blue rack" in prompt and "Five open shelves" in prompt
    assert "suggested advertising use" in image_prompt
    assert "flag a mismatch" in image_prompt
    assert "suggested story progression" in combine_prompt


def test_advertising_context_changes_refresh_cached_analysis(tmp_path, monkeypatch):
    product = product_with_photos(tmp_path)
    brand = Brand(name="Shop", audience="Shop owners")
    briefs = []
    monkeypatch.setattr(photo_analysis.gemini, "resolve_key", lambda *a: "test-key")
    monkeypatch.setattr(photo_analysis.gemini, "resolve_backup_key", lambda: None)

    def batch(paths, model, key, backup, brief):
        briefs.append(brief)
        return [{"name": p.name, "summary": "Visible shelves; useful for the product introduction."} for p in paths]

    monkeypatch.setattr(photo_analysis, "_analyze_batch", batch)
    monkeypatch.setattr(photo_analysis, "_combine", lambda *a: "Product advertising context.")
    photo_analysis.analyze(product, brand)
    photo_analysis.analyze(product, brand)
    assert len(briefs) == 1  # Same photos and advertising brief reuse descriptions.
    product.usp_en = ["Adjustable shelves"]
    photo_analysis.analyze(product, brand)
    assert len(briefs) == 2 and "Adjustable shelves" in briefs[-1]
    brand.audience = "Warehouse owners"
    photo_analysis.analyze(product, brand)
    assert len(briefs) == 3 and "Warehouse owners" in briefs[-1]
    old = photo_analysis.load(product.dir)
    old.pop("analysis_revision")
    write_yaml(photo_analysis.path_for(product.dir), old)
    photo_analysis.analyze(product, brand)
    assert len(briefs) == 4  # Old generic analysis is replaced on explicit update.


def test_photo_descriptions_survive_context_failure_and_are_reused(tmp_path, monkeypatch):
    product = product_with_photos(tmp_path)
    analyzed = []
    combined = []
    monkeypatch.setattr(photo_analysis.gemini, "resolve_key", lambda *a: "test-key")
    monkeypatch.setattr(photo_analysis.gemini, "resolve_backup_key", lambda: None)

    def analyze_batch(paths, *args):
        analyzed.extend(p.name for p in paths)
        return [{"name": p.name, "summary": f"Description of {p.name}"} for p in paths]

    def combine(rows, *args):
        combined.append([row["summary"] for row in rows])
        if len(combined) == 1:
            raise photo_analysis.gemini.GeminiError("Temporary summary failure")
        return "Overall context from both views."

    monkeypatch.setattr(photo_analysis, "_analyze_batch", analyze_batch)
    monkeypatch.setattr(photo_analysis, "_combine", combine)
    with pytest.raises(photo_analysis.gemini.GeminiError):
        photo_analysis.analyze(product, Brand())
    saved = photo_analysis.load(product.dir)
    assert len(saved["photos"]) == 2
    assert not saved["group_summary"]
    assert not photo_analysis.prompt_block(product)
    photo_analysis.analyze(product, Brand())
    assert analyzed == ["1.jpg", "2.png"]  # No repeated image calls on retry.
    assert combined[0] == combined[1] == ["Description of 1.jpg", "Description of 2.png"]
    prompt = photo_analysis.prompt_block(product)
    assert all(text in prompt for text in combined[1])
    assert "Overall context from both views" in prompt


def test_update_analyzes_only_changed_photos_but_combines_every_description(tmp_path, monkeypatch):
    product = product_with_photos(tmp_path)
    analyzed, combined = [], []
    monkeypatch.setattr(photo_analysis.gemini, "resolve_key", lambda *a: "test-key")
    monkeypatch.setattr(photo_analysis.gemini, "resolve_backup_key", lambda: None)

    def batch(paths, *args):
        analyzed.append([p.name for p in paths])
        return [{"name": p.name, "summary": f"Round {len(analyzed)}: {p.name}"} for p in paths]

    def combine(rows, *args):
        combined.append([r["summary"] for r in rows])
        return "Combined: " + "; ".join(combined[-1])

    monkeypatch.setattr(photo_analysis, "_analyze_batch", batch)
    monkeypatch.setattr(photo_analysis, "_combine", combine)
    photo_analysis.analyze(product, Brand())
    product.photos[1].write_bytes(b"replacement contents")
    new_photo = product.dir / "photos" / "3.jpg"
    new_photo.write_bytes(b"new view")
    product.photos.append(new_photo)
    photo_analysis.analyze(product, Brand())
    assert analyzed == [["1.jpg", "2.png"], ["2.png", "3.jpg"]]
    assert combined[-1] == ["Round 1: 1.jpg", "Round 2: 2.png", "Round 2: 3.jpg"]
    assert photo_analysis.status(product.dir)["fresh"]


def test_changed_photo_makes_analysis_stale_and_keeps_it_out_of_prompt(tmp_path, monkeypatch):
    product = product_with_photos(tmp_path, ("1.jpg",))
    answers = iter([
        response({"photos": [{"name": "1.jpg", "summary": "A blue rack."}]}),
        response({"group_summary": "A blue rack shown from the front."}),
    ])
    monkeypatch.setattr(photo_analysis.gemini, "resolve_key", lambda explicit=None: "test-key")
    monkeypatch.setattr(photo_analysis.gemini, "resolve_backup_key", lambda explicit=None: None)
    monkeypatch.setattr(
        photo_analysis.gemini, "generate_content", lambda *args, **kwargs: next(answers)
    )
    photo_analysis.analyze(product, Brand())

    product.photos[0].write_bytes(b"replacement image contents")
    assert photo_analysis.status(product.dir, ["1.jpg"])["state"] == "stale"
    prompt = ad_prompt.build_prompt(product, Brand(), "en", product.usp_en)
    assert "VISUAL OBSERVATIONS FROM THE UPLOADED PHOTOS" not in prompt


def test_combined_summary_is_editable_without_losing_fingerprints(tmp_path, monkeypatch):
    product = product_with_photos(tmp_path, ("1.jpg",))
    answers = iter([
        response({"photos": [{"name": "1.jpg", "summary": "A blue rack."}]}),
        response({"group_summary": "Original summary."}),
    ])
    monkeypatch.setattr(photo_analysis.gemini, "resolve_key", lambda explicit=None: "test-key")
    monkeypatch.setattr(photo_analysis.gemini, "resolve_backup_key", lambda explicit=None: None)
    monkeypatch.setattr(
        photo_analysis.gemini, "generate_content", lambda *args, **kwargs: next(answers)
    )
    photo_analysis.analyze(product, Brand())
    photo_analysis.update_group_summary(product.dir, "Corrected by the user.")
    info = photo_analysis.status(product.dir, ["1.jpg"])
    assert info["fresh"]
    assert info["group_summary"] == "Corrected by the user."


def test_blank_brand_model_falls_back_to_vision_capable_default(tmp_path, monkeypatch):
    product = product_with_photos(tmp_path, ("1.jpg",))
    seen = []
    answers = iter([
        response({"photos": [{"name": "1.jpg", "summary": "A blue rack."}]}),
        response({"group_summary": "A blue rack."}),
    ])
    monkeypatch.setattr(photo_analysis.gemini, "resolve_key", lambda explicit=None: "test-key")
    monkeypatch.setattr(photo_analysis.gemini, "resolve_backup_key", lambda explicit=None: None)

    def generate(model, *args, **kwargs):
        seen.append(model)
        return next(answers)

    monkeypatch.setattr(photo_analysis.gemini, "generate_content", generate)
    photo_analysis.analyze(product, Brand(gemini_script_model=""))
    assert seen == [photo_analysis.DEFAULT_MODEL, photo_analysis.DEFAULT_MODEL]


def test_named_summary_snapshot_restores_only_for_the_same_photos(tmp_path):
    product = product_with_photos(tmp_path, ("1.jpg",))
    digest = hashlib.sha256(product.photos[0].read_bytes()).hexdigest()
    write_yaml(photo_analysis.path_for(product.dir), {
        "version": 1,
        "model": "gemini-2.5-flash",
        "updated_at": "2026-08-31T10:00:00+00:00",
        "group_summary": "Original showroom view.",
        "photos": [{
            "name": "1.jpg", "sha256": digest,
            "summary": "A rack photographed in a showroom.",
        }],
    })

    saved = photo_analysis.save_snapshot(product.dir, "Showroom original")
    assert saved["name"] == "Showroom original"
    assert photo_analysis.load_saved(product.dir)[0]["photos"][0]["sha256"] == digest

    photo_analysis.update_group_summary(product.dir, "Temporary correction.")
    photo_analysis.restore_snapshot(product.dir, 0)
    info = photo_analysis.status(product.dir, ["1.jpg"])
    assert info["fresh"]
    assert info["group_summary"] == "Original showroom view."

    product.photos[0].write_bytes(b"a different future photo")
    photo_analysis.restore_snapshot(product.dir, 0)
    assert photo_analysis.status(product.dir, ["1.jpg"])["state"] == "stale"

    removed = photo_analysis.delete_snapshot(product.dir, 0)
    assert removed["name"] == "Showroom original"
    assert photo_analysis.load_saved(product.dir) == []
