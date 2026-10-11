"""Local background rendering with durable, reviewed input snapshots.

One worker per workspace renders serially. Workers run in the web server;
waiting jobs survive a restart, but active jobs are never retried implicitly.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
import uuid

from . import cli, telemetry, templates
from .config import Brand, Product
from .growth import Store, digest, now
from .script import LANGS, Segment
from .storage import atomic_text, file_lock

SETTINGS = ("tts", "preset", "template", "voice_rate", "voice_delivery", "no_music")


def render_settings(args):
    """Only rendering controls; never credentials or provider endpoints."""
    return {key: getattr(args, key) for key in SETTINGS}


@contextmanager
def worker_lock(root):
    """Non-blocking OS lock, released on process death on Windows and POSIX."""
    path = Path(root) / "worker.lock"
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            yield False
            return
        try:
            yield True
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class RenderQueue:
    def __init__(self, workspace, out_root, logger=None):
        self.store = Store(workspace, folder="render_jobs")
        self.root = self.store.path.parent
        self.out_root = Path(out_root).resolve()
        self._guard = threading.Lock()
        self._thread = None
        self.logger = logger

    def jobs(self):
        return self.store.list("render")

    def _check_capacity(self):
        if sum(j["status"] in {"queued", "running"} for j in self.jobs()) >= 25:
            raise ValueError("The queue holds up to 25 waiting or active jobs. Finish or cancel a job first.")

    def enqueue(self, product, brand, args, aspects, runs):
        if not aspects or any(a not in cli.ASPECTS for a in aspects):
            raise ValueError("Choose a valid output shape.")
        if not runs or len(runs) > 12:
            raise ValueError("Queue between one and twelve reviewed scripts at a time.")
        available = {p.name: p for p in product.photos}
        for run in runs:
            if run["lang"] not in LANGS or not run["segments"]:
                raise ValueError("Generate and review a script for each selected language before queuing.")
            if (len(run["photos"]) != len(run["segments"])
                    or any(p not in available for p in run["photos"])):
                raise ValueError("Choose an available picture or clip for every scene before queuing.")
            if product.collection_members:
                from .collections import render_photos
                render_photos(product, [Segment(**s) for s in run["segments"]], run["photos"])
        # Capture the template values too, so editing a look later cannot
        # silently change a reviewed job. Built-in defaults work identically.
        look = templates.load(args.template or product.resolve_template(brand))
        settings = render_settings(args)
        snapshot_id = uuid.uuid4().hex
        snapshots = self.root / "snapshots"
        snapshots.mkdir(exist_ok=True)
        with file_lock(self.root / "enqueue"):
            self._check_capacity()
            with tempfile.TemporaryDirectory(prefix=".preparing-", dir=snapshots) as temporary:
                stage = Path(temporary)
                media_dir = stage / "photos"
                media_dir.mkdir()
                names = list(available)
                for name in names:
                    shutil.copyfile(available[name], media_dir / name)
                product_data = asdict(product)
                product_data.update(dir=".", photos=names)
                brand_data = asdict(brand)
                # Script generation is already complete; no server URL belongs
                # in a saved recipe (it could contain embedded credentials).
                brand_data.pop("local_base_url", None)
                for key in ("logo", "music", "font_hi", "font_en"):
                    value = brand_data.get(key)
                    if value and Path(value).is_file():
                        target = key + Path(value).suffix
                        shutil.copyfile(value, stage / target)
                        brand_data[key] = {"asset": target}
                recipe = {"schema": 1, "product": product_data, "brand": brand_data,
                          "settings": settings, "look": asdict(look),
                          "aspects": list(dict.fromkeys(aspects)), "runs": runs}
                recipe["assets"] = {p.relative_to(stage).as_posix(): digest(p)
                                    for p in stage.rglob("*") if p.is_file()}
                atomic_text(stage / "recipe.json", json.dumps(recipe, ensure_ascii=False, indent=2))
                stage.rename(snapshots / snapshot_id)
            return self.store.add("render", {"snapshot": snapshot_id, "slug": product.slug,
                "name": product.name_en, "status": "queued", "phase": "Waiting",
                "completed": 0, "total": len(runs) * len(recipe["aspects"]), "outputs": [],
                "attempt": 0, "error": "", "history": [], "elapsed_seconds": 0})

    def recipe(self, job):
        return json.loads((self.root / "snapshots" / job["snapshot"] / "recipe.json").read_text(encoding="utf-8"))

    def cancel(self, id):
        self.store.get(id, "render")
        return self.store.update(id, {"status": "cancelled", "phase": "Cancelled"}, states={"queued"})

    def retry(self, id):
        self.store.get(id, "render")
        # New filenames on retry; preserve prior output links in history.
        with file_lock(self.root / "enqueue"):
            self._check_capacity()
            return self.store.update(id, {"status": "queued", "phase": "Waiting", "error": ""},
                                     states={"failed", "interrupted", "cancelled"})

    def repeat(self, id):
        job = self.store.get(id, "render")
        if job["status"] != "completed":
            raise ValueError("Only finished jobs can be rendered again. Retry failed or interrupted jobs instead.")
        with file_lock(self.root / "enqueue"):
            self._check_capacity()
            return self.store.add("render", {"snapshot": job["snapshot"], "slug": job["slug"],
                "name": job["name"], "status": "queued", "phase": "Waiting", "completed": 0,
                "total": job["total"], "outputs": [], "attempt": 0, "error": "", "history": [],
                "elapsed_seconds": 0, "source_job_id": id})

    def start(self):
        with self._guard:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="reel-render-queue", daemon=True)
                self._thread.start()

    def _work(self):
        try:
            while True:
                self.drain()
                # Synchronize thread exit with enqueue/start to avoid a lost
                # wakeup when a job arrives just as this worker goes idle.
                with self._guard:
                    if not any(j["status"] == "queued" for j in self.jobs()):
                        self._thread = None
                        return
                time.sleep(1)
        finally:
            with self._guard:
                if self._thread is threading.current_thread():
                    self._thread = None

    def drain(self):
        """Process pending jobs; also usable by tests without a background thread."""
        with worker_lock(self.root) as acquired:
            if not acquired:
                return
            # Only the holder can know there is no live renderer for these.
            for job in self.jobs():
                if job["status"] == "running":
                    self.store.update(job["id"], {"status": "interrupted", "phase": "Interrupted",
                        "error": "The server stopped before this job finished. Review any outputs before retrying."},
                        states={"running"})
            while True:
                pending = [j for j in reversed(self.jobs()) if j["status"] == "queued"]
                if not pending:
                    return
                job = pending[0]
                try:
                    job = self.store.update(job["id"], {"status": "running", "phase": "Preparing",
                        "attempt": job["attempt"] + 1, "completed": 0, "elapsed_seconds": 0,
                        "error": "", "started_at": now()}, states={"queued"})
                except ValueError:
                    continue  # A queued cancellation won the transaction.
                self._render(job)

    def _render(self, job):
        started = time.monotonic()
        outputs = list(job["outputs"])
        completed = 0
        history = list(job["history"])

        def progress(label, details):
            nonlocal completed
            phase = label + (" · " + details["state"] if details.get("state") else "")
            # Persist labels and counts, not provider payloads or credentials.
            if label == "Video saved":
                completed += 1
                remember(self.out_root / job["slug"] / details["filename"])
            self.store.update(job["id"], {"phase": phase, "completed": completed,
                "outputs": outputs, "elapsed_seconds": round(time.monotonic() - started)}, states={"running"})

        def remember(path):
            path = Path(path)
            relative = path.resolve().relative_to(self.out_root).as_posix()
            if relative not in outputs:
                outputs.append(relative)
            if path.suffix.lower() == ".mp4":
                atomic_text(path.with_suffix(".recipe.json"), json.dumps(recipe, ensure_ascii=False, indent=2))

        token = telemetry.sink.set(progress)
        try:
            recipe = self.recipe(job)
            base = self.root / "snapshots" / job["snapshot"]
            if any(not (base / name).is_file() or digest(base / name) != checksum
                   for name, checksum in recipe["assets"].items()):
                raise ValueError("Snapshot media missing or changed")
            data = dict(recipe["product"], dir=base, photos=[base / "photos" / n for n in recipe["product"]["photos"]])
            brand_data = {k: str(base / v["asset"]) if isinstance(v, dict) and "asset" in v else v
                          for k, v in recipe["brand"].items()}
            args = SimpleNamespace(**recipe["settings"], script="template", steer="", keep_temp=False,
                                   gemini_key=None, gemini_backup_key=None, local_key=None,
                                   render_template=templates.Template(**recipe["look"]))
            for run in recipe["runs"]:
                written = cli.build_one(Product(**data), Brand(**brand_data), run["lang"], recipe["aspects"],
                    self.out_root, args, segments=[Segment(**s) for s in run["segments"]],
                    photo_names=run["photos"], variant_tag=run.get("variant_tag", ""))
                for path in written:
                    remember(path)
                self.store.update(job["id"], {"outputs": outputs}, states={"running"})
            state, phase, error = "completed", "Finished", ""
            completed = job["total"]
        except Exception as exc:
            if self.logger:
                self.logger.exception("Background render failed job=%s", job["id"])
            state, phase = "failed", "Failed"
            error = f"Render failed ({type(exc).__name__}). Check Debug, voice/API settings, disk space and snapshot media, then retry."
        finally:
            telemetry.sink.reset(token)
        elapsed = round(time.monotonic() - started)
        history.append({"attempt": job["attempt"], "status": state, "finished_at": now(), "seconds": elapsed})
        self.store.update(job["id"], {"status": state, "phase": phase, "error": error,
            "completed": completed, "outputs": outputs, "elapsed_seconds": elapsed, "history": history[-20:]}, states={"running"})
