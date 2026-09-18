"""Regression coverage for web/console behavior that does not need FFmpeg."""
from __future__ import annotations

import hashlib
import io

import pytest
import requests
import yaml

from reelfactory import cli, gemini, render, stock
from reelfactory.web.app import create_app


@pytest.fixture
def bare_project(tmp_path):
    root = tmp_path / "project"
    product = root / "products" / "chair"
    (product / "photos").mkdir(parents=True)
    (root / "out").mkdir()
    (root / "brand.yaml").write_text("name: Demo\n", encoding="utf-8")
    (product / "product.yaml").write_text(
        "name_en: Chair\n"
        "name_hi: Kursi\n"
        "price: Rs100\n"
        "offer: 20 percent off\n"
        "usp_en:\n- Strong\n"
        "target_seconds: 60\n",
        encoding="utf-8",
    )
    app = create_app(root / "brand.yaml", root / "products", root / "out")
    app.config.update(TESTING=True)
    return root, app.test_client()


def read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_product_fields_can_be_cleared(bare_project):
    root, client = bare_project
    response = client.post("/products/chair/edit", data={
        "name_en": "Chair", "name_hi": "Kursi", "tone": "value",
        "cta_action": "auto", "price": "", "offer": "", "usp_en": "",
        "target_seconds": "", "intent": "",
    })
    saved = read(root / "products" / "chair" / "product.yaml")
    assert response.status_code == 302
    for key in ("price", "offer", "usp_en", "target_seconds", "intent"):
        assert key not in saved


@pytest.mark.parametrize("kind", ["brand", "product"])
def test_broken_yaml_opens_a_repair_editor(bare_project, kind):
    root, client = bare_project
    if kind == "brand":
        path, url = root / "brand.yaml", "/brand"
    else:
        path, url = root / "products" / "chair" / "product.yaml", "/products/chair/edit"
    path.write_text("name: [oops\n", encoding="utf-8")
    response = client.get(url)
    assert response.status_code == 200
    assert 'name="source"' in response.get_data(as_text=True)


def test_repair_validates_before_overwriting(bare_project):
    root, client = bare_project
    path = root / "brand.yaml"
    path.write_text("name: [oops\n", encoding="utf-8")
    bad = client.post("/brand/repair", data={"source": "name: [still broken"})
    assert bad.status_code == 400
    assert path.read_text(encoding="utf-8") == "name: [oops\n"
    good = client.post("/brand/repair", data={"source": "name: Fixed\n"})
    assert good.status_code == 302
    assert read(path)["name"] == "Fixed"


def test_normal_save_removes_unknown_keys(bare_project):
    root, client = bare_project
    brand = root / "brand.yaml"
    brand.write_text("name: Demo\ntypo_setting: x\n", encoding="utf-8")
    client.post("/brand", data={"name": "Demo", "music_volume": "0.12"})
    assert "typo_setting" not in read(brand)

    product = root / "products" / "chair" / "product.yaml"
    data = read(product)
    data["typo_field"] = "x"
    product.write_text(yaml.safe_dump(data), encoding="utf-8")
    client.post("/products/chair/edit", data={
        "name_en": "Chair", "name_hi": "Kursi", "tone": "value",
        "cta_action": "auto",
    })
    assert "typo_field" not in read(product)


