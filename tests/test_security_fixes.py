"""Regressions for the October security and behavior review, using disposable data."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import time
import threading

import pytest
from flask.testing import FlaskClient

from conftest import PRODUCT, make_product, read_yaml
from reelfactory import cli, storage
from reelfactory.config import Brand, Product
from reelfactory.script import Segment
from reelfactory.web.preview_cache import PreviewCache


def test_missing_invalid_and_foreign_tokens_cannot_delete(app, project):
    client = FlaskClient(app, app.response_class)
    export = project / "out/test-rack/final.mp4"
    export.parent.mkdir()
    export.write_bytes(b"keep me")
    url = "/products/test-rack/build/delete"
    data = {"delete_file": export.name}
    assert client.post(url, data=data).status_code == 403
    client.get("/")
    with client.session_transaction() as session:
        token = session["request_csrf"]
    for headers in ({"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": "\u2603"},
                    {"X-CSRF-Token": token, "Origin": "http://foreign.example"},
                    {"X-CSRF-Token": token, "Sec-Fetch-Site": "cross-site"}):
        assert client.post(url, data=data, headers=headers).status_code == 403
        assert export.read_bytes() == b"keep me"
    assert client.post(url, data={**data, "_csrf_token": token}).status_code == 200
    assert not export.exists()


def test_host_and_upload_limits(app):
    client = FlaskClient(app, app.response_class)
    assert client.get("/", headers={"Host": "foreign.example"}).status_code == 400
    assert app.config["MAX_CONTENT_LENGTH"] == 256 * 1024 * 1024
    app.config["MAX_CONTENT_LENGTH"] = 16
    assert client.post("/products/new", data={"name": "x" * 100}).status_code == 413


@pytest.mark.parametrize("key", ["logo", "music"])
def test_repair_cannot_expose_external_asset(client, project, tmp_path, key):
    import yaml
    dummy = tmp_path / ("external.png" if key == "logo" else "external.mp3")
    dummy.write_bytes(b"private dummy contents")
    data = read_yaml(project / "brand.yaml")
    data[key] = str(dummy)
    assert client.post("/brand/repair", data={"source": yaml.safe_dump(data)}).status_code == 302
    assert client.get(f"/brand/asset/{key}").status_code == 404


def test_concurrent_output_names_are_reserved(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: cli._free_path(tmp_path, "reel", ".mp4"), range(32)))
    assert len(set(paths)) == 32
    assert all(path.exists() for path in paths)


def test_failed_atomic_save_preserves_original(tmp_path, monkeypatch):
    dest = tmp_path / "product.yaml"
    dest.write_text("original", encoding="utf-8")
    def fail(*args):
        raise OSError("disk error")
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="disk error"):
        storage.atomic_text(dest, "replacement")
    assert dest.read_text() == "original"
    assert list(tmp_path.iterdir()) == [dest]


@pytest.mark.skipif(storage.os.name != "nt", reason="Windows sharing violation")
def test_atomic_save_retries_temporary_windows_sharing_violation(tmp_path, monkeypatch):
    dest = tmp_path / "caption.txt"
    dest.write_text("old")
    replace = storage.os.replace
    calls = []
    def briefly_locked(source, target):
        calls.append(target)
        if len(calls) == 1:
            assert dest.read_text() == "old"
            raise PermissionError("sharing violation")
        return replace(source, target)
    monkeypatch.setattr(storage.os, "replace", briefly_locked)
    storage.atomic_text(dest, "complete new caption")
    assert len(calls) == 2
    assert dest.read_text() == "complete new caption"


@pytest.mark.parametrize("fail", [False, True])
def test_render_publishes_separate_complete_files_and_cleans_failures(project, monkeypatch, fail):
    from reelfactory import templates
    from reelfactory.render import RenderError
    prod = Product.load(project / "products/test-rack")
    brand = Brand.load(project / "brand.yaml")
    monkeypatch.setattr(cli.voice, "synthesize", lambda *a, **kw: [SimpleNamespace(duration=1, words=[])])
    monkeypatch.setattr(cli.voice, "concat", lambda *a, **kw: project / "unused.wav")
    monkeypatch.setattr(cli.subtitles, "write", lambda *a, **kw: project / "unused.ass")
    barrier = threading.Barrier(2)
    def render(shots, ass, track, destination, *args, **kwargs):
        destination.write_bytes(shots[0].photo.name.encode())
        barrier.wait(timeout=15)
        if fail:
            raise RenderError("render failed")
    monkeypatch.setattr(cli, "render", render)
    def build(name):
        return cli._render_variant(prod, brand, "en", ["9:16"], project / "out",
                                   SimpleNamespace(tts="silent", no_music=True, preset="ultrafast"),
                                   templates.load("classic"), [Segment("hook", "Hello", "Hello")],
                                   photo_names=[name])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(build, name) for name in ["1.jpg", "2.jpg"]]
        if fail:
            for future in futures:
                with pytest.raises(RenderError, match="render failed"):
                    future.result()
        else:
            paths = [p for f in futures for p in f.result() if p.suffix == ".mp4"]
            assert len(set(paths)) == 2
            assert {p.read_bytes() for p in paths} == {b"1.jpg", b"2.jpg"}
    remaining = list((project / "out/test-rack").glob("*.mp4"))
    assert len(remaining) == (0 if fail else 2)
    assert not any(p.name.startswith(".render-") for p in remaining)


def test_invalid_image_is_rejected_before_any_product_changes(client, project):
    folder = project / "products/test-rack"
    before = (folder / "product.yaml").read_bytes()
    assert client.post("/products/test-rack/scenes/media", data={
        "media": (BytesIO(b"not an image"), "bad.jpg")}).status_code == 400
    response = client.post("/products/test-rack/edit", data={
        "name_en": "Do not save", "delete_photo": "1.jpg",
        "photos": (BytesIO(b"not an image"), "bad.jpg")})
    assert response.status_code == 400
    assert (folder / "product.yaml").read_bytes() == before
    assert sorted(p.name for p in (folder / "photos").iterdir()) == ["1.jpg", "2.jpg", "3.jpg"]


def test_unselected_corrupt_image_does_not_block_build(project, monkeypatch):
    folder = project / "products/test-rack"
    (folder / "photos/bad.jpg").write_bytes(b"bad")
    calls = []
    monkeypatch.setattr(cli, "_render_variant", lambda *a, **kw: calls.append(kw) or [])
    cli.build_one(Product.load(folder), Brand.load(project / "brand.yaml"), "en", ["9:16"],
                  project / "out", SimpleNamespace(tts="silent", template="classic"),
                  segments=[Segment("hook", "Keep my draft", "Draft")], photo_names=["1.jpg"])
    assert calls[0]["photo_names"] == ["1.jpg"]


def test_simultaneous_collection_uploads_keep_explicit_product(app, client, project, photos, monkeypatch):
    from reelfactory.collections import render_photos
    import reelfactory.web.app as web
    make_product(project, "chair", dict(PRODUCT, name_en="Chair"), photos)
    response = client.post("/collections/new", data={"products": ["test-rack", "chair"]})
    slug = response.location.split("/")[-2]
    folder = project / "products" / slug
    prod = Product.load(folder)
    source = next(iter(prod.collection_members[0]["media"].values()))
    payload = (folder / "photos" / source).read_bytes()
    write = web.write_yaml
    def delayed_write(*args, **kwargs):
        time.sleep(0.05)  # Overlap the old read/modify/write implementation.
        return write(*args, **kwargs)
    monkeypatch.setattr(web, "write_yaml", delayed_write)
    def upload(_):
        with app.test_client() as local:
            return local.post(f"/products/{slug}/scenes/media", data={
                "source_photo": source, "member_slug": "chair", "media": (BytesIO(payload), "new.jpg")})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(upload, range(2)))
    assert all(r.status_code == 201 for r in responses)
    names = [r.json["name"] for r in responses]
    updated = Product.load(folder)
    chair = next(m for m in updated.collection_members if m["slug"] == "chair")
    rack = next(m for m in updated.collection_members if m["slug"] == "test-rack")
    assert set(names).issubset(chair["media"].values())
    assert not set(names).intersection(rack["media"].values())
    assert render_photos(updated, [Segment("usp", "Chair is sturdy", "Chair")], names[:1]) == names[:1]
    assert 'data-scene-member' in client.get(f"/products/{slug}/build").get_data(as_text=True)


def test_preview_retention_failure_and_clear_preserve_exports(tmp_path):
    export = tmp_path / "final.mp4"
    export.write_bytes(b"final")
    cache = PreviewCache(tmp_path)
    jobs = []
    for _ in range(5):
        with cache.job() as folder:
            (folder / "preview.mp4").write_bytes(b"preview")
            jobs.append(folder)
        time.sleep(0.01)
    assert [p.exists() for p in jobs] == [False, False, True, True, True]
    with pytest.raises(ValueError):
        with cache.job() as failed:
            raise ValueError("render failed")
    assert not failed.exists()
    assert all(p.exists() for p in jobs[-3:])
    with cache.job() as active:
        result = cache.clear()
        assert result == {"removed": 3, "failed": 0}
        assert active.exists()
    assert cache.clear() == {"removed": 1, "failed": 0}
    assert export.read_bytes() == b"final"


@pytest.mark.parametrize("field,value", [("aspect", "bad"), ("lang", "bad"), ("tts", "bad"),
                                         ("preset", "bad"), ("template", "bad")])
def test_invalid_render_settings_return_400_without_rendering(client, monkeypatch, field, value):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid request reached renderer")
    monkeypatch.setattr(cli, "build_one", unexpected)
    response = client.post("/products/test-rack/build", data={
        "lang": "en", "tts": "silent", "seg_vo_en": "Keep these words", "seg_photo_en": "1.jpg",
        field: value})
    assert response.status_code == 400
    assert "Keep these words" in response.get_data(as_text=True)
