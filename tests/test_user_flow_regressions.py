"""User-flow regressions using disposable data and no external services."""
import pytest

from conftest import CSRFClient, form, write_yaml
from reelfactory.render import RenderError
from reelfactory.web.app import create_app


@pytest.fixture
def workspace(tmp_path):
    product = tmp_path / "products" / "chair"
    (product / "photos").mkdir(parents=True)
    (product / "photos" / "1.jpg").write_bytes(b"photo")
    write_yaml(product / "product.yaml", {"name_en": "Chair", "name_hi": "Kursi"})
    write_yaml(tmp_path / "brand.yaml", {"name": "Demo"})
    app = create_app(tmp_path / "brand.yaml", tmp_path / "products", tmp_path / "out")
    app.config.update(TESTING=True)
    app.test_client_class = CSRFClient
    return tmp_path, app.test_client()


@pytest.mark.parametrize("fails", [False, True])
def test_build_keeps_unselected_drafts_and_unfinished_rows(workspace, monkeypatch, fails):
    _, client = workspace
    calls = []

    def build(*args, **kwargs):
        calls.append(args[2])
        if fails:
            raise RenderError("Render failed")
        return []

    monkeypatch.setattr("reelfactory.cli.build_one", build)
    response = client.post("/products/chair/build", data=form(
        ("lang", "en"), ("seg_vo_en", "Keep this English draft"),
        ("seg_vo_en", ""), ("seg_overlay_en", "Opening"),
        ("seg_overlay_en", "Unfinished scene"), ("seg_photo_en", "1.jpg"),
        ("seg_photo_en", "1.jpg"), ("seg_vo_hi", "Keep this unchecked draft"),
        ("seg_photo_hi", "1.jpg"), ("tts", "silent"),
    ))
    assert response.status_code == 200
    assert calls == ["en"]
    html = response.get_data(as_text=True)
    assert "Keep this unchecked draft" in html
    assert "Unfinished scene" in html


@pytest.mark.parametrize("name", ["con", "aux", "nul", "prn", "com1", "lpt9"])
def test_windows_device_names_are_rejected_before_creation(workspace, name):
    root, client = workspace
    response = client.post("/products/new", data={
        "slug": name, "name_en": "Chair", "name_hi": "Kursi",
    })
    assert response.status_code == 400
    assert not (root / "products" / name / "product.yaml").is_file()


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("missing", ["name_en", "name_hi"])
def test_blank_names_do_not_save_broken_products_or_delete_media(workspace, editing, missing):
    root, client = workspace
    product = root / "products" / "chair"
    before = (product / "product.yaml").read_bytes()
    values = {"slug": "new-chair", "name_en": "Chair", "name_hi": "Kursi",
              "delete_photo": "1.jpg", missing: "   "}
    response = client.post("/products/chair/edit" if editing else "/products/new", data=values)
    assert response.status_code == 400
    assert b"required" in response.data
    assert (product / "product.yaml").read_bytes() == before
    assert (product / "photos" / "1.jpg").exists()
    assert not (root / "products" / "new-chair").exists()


def test_queue_stays_in_editor_with_all_drafts(workspace, monkeypatch):
    _, client = workspace
    queue = client.application.extensions["render_queue"]
    monkeypatch.setattr(queue, "start", lambda: None)
    response = client.post("/products/chair/queue", data={
        "lang": "en", "tts": "silent", "seg_vo_en": "Reviewed English draft",
        "seg_photo_en": "1.jpg", "seg_vo_hi": "Unchecked draft", "seg_photo_hi": "1.jpg",
    })
    assert response.status_code == 200
    assert b"background render queue" in response.data
    assert b"Reviewed English draft" in response.data
    assert b"Unchecked draft" in response.data
    assert b'data-page-key="/products/chair/build"' in response.data
    assert len(queue.jobs()) == 1


def test_duplicate_keeps_source_page_and_offers_copy_link(workspace):
    root, client = workspace
    response = client.post("/products/chair/duplicate", data={"new_slug": "chair-copy"}, follow_redirects=True)
    assert b'data-page-key="/products/chair/edit"' in response.data
    assert b'href="/products/chair-copy/edit">Open copy</a>' in response.data
    assert (root / "products/chair-copy/product.yaml").exists()


def test_output_delete_preserves_posted_editor_state(workspace):
    root, client = workspace
    output = root / "out/chair/old.txt"
    output.parent.mkdir()
    output.write_text("Old export", encoding="utf-8")
    response = client.post("/products/chair/build/delete", data={
        "delete_file": "old.txt", "lang": "en", "tts": "silent",
        "seg_vo_en": "Unsaved words", "seg_photo_en": "1.jpg",
    })
    assert response.status_code == 200
    assert not output.exists()
    assert b"Unsaved words" in response.data
    assert b'value="silent" selected' in response.data


def test_stock_add_stays_with_search_results(workspace, monkeypatch):
    root, client = workspace
    from reelfactory import stock
    photo = stock.Photo("pexels-1", "https://images.pexels.com/1.jpg", "/thumb.jpg",
                        "pexels", "Example", "https://pexels.com/photo/1", 1500, 2600)
    saved = root / "products/chair/photos/2.jpg"

    def download(chosen, destination, **kwargs):
        assert chosen == [photo]
        saved.write_bytes(b"downloaded photo")
        return [(saved, photo)]

    monkeypatch.setattr("reelfactory.web.app._stock_rows", lambda form: [photo])
    monkeypatch.setattr(stock, "download", download)
    response = client.post("/products/chair/photos/stock/add", data={"pick": "pexels-1", "query": "blue chair"})
    assert response.status_code == 200
    assert b'data-page-key="/products/chair/photos/stock"' in response.data
    assert b"Added 1 photo" in response.data
    assert b'value="blue chair"' in response.data
    assert b'value="pexels-1"' in response.data
