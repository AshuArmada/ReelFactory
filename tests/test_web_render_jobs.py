"""Browser contracts for queued renders and portable presets."""
import json
import pytest
from reelfactory import cli
from reelfactory.render_jobs import RenderQueue
from test_render_jobs import enqueue, reviewed, stub_renderer


@pytest.fixture
def queue(app, monkeypatch):
    value = app.extensions["render_queue"]
    monkeypatch.setattr(value, "start", lambda: None)
    return value


def test_queue_route_preserves_review_and_returns_without_render(client, queue, project, monkeypatch):
    monkeypatch.setattr(cli, "build_one", lambda *a, **k: pytest.fail("Must not render in request"))
    response = client.post("/products/test-rack/queue", data=reviewed())
    assert response.status_code == 302
    assert response.location.endswith("/renders")
    job = queue.jobs()[0]
    recipe = queue.recipe(job)
    assert recipe["runs"][0]["segments"][0]["vo"] == "Your exact reviewed line."
    assert recipe["settings"]["tts"] == "silent"
    assert job["status"] == "queued"
    assert RenderQueue(project, project / "out").jobs()[0]["id"] == job["id"]


def test_selected_variants_queue_exact_words_and_tags(client, queue):
    from conftest import form
    data = form(("build_versions", "en:0"), ("build_versions", "en:1"),
                lang="en", aspect="9:16", tts="silent", preset="ultrafast",
                ver0_seg_vo_en="First reviewed hook", ver0_seg_role_en="hook", ver0_seg_photo_en="1.jpg",
                ver1_seg_vo_en="Second reviewed hook", ver1_seg_role_en="hook", ver1_seg_photo_en="2.jpg")
    response = client.post("/products/test-rack/queue", data=data)
    assert response.status_code == 302
    runs = queue.recipe(queue.jobs()[0])["runs"]
    assert [run["variant_tag"] for run in runs] == ["_v1", "_v2"]
    assert [run["segments"][0]["vo"] for run in runs] == ["First reviewed hook", "Second reviewed hook"]
    assert [run["photos"] for run in runs] == [["1.jpg"], ["2.jpg"]]


@pytest.mark.parametrize("data, message", [
    ({"lang": "en"}, "Generate and review"),
    (dict(reviewed(), seg_photo_en="../brand.yaml"), "available picture"),
    (dict(reviewed(), aspect="evil"), "valid output shape"),
    (dict(reviewed(), lang="invalid"), "selected language"),
    (dict(reviewed(), seg_vo_en="  "), "Generate and review"),
    (dict(reviewed(), build_versions="en:garbage"), "valid script version"),
])
def test_invalid_job_never_enters_queue(client, queue, data, message):
    response = client.post("/products/test-rack/queue", data=data)
    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    assert not queue.jobs()


def test_presets_are_validated_portable_and_omit_credentials(client, queue):
    payload = {"schema": 1, "name": "Vertical sale", "settings": {
        "tts": "silent", "preset": "ultrafast", "template": "", "voice_rate": "",
        "voice_delivery": "", "no_music": True, "lang": ["en"], "aspect": ["9:16"]}}
    response = client.post("/renders/presets", json=payload)
    assert response.status_code == 201
    id = response.json["id"]
    assert client.get("/renders/presets/" + id).json == payload
    assert client.get("/renders/presets").json["presets"][0]["name"] == payload["name"]
    payload["settings"]["api_key"] = "must-never-save"
    assert client.post("/renders/presets", json=payload).status_code == 400
    assert "must-never-save" not in json.dumps(queue.store.list("preset"))
    assert client.post(f"/renders/presets/{id}/delete").status_code == 200
    assert client.get("/renders/presets/" + id).status_code == 404


@pytest.mark.parametrize("key, value", [("tts", []), ("no_music", "false"), ("lang", "en"),
                                       ("aspect", ["16:10"]), ("voice_delivery", "a" * 1501)])
def test_malformed_import_rejected(client, key, value):
    payload = {"schema": 1, "name": "Bad", "settings": {
        "tts": "silent", "preset": "ultrafast", "template": "", "voice_rate": "",
        "voice_delivery": "", "no_music": True, "lang": ["en"], "aspect": ["9:16"]}}
    payload["settings"][key] = value
    assert client.post("/renders/presets", json=payload).status_code == 400


def test_render_page_download_and_growth_handoff(client, queue, project, monkeypatch):
    job = enqueue(queue, project)
    stub_renderer(monkeypatch, [])
    queue.drain()
    response = client.get("/renders")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Schedule this reel" in html and "Download recipe" in html
    recipe = client.get(f"/renders/{job['id']}/recipe")
    assert recipe.status_code == 200
    assert "local_base_url" not in recipe.get_data(as_text=True)
    assert "attachment" in recipe.headers["Content-Disposition"]
    assert client.get("/renders/status").json["jobs"][0]["status"] == "completed"

