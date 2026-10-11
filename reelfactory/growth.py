"""Durable research briefs, publishing state, and observed performance."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .social import SocialError, metric_snapshot, safe_url


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def schedule_time(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("Enter a valid date and time with a UTC offset.") from None
    if date.tzinfo is None:
        raise ValueError("Scheduled times must include a UTC offset.")
    date = date.astimezone(timezone.utc)
    if date <= datetime.now(timezone.utc) + timedelta(minutes=2):
        raise ValueError("Schedule at least two minutes in the future.")
    return date.isoformat(timespec="seconds")


def output_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if (not path.is_relative_to(root) or path == root or len(path.relative_to(root).parts) != 2 or path.suffix.lower() != ".mp4"
            or not path.is_file() or not path.stat().st_size
            or any(p.startswith(".") for p in path.relative_to(root).parts)):
        raise ValueError("Choose a finished MP4 from the output library.")
    return path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Store:
    def __init__(self, root):
        self.path = Path(root) / "growth" / "workspace.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, kind, data):
        data = dict(data, id=uuid.uuid4().hex, created_at=now(), updated_at=now(), revision=1)
        with self.connection() as db:
            db.execute("INSERT INTO records VALUES (?, ?, ?)", (data["id"], kind, json.dumps(data)))
        return data

    def get(self, id, kind=None):
        with self.connection() as db:
            row = db.execute("SELECT kind, data FROM records WHERE id=?", (id,)).fetchone()
        if not row or (kind is not None and row[0] != kind):
            raise ValueError("This item no longer exists.")
        return json.loads(row[1])

    def list(self, kind):
        with self.connection() as db:
            rows = db.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid DESC", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update(self, id, changes, *, states=None, expected_revision=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM records WHERE id=?", (id,)).fetchone()
            if not row:
                raise ValueError("This item no longer exists.")
            data = json.loads(row[0])
            if ((states is not None and data.get("status") not in states)
                    or (expected_revision is not None and data.get("revision", 1) != expected_revision)):
                raise ValueError("This post has changed or is already being processed. Refresh the page.")
            data.update(changes, updated_at=now(), revision=data.get("revision", 1) + 1)
            db.execute("UPDATE records SET data=? WHERE id=?", (json.dumps(data), id))
        return data


def create_draft(store, out_root, values, accounts):
    account = next((x for x in accounts if x["id"] == values.get("account_id")), None)
    if not account:
        raise ValueError("Refresh accounts and choose a connected Instagram, Facebook or YouTube account.")
    video = output_path(out_root, values.get("video", ""))
    title, caption = values.get("title", "").strip(), values.get("caption", "").strip()
    if not 2 <= len(title) <= 100:
        raise ValueError("Enter a title between 2 and 100 characters.")
    limit = 2200 if account["platform"].startswith("instagram") else 5000 if account["platform"] == "youtube" else 63206
    if not caption or len(caption) > limit:
        raise ValueError(f"Enter a caption of 1 to {limit} characters for this channel.")
    visibility, kids = values.get("visibility", "private"), values.get("made_for_kids", "no")
    if visibility not in {"public", "private", "unlisted"} or kids not in {"yes", "no"}:
        raise ValueError("Choose valid YouTube visibility and audience settings.")
    brief_id = values.get("brief_id", "")
    if brief_id:
        store.get(brief_id, "brief")
    data = {"status": "draft", "account_id": account["id"],
        "account_name": account["name"], "platform": account["platform"], "title": title,
        "caption": caption, "video": video.relative_to(Path(out_root).resolve()).as_posix(),
        "sha256": digest(video), "scheduled_at": schedule_time(values.get("scheduled_at", "")),
        "display_offset": values.get("display_offset", "+05:30"),
        "scheduled_local": values.get("scheduled_at", ""),
        "visibility": visibility, "made_for_kids": kids, "brief_id": brief_id,
        "remote_id": "", "error": "", "metrics": []}
    if values.get("draft_id"):
        store.get(values["draft_id"], "post")
        return store.update(values["draft_id"], data, states={"draft", "failed"})
    return store.add("post", data)


def submit(store, id, out_root, client):
    draft = store.get(id, "post")
    # Preflight before claiming so configuration errors leave an editable draft.
    schedule_time(draft["scheduled_at"])
    video = output_path(out_root, draft["video"])
    if digest(video) != draft["sha256"]:
        raise ValueError("The video changed since review. Create a fresh draft for the new file.")
    if not any(x["id"] == draft["account_id"] and x["platform"] == draft["platform"] for x in client.accounts()):
        raise ValueError("The selected account is disconnected. Reconnect it in Postiz.")
    draft = store.update(id, {"status": "uploading", "error": "", "submitted_at": now(),
                              "provider_url": client.base}, states={"draft", "failed"},
                         expected_revision=draft.get("revision", 1))
    try:
        media = client.upload(video)
    except (SocialError, OSError):
        store.update(id, {"status": "failed", "error": "Video upload failed; no post was submitted. You can retry."}, states={"uploading"})
        raise ValueError("Video upload failed; no post was submitted. You can retry.") from None
    # Persist before the non-idempotent remote operation. A crash or timeout now
    # requires reconciliation, never an automatic second POST.
    store.update(id, {"status": "submitting", "media": media}, states={"uploading"})
    try:
        remote_id = client.schedule(draft, media)
    except SocialError as exc:
        store.update(id, {"status": "uncertain" if exc.uncertain else "failed", "error": str(exc)}, states={"submitting"})
        raise
    return store.update(id, {"status": "scheduled", "remote_id": remote_id, "error": ""}, states={"submitting"})


def remote_post(client, draft, remote_id):
    if draft.get("provider_url") and draft["provider_url"] != client.base:
        raise ValueError("Restore the Postiz server URL used for this post before syncing it.")
    date = datetime.fromisoformat(draft["scheduled_at"])
    rows = client.posts((date - timedelta(days=2)).isoformat(), (date + timedelta(days=2)).isoformat())
    remote = next((x for x in rows if str(x.get("id")) == remote_id), None)
    if not remote:
        raise ValueError("Post not found near the saved schedule. Check its ID and date in Postiz; no new post was sent.")
    if str((remote.get("integration") or {}).get("id")) != draft["account_id"]:
        raise ValueError("This remote post belongs to a different account.")
    return remote


def sync(store, id, client, remote_id=None):
    draft = store.get(id, "post")
    remote_id = (remote_id or draft.get("remote_id", "")).strip()
    if not remote_id or len(remote_id) > 200:
        raise ValueError("Enter the post ID from your Postiz calendar to reconcile this post.")
    if draft["status"] == "uploading":
        raise ValueError("Upload is in progress. If the app restarted, discard this local draft and create a new one; no schedule request was started.")
    remote = remote_post(client, draft, remote_id)
    state = {"QUEUE": "scheduled", "PUBLISHED": "published", "ERROR": "publish_failed", "DRAFT": "remote_draft"}.get(remote.get("state"), "remote_unknown")
    # Record known delivery state even if the separate analytics call fails.
    result = store.update(id, {"remote_id": remote_id, "status": state,
        "release_url": safe_url(str(remote.get("releaseURL") or "")), "last_synced": now(), "error": ""},
        states={"scheduled", "published", "publish_failed", "remote_draft", "remote_unknown", "uncertain", "submitting", "cancel_uncertain", "cancelling"})
    if state == "published":
        try:
            raw = client.analytics(remote_id)
            captured = now()
            metrics = metric_snapshot(raw)
            store.add("snapshot", {"post_id": id, "captured_at": captured, "metrics": metrics, "raw": raw})
            result = store.update(id, {"metrics": metrics, "metrics_at": captured, "analytics_error": ""}, states={"published"})
        except SocialError as exc:
            result = store.update(id, {"analytics_error": str(exc)}, states={"published"})
    return result


def cancel(store, id, client):
    draft = store.get(id, "post")
    remote = remote_post(client, draft, draft.get("remote_id", ""))
    if remote.get("state") != "QUEUE":
        raise ValueError("Only a queued post can be cancelled here. Refresh its status.")
    store.update(id, {"status": "cancelling"}, states={"scheduled"})
    try:
        client.cancel(draft["remote_id"])
    except SocialError as exc:
        store.update(id, {"status": "cancel_uncertain" if exc.uncertain else "scheduled", "error": str(exc)}, states={"cancelling"})
        raise
    store.update(id, {"status": "cancelled", "error": ""}, states={"cancelling"})


def insights(posts):
    """Descriptive comparisons within a channel; never infer optimal times."""
    groups = {}
    for post in posts:
        if post["status"] != "published":
            continue
        views = next((m["value"] for m in post.get("metrics", []) if m["label"].lower() == "views"), None)
        if views is not None:
            groups.setdefault(post["account_id"], []).append((views, post))
    result = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda row: row[0], reverse=True)
        views, winner = rows[0]
        result.append({"title": winner["title"], "views": views, "count": len(rows),
            "account": winner["account_name"], "post_id": winner["id"],
            "brief": f'Test a new opening inspired by "{winner["title"]}" on {winner["account_name"]}. '
                     f'It has the highest latest reported views ({views:g}) among {len(rows)} tracked reels on this account. '
                     'Compare at the same age after publishing before deciding which approach works. '
                     'Keep product facts accurate; change one creative element at a time.'})
    return result
