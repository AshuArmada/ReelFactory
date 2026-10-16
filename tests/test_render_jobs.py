"""Durability, isolation and recovery for queued renders."""
from dataclasses import asdict
import json
import threading

import pytest

from conftest import form
from reelfactory import cli, telemetry
from reelfactory.config import Brand, Product
from reelfactory.render_jobs import RenderQueue, worker_lock
from reelfactory.script import Segment
from reelfactory.web.app import _render_args


@pytest.fixture
def queue(project, monkeypatch):
    value = RenderQueue(project, project / "out")
    monkeypatch.setattr(value, "start", lambda: None)
    return value


def reviewed(**extra):
    return form(lang="en", aspect="9:16", tts="silent", preset="ultrafast",
                seg_vo_en="Your exact reviewed line.", seg_role_en="hook",
                seg_overlay_en="Reviewed overlay", seg_photo_en="1.jpg", **extra)


def enqueue(queue, project, *, aspects=None):
    product = Product.load(project / "products/test-rack")
    brand = Brand.load(project / "brand.yaml")
    args = _render_args(reviewed(), brand)
    return queue.enqueue(product, brand, args, aspects or ["9:16"], [{"lang": "en",
        "segments": [asdict(Segment("hook", "Reviewed words", "Original overlay"))], "photos": ["1.jpg"]}])


def stub_renderer(monkeypatch, calls):
    def build(product, brand, lang, aspects, outroot, args, **kwargs):
        calls.append((product, brand, args, kwargs))
        outdir = outroot / product.slug
        outdir.mkdir(exist_ok=True)
        paths = []
        for aspect in aspects:
            path = cli._free_path(outdir, lang + aspect.replace(":", "x"), ".mp4")
            path.write_bytes(b"video")
            telemetry.event("Video saved", filename=path.name, aspect=aspect)
            paths.append(path)
        return paths
    monkeypatch.setattr(cli, "build_one", build)


