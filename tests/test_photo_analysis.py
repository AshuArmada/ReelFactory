"""Cached Gemini photo descriptions and prompt grounding (no network/FFmpeg)."""
from __future__ import annotations

import json

from reelfactory import ad_prompt, photo_analysis
from reelfactory.config import Brand, Product


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
