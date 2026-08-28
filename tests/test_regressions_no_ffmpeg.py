"""Regression coverage for web/console behavior that does not need FFmpeg."""
from __future__ import annotations

import io

import pytest
import yaml

from reelfactory import cli
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


def test_missing_settings_are_editable(bare_project):
    _root, client = bare_project
    brand_html = client.get("/brand").get_data(as_text=True)
    for name in ("music_bpm", "music_offset"):
        assert f'name="{name}"' in brand_html
    product_html = client.get("/products/chair/edit").get_data(as_text=True)
    for name in ("template", "seed", "script_en", "script_hi", "overlay_en", "overlay_hi"):
        assert f'name="{name}"' in product_html


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