def test_unsupported_product_upload_is_reported_without_creating_product(bare_project):
    root, client = bare_project
    response = client.post("/products/new", data={
        "slug": "bad-photo", "name_en": "A", "name_hi": "B",
        "photos": (io.BytesIO(b"GIF89a"), "photo.gif"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "Unsupported photo or clip" in response.get_data(as_text=True)
    assert not (root / "products" / "bad-photo").exists()


def test_output_route_cannot_escape_its_root(bare_project):
    root, client = bare_project
    (root / "brand.yaml").write_text("name: TOP-SECRET\n", encoding="utf-8")
    output = root / "out" / "chair"
    output.mkdir()
    (output / "caption.txt").write_text("safe output", encoding="utf-8")
    assert client.get("/out/chair/caption.txt").data == b"safe output"
    response = client.get("/out/../brand.yaml")
    assert response.status_code == 404
    assert b"TOP-SECRET" not in response.data


def test_output_delete_route_cannot_escape_its_root(bare_project):
    root, client = bare_project
    in_project = root / "keep.txt"
    above_project = root.parent / "keep-above.txt"
    in_project.write_text("keep", encoding="utf-8")
    above_project.write_text("keep", encoding="utf-8")

    first = client.post(
        "/products/%2E%2E/build/delete", data={"delete_file": in_project.name}
    )
    second = client.post(
        "/products/%2E%2E%5C%2E%2E/build/delete",
        data={"delete_file": above_project.name},
    )

    assert first.status_code == second.status_code == 404
    assert in_project.read_text(encoding="utf-8") == "keep"
    assert above_project.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("slug", ["%2E%2E", "%2E%2E%5Coutside"])
def test_every_product_route_rejects_noncanonical_slugs(bare_project, slug):
    _root, client = bare_project

    assert client.get(f"/products/{slug}/edit").status_code == 404


def test_gemini_connection_errors_never_expose_the_api_key(monkeypatch):
    secret = "super-secret-api-key"

    def fail(url, **kwargs):
        assert kwargs["headers"] == {"x-goog-api-key": secret}
        assert "params" not in kwargs
        raise requests.ConnectionError(f"could not connect to {url}")

    monkeypatch.setattr(gemini.requests, "post", fail)
    monkeypatch.setattr(gemini.time, "sleep", lambda _seconds: None)

    with pytest.raises(gemini.GeminiError) as caught:
        gemini.generate_content("test-model", secret, {"contents": []})

    assert secret not in str(caught.value)


def test_stock_connection_errors_never_expose_query_string_keys(monkeypatch):
    secret = "private-pixabay-key"

    def fail(url, **kwargs):
        request = requests.Request("GET", url, params=kwargs["params"]).prepare()
        raise requests.ConnectionError(f"could not connect to {request.url}")

    monkeypatch.setattr(stock.requests, "get", fail)

    with pytest.raises(stock.StockError) as caught:
        stock._get("https://pixabay.com/api/", "pixabay", params={"key": secret})

    assert secret not in str(caught.value)


def test_media_probe_caches_refresh_when_a_file_is_replaced(tmp_path, monkeypatch):
    photo = tmp_path / "1.jpg"
    photo.write_bytes(b"first")
    sizes = iter(["100x200", "300x400"])
    signals = iter([
        "signalstats.YAVG=1\nsignalstats.UAVG=2\nsignalstats.VAVG=3",
        "signalstats.YAVG=4\nsignalstats.UAVG=5\nsignalstats.VAVG=6",
    ])

    def fake_run(args, **_kwargs):
        return next(sizes) if args[0] == "ffprobe" else next(signals)

    render._sizes.clear()
    render._stats.clear()
    monkeypatch.setattr(render, "_run", fake_run)
    assert render._probe_size(photo) == (100, 200)
    assert render._signal_stats(photo) == (1.0, 2.0, 3.0)

    photo.write_bytes(b"replacement-is-a-different-size")
    assert render._probe_size(photo) == (300, 400)
    assert render._signal_stats(photo) == (4.0, 5.0, 6.0)


def test_missing_settings_are_editable(bare_project):
    _root, client = bare_project
    brand_html = client.get("/brand").get_data(as_text=True)
    for name in ("music_bpm", "music_offset"):
        assert f'name="{name}"' in brand_html
    product_html = client.get("/products/chair/edit").get_data(as_text=True)
    for name in ("template", "seed", "script_en", "script_hi", "overlay_en", "overlay_hi"):
        assert f'name="{name}"' in product_html


def test_brand_save_restores_blank_model_defaults(bare_project):
    root, client = bare_project
    client.post("/brand", data={
        "name": "Demo", "music_volume": "0.12", "gemini_script_model": "",
        "gemini_tts_model": "", "gemini_voice": "",
        "local_script_model": "", "local_base_url": "",
    })
    saved = read(root / "brand.yaml")
    assert saved["gemini_script_model"] == "gemini-2.5-flash"
    assert saved["gemini_voice"] == "Kore"
    assert saved["local_base_url"] == "http://localhost:11434/v1"


def test_product_page_exposes_explicit_photo_analysis_action(bare_project):
    root, client = bare_project
    (root / "products" / "chair" / "photos" / "1.jpg").write_bytes(b"test image")
    html = client.get("/products/chair/edit").get_data(as_text=True)
    assert "Photo understanding" in html
    assert "/products/chair/photos/analyze" in html
    assert 'class="photo-analysis-panel"' in html
    assert 'form="photo-analysis-form"' in html
    assert "Still images are sent to Gemini only when you press Analyze or Refresh" in html


def test_fresh_photo_analysis_is_reviewable_and_editable_in_photos_step(bare_project):
    root, client = bare_project
    photo = root / "products" / "chair" / "photos" / "1.jpg"
    photo.write_bytes(b"test image")
    analysis = {
        "version": 1,
        "model": "gemini-2.5-flash",
        "updated_at": "2026-08-31T10:00:00+00:00",
        "group_summary": "A compact blue rack shown from the front.",
        "photos": [{
            "name": "1.jpg",
            "sha256": hashlib.sha256(photo.read_bytes()).hexdigest(),
            "summary": "Front view of a compact blue rack with open shelves.",
        }],
    }
    (photo.parent.parent / "photo_analysis.yaml").write_text(
        yaml.safe_dump(analysis, sort_keys=False), encoding="utf-8"
    )

    html = client.get("/products/chair/edit").get_data(as_text=True)
    assert "Gemini sees" in html
    assert "Front view of a compact blue rack with open shelves." in html
    assert "A compact blue rack shown from the front." in html
    assert "Current" in html
    assert 'form="photo-summary-form"' in html
    assert "Save your product changes first" in html
    # The two special actions are external forms, not invalid forms nested in
    # the main product editor.
    assert html.index('id="photo-analysis-form"') > html.index("</form>")

    response = client.post(
        "/products/chair/photos/summary",
        data={"group_summary": "User-corrected visual summary."},
    )
    assert response.status_code == 302
    assert "step=photos" in response.headers["Location"]
    saved = read(photo.parent.parent / "photo_analysis.yaml")
    assert saved["group_summary"] == "User-corrected visual summary."

    response = client.post(
        "/products/chair/photos/summary/archive",
        data={
            "group_summary": "Reusable showroom summary.",
            "summary_name": "Showroom set",
        },
    )
    assert response.status_code == 302
    archive = read(photo.parent.parent / "saved_photo_summaries.yaml")
    assert archive["summaries"][0]["name"] == "Showroom set"
    assert archive["summaries"][0]["group_summary"] == "Reusable showroom summary."
    library_html = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Saved photo summaries" in library_html
    assert "Showroom set" in library_html
    assert "/products/chair/photos/summary/restore" in library_html

    client.post(
        "/products/chair/photos/summary",
        data={"group_summary": "A later temporary edit."},
    )
    response = client.post(
        "/products/chair/photos/summary/restore", data={"summary_pick": "0"}
    )
    assert response.status_code == 302
    assert read(photo.parent.parent / "photo_analysis.yaml")["group_summary"] == (
        "Reusable showroom summary."
    )

    response = client.post(
        "/products/chair/photos/summary/delete", data={"summary_pick": "0"}
    )
    assert response.status_code == 302
    assert read(photo.parent.parent / "saved_photo_summaries.yaml") == {"summaries": []}


def test_photo_analysis_route_returns_to_product_with_result(bare_project, monkeypatch):
    root, client = bare_project
    (root / "products" / "chair" / "photos" / "1.jpg").write_bytes(b"test image")
    monkeypatch.setattr(
        "reelfactory.web.app.photo_analysis.analyze",
        lambda product, brand: {"photos": [{"name": "1.jpg"}]},
    )
    response = client.post("/products/chair/photos/analyze")
    assert response.status_code == 302
    assert "Analyzed+1+photo" in response.headers["Location"]
    assert "step=photos" in response.headers["Location"]
    landed = client.get(response.headers["Location"]).get_data(as_text=True)
    assert 'data-start-step="1"' in landed


def test_product_script_can_be_saved_and_loaded_without_regenerating(bare_project):
    root, client = bare_project
    (root / "products" / "chair" / "photos" / "1.jpg").write_bytes(b"test image")
    response = client.post("/products/chair/script/save", data={
        "lang": "en",
        "script": "ai",
        "save_name": "Launch version",
        "seg_role_en": "hook",
        "seg_vo_en": "Meet the compact chair.",
        "seg_overlay_en": "Compact comfort",
        "seg_photo_en": "1.jpg",
    })
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Saved scripts for chair" in html

    stored = read(root / "products" / "chair" / "saved_scripts.yaml")
    assert stored["en"][0]["writer"] == "ai"
    assert stored["en"][0]["segments"][0] == {
        "role": "hook",
        "vo": "Meet the compact chair.",
        "overlay": "Compact comfort",
        "photo": "1.jpg",
    }

    loaded = client.post(
        "/products/chair/script/load",
        data={"lang": "en", "load_pick": "en:0"},
    ).get_data(as_text=True)
    assert "Meet the compact chair." in loaded
    assert "Compact comfort" in loaded


def test_new_product_settings_round_trip_and_can_be_removed(bare_project):
    root, client = bare_project
    product = root / "products" / "chair" / "product.yaml"
    fields = {
        "name_en": "Chair", "name_hi": "Kursi", "tone": "value",
        "cta_action": "auto", "template": "bold", "seed": "42",
        "script_en": "First line\nSecond line", "overlay_en": "First\nSecond",
    }
    client.post("/products/chair/edit", data=fields)
    saved = read(product)
    assert saved["template"] == "bold"
    assert saved["seed"] == 42
    assert saved["script_en"] == ["First line", "Second line"]
    assert saved["overlay_en"] == ["First", "Second"]

    fields.update(template="", seed="", script_en="", overlay_en="")
    client.post("/products/chair/edit", data=fields)
    saved = read(product)
    for key in ("template", "seed", "script_en", "overlay_en"):
        assert key not in saved


def test_console_streams_are_reconfigured_to_utf8(monkeypatch):
    calls = []

    class Stream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(cli.sys, "stdout", Stream())
    monkeypatch.setattr(cli.sys, "stderr", Stream())
    cli._utf8_console()
    assert calls == [
        {"encoding": "utf-8", "errors": "replace"},
        {"encoding": "utf-8", "errors": "replace"},
    ]
