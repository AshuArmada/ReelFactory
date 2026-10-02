"""Scene uploads stay local and can be saved and rendered as scene choices."""
from io import BytesIO

import pytest

from conftest import selected_photos


UPLOAD = "/products/test-rack/scenes/media"


def test_scene_upload_does_not_replace_existing_photos(client, project):
    photos = project / "products/test-rack/photos"
    original = (photos / "1.jpg").read_bytes()
    response = client.post(UPLOAD, data={"media": (BytesIO(original), "../../1.JPG")})
    assert response.status_code == 201
    name = response.json["name"]
    assert name.startswith("scene-") and name.endswith(".jpg")
    assert (photos / name).read_bytes() == original
    assert (photos / "1.jpg").read_bytes() == original
    assert client.get(response.json["url"]).data == original

    # Saving and loading must retain the uploaded picture and exact words.
    data = {"lang": "en", "save_name": "New scene", "seg_role_en": "hook",
            "seg_vo_en": "Keep these words", "seg_overlay_en": "Keep this caption",
            "seg_photo_en": name}
    saved = client.post("/products/test-rack/script/save", data=data)
    assert saved.status_code == 200
    assert selected_photos(saved.get_data(as_text=True)) == [name]
    from reelfactory.web.app import _load_saved_scripts
    scripts = _load_saved_scripts(project / "products", "test-rack")
    assert scripts["en"][0]["segments"][0]["photo"] == name


@pytest.mark.parametrize("filename,payload", [("bad.svg", b"<svg/>"), ("empty.jpg", b"")])
def test_rejected_scene_upload_leaves_library_unchanged(client, project, filename, payload):
    photos = project / "products/test-rack/photos"
    before = set(photos.iterdir())
    response = client.post(UPLOAD, data={"media": (BytesIO(payload), filename)})
    assert response.status_code == 400
    assert response.json["error"]
    assert set(photos.iterdir()) == before


def test_scene_upload_requires_file_and_existing_product(client):
    assert client.post(UPLOAD).status_code == 400
    assert client.post("/products/missing/scenes/media", data={
        "media": (BytesIO(b"photo"), "photo.jpg")}).status_code == 404


def test_uploaded_scene_reaches_renderer(client, project, monkeypatch):
    photo = (project / "products/test-rack/photos/1.jpg").read_bytes()
    name = client.post(UPLOAD, data={"media": (BytesIO(photo), "new.jpg")}).json["name"]
    calls = []
    def build(*args, **kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr("reelfactory.cli.build_one", build)
    response = client.post("/products/test-rack/build", data={
        "lang": "en", "tts": "silent", "seg_vo_en": "Same narration",
        "seg_photo_en": name, "seg_role_en": "hook", "seg_overlay_en": "Same caption"})
    assert response.status_code == 200
    assert calls[0]["photo_names"] == [name]
    assert calls[0]["segments"][0].vo == "Same narration"


def test_collection_upload_retains_source_product_matching(client, project, photos):
    from conftest import PRODUCT, make_product
    from reelfactory.config import Product
    from reelfactory.collections import render_photos
    from reelfactory.script import Segment
    make_product(project, "chair", dict(PRODUCT, name_en="Chair"), photos)
    response = client.post("/collections/new", data={"products": ["test-rack", "chair"]})
    slug = response.location.split("/")[-2]
    folder = project / "products" / slug
    product = Product.load(folder)
    source = next(iter(product.collection_members[0]["media"].values()))
    uploaded = client.post(f"/products/{slug}/scenes/media", data={
        "source_photo": source, "media": (BytesIO((folder / "photos" / source).read_bytes()), "new.jpg")})
    assert uploaded.status_code == 201
    name = uploaded.json["name"]
    updated = Product.load(folder)
    assert name in updated.collection_members[0]["media"].values()
    assert name not in updated.collection_members[1]["media"].values()
    assert render_photos(updated, [Segment("usp", "Test Rack is sturdy", "Rack")], [name]) == [name]
    assert not (project / "products/test-rack/photos" / name).exists()
    rejected = client.post(f"/products/{slug}/scenes/media", data={
        "source_photo": "missing.jpg", "media": (BytesIO(b"photo"), "new.jpg")})
    assert rejected.status_code == 400