def test_snapshot_survives_product_brand_template_changes(queue, project, monkeypatch):
    job = enqueue(queue, project)
    original = (project / "products/test-rack/photos/1.jpg").read_bytes()
    (project / "products/test-rack/photos/1.jpg").write_bytes(b"changed")
    (project / "brand.yaml").write_text("name: Changed", encoding="utf-8")
    calls = []
    stub_renderer(monkeypatch, calls)
    monkeypatch.setattr(cli.templates, "load", lambda *a, **k: pytest.fail("Must use saved look"))
    queue.drain()
    prod, brand, args, kwargs = calls[0]
    assert prod.photos[0].read_bytes() == original
    assert brand.name == "Test Steel Works"
    assert kwargs["segments"][0].vo == "Reviewed words"
    assert args.render_template.name
    done = queue.store.get(job["id"])
    assert done["status"] == "completed" and done["completed"] == 1
    sidecar = (project / "out" / done["outputs"][0]).with_suffix(".recipe.json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["runs"] == queue.recipe(done)["runs"]


def test_missing_snapshot_fails_before_voice_or_render(queue, project, monkeypatch):
    job = enqueue(queue, project)
    (queue.root / "snapshots" / job["snapshot"] / "photos/1.jpg").write_bytes(b"corrupted")
    monkeypatch.setattr(cli, "build_one", lambda *a, **k: pytest.fail("Changed assets must not render"))
    queue.drain()
    assert queue.store.get(job["id"])["status"] == "failed"


def test_repeat_finished_job_keeps_inputs_and_makes_new_outputs(queue, project, monkeypatch):
    job = enqueue(queue, project)
    with pytest.raises(ValueError):
        queue.repeat(job["id"])
    stub_renderer(monkeypatch, [])
    queue.drain()
    original = queue.store.get(job["id"])
    repeated = queue.repeat(job["id"])
    assert repeated["id"] != original["id"]
    assert repeated["snapshot"] == original["snapshot"]
    queue.drain()
    assert queue.store.get(repeated["id"])["outputs"] != original["outputs"]
    assert queue.store.get(original["id"]) == original


def test_partial_outputs_survive_failure_and_retry_is_explicit(queue, project, monkeypatch):
    job = enqueue(queue, project, aspects=["9:16", "1:1"])
    def partial(product, brand, lang, aspects, outroot, args, **kwargs):
        outdir = outroot / product.slug
        outdir.mkdir(exist_ok=True)
        path = outdir / "first.mp4"
        path.write_bytes(b"video")
        telemetry.event("Video saved", filename=path.name, aspect="9:16")
        raise RuntimeError("provider secret-do-not-show")
    monkeypatch.setattr(cli, "build_one", partial)
    queue.drain()
    failed = queue.store.get(job["id"])
    assert failed["status"] == "failed" and failed["completed"] == 1
    assert failed["outputs"] == ["test-rack/first.mp4"]
    assert "secret-do-not-show" not in failed["error"]
    queue.drain()
    assert queue.store.get(job["id"])["attempt"] == 1
    calls = []
    stub_renderer(monkeypatch, calls)
    queue.retry(job["id"])
    queue.drain()
    done = queue.store.get(job["id"])
    assert done["status"] == "completed" and done["attempt"] == 2
    assert len(done["outputs"]) == 3 and len(done["history"]) == 2


def test_waiting_cancel_does_not_render_and_running_stop_keeps_job_active(queue, project, monkeypatch):
    job = enqueue(queue, project)
    queue.cancel(job["id"])
    monkeypatch.setattr(cli, "build_one", lambda *a, **k: pytest.fail("Cancelled job rendered"))
    queue.drain()
    assert queue.store.get(job["id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        queue.cancel(job["id"])

    running = enqueue(queue, project)
    queue.store.update(running["id"], {"status": "running"})
    stopped = queue.cancel(running["id"])
    assert stopped["status"] == "running"
    assert stopped["cancel_requested"] is True
    assert stopped["outputs"] == []
    with pytest.raises(ValueError, match="already requested"):
        queue.cancel(running["id"])


def test_interrupted_job_requires_retry_but_waiting_jobs_continue(queue, project, monkeypatch):
    first = enqueue(queue, project)
    second = enqueue(queue, project)
    queue.store.update(first["id"], {"status": "running", "attempt": 1})
    calls = []
    stub_renderer(monkeypatch, calls)
    restarted = RenderQueue(project, project / "out")
    restarted.drain()
    assert restarted.store.get(first["id"])["status"] == "interrupted"
    assert restarted.store.get(second["id"])["status"] == "completed"
    assert len(calls) == 1


def test_two_workers_cannot_render_or_recover_each_others_jobs(queue, project, monkeypatch):
    first = enqueue(queue, project)
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return []
    monkeypatch.setattr(cli, "build_one", blocked)
    worker = threading.Thread(target=queue.drain)
    worker.start()
    try:
        assert entered.wait(10)
        other = RenderQueue(project, project / "out")
        with worker_lock(other.root) as acquired:
            assert not acquired
        other.drain()
        assert other.store.get(first["id"])["status"] == "running"
    finally:
        release.set()
        worker.join(10)
    assert queue.store.get(first["id"])["attempt"] == 1


def test_worker_picks_up_new_job_while_rendering(project, monkeypatch):
    queue = RenderQueue(project, project / "out")
    first = enqueue(queue, project)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def render(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        return []
    monkeypatch.setattr(cli, "build_one", render)
    queue.start()
    worker = queue._thread
    try:
        assert entered.wait(10)
        second = enqueue(queue, project)
        queue.start()
        assert queue._thread is worker
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert len(calls) == 2
    assert all(queue.store.get(j["id"])["status"] == "completed" for j in [first, second])


def test_brand_assets_are_copied_and_provider_credentials_are_not_saved(queue, project):
    logo = project / "logo.png"
    logo.write_bytes(b"logo-before")
    brand = Brand(name="Test", logo=str(logo), local_base_url="http://user:private@localhost/v1")
    args = _render_args(reviewed(), brand)
    args.gemini_key = "private-api-key"
    product = Product.load(project / "products/test-rack")
    job = queue.enqueue(product, brand, args, ["9:16"], [{"lang": "en", "photos": ["1.jpg"],
        "segments": [asdict(Segment("hook", "Reviewed", "Reviewed"))]}])
    logo.write_bytes(b"logo-after")
    recipe = queue.recipe(job)
    copied = queue.root / "snapshots" / job["snapshot"] / recipe["brand"]["logo"]["asset"]
    assert copied.read_bytes() == b"logo-before"
    assert "private" not in json.dumps(recipe)


@pytest.mark.slow
def test_real_background_render_writes_playable_video_and_recipe(queue, project):
    job = enqueue(queue, project)
    queue.drain()
    done = queue.store.get(job["id"])
    assert done["status"] == "completed", done["error"]
    video = project / "out" / next(p for p in done["outputs"] if p.endswith(".mp4"))
    assert video.stat().st_size > 1000
    from reelfactory.render import validate_video
    validate_video(video)
    recipe = json.loads(video.with_suffix(".recipe.json").read_text(encoding="utf-8"))
    assert recipe["runs"][0]["segments"][0]["vo"] == "Reviewed words"
