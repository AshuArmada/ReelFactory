"""Browser integration for research, publishing review, and analytics."""
from unittest.mock import Mock

import pytest
import requests
from flask.testing import FlaskClient

from reelfactory import growth
from reelfactory.web import growth as web
from test_growth import FakePostiz


@pytest.fixture
def growth_client(client, project, monkeypatch):
    client.application.config["API_ENV_PATH"] = project / ".env"
    fake = FakePostiz()
    monkeypatch.setattr(web, "Postiz", lambda *_: fake)
    monkeypatch.setattr(requests, "request", Mock(side_effect=AssertionError("Unexpected network request")))
    return client, fake


def test_growth_get_performs_no_network_and_shows_setup(growth_client):
    client, _ = growth_client
    response = client.get("/growth")
    assert response.status_code == 200
    assert b"Make the next reel better" in response.data
    assert response.headers["Cache-Control"] == "no-store"
    assert b"No confirmed published posts" in response.data


def test_brief_reaches_existing_writer_with_sources(growth_client, project, monkeypatch):
    client, _ = growth_client
    db = growth.Store(project)
    source = {"title": "A source", "url": "https://source.test", "published": "2026-10-11",
              "publisher": "Publisher", "summary": "Research context"}
    search = db.add("research", {"query": "storage", "source": "news", "days": 7, "searched_at": growth.now(), "results": [source]})
    response = client.post("/growth/briefs", data={"product": "test-rack", "angle": "Demonstrate a small-space makeover",
                           "research_id": search["id"], "source_index": "0"})
    assert response.status_code == 302
    brief = db.list("brief")[0]
    page = client.get(response.location)
    assert b"Research brief" in page.data and b"https://source.test" in page.data
    captured = []
    from reelfactory import cli
    from reelfactory.script import Segment
    def writer(prod, brand, lang, args):
        captured.append(args.steer)
        return [Segment(role="hook", vo="Look at this rack", overlay="More space")]
    monkeypatch.setattr(cli, "_build_segments", writer)
    response = client.post("/products/test-rack/script", data={"lang": "en", "script": "ai",
        "research_brief_id": brief["id"], "use_research": "on"})
    assert response.status_code == 200
    assert "Research context" in captured[0] and "untrusted reference data" in captured[0]
    assert b'research_brief_id' in response.data


def test_browser_draft_review_submit_sync_and_edit(growth_client, project):
    client, fake = growth_client
    folder = project / "out" / "test-rack"
    folder.mkdir(exist_ok=True)
    (folder / "test-rack_en_9x16.mp4").write_bytes(b"finished-video")
    (folder / "test-rack_en_caption.txt").write_text("Existing caption", encoding="utf-8")
    assert client.post("/growth/accounts").status_code == 302
    page = client.get("/growth?video=test-rack/test-rack_en_9x16.mp4")
    assert b"Existing caption" in page.data
    values = dict(video="test-rack/test-rack_en_9x16.mp4", account_id="ig-1", title="A better rack",
        caption="More room", local_time="2035-01-01T19:00", display_offset="+05:30")
    assert client.post("/growth/posts", data=values).status_code == 302
    db = growth.Store(project)
    post = db.list("post")[0]
    assert fake.schedule_calls == 0 and post["status"] == "draft"
    page = client.get("/growth?edit=" + post["id"])
    assert page.status_code == 200 and b"A better rack" in page.data
    assert b'value="2035-01-01T19:00"' in page.data
    values.update(draft_id=post["id"], caption="Updated caption")
    assert client.post("/growth/posts", data=values).status_code == 302
    assert len(db.list("post")) == 1
    assert client.post(f'/growth/posts/{post["id"]}/submit').status_code == 302
    assert fake.schedule_calls == 1
    fake.state = "PUBLISHED"
    assert client.post(f'/growth/posts/{post["id"]}/sync').status_code == 302
    page = client.get("/growth")
    assert page.status_code == 200 and b"120" in page.data and b"View published post" in page.data
    assert b"Views,120.0" in client.get("/growth/analytics.csv").data
    assert client.get("/growth?edit=" + post["id"]).status_code == 400


def test_growth_changes_require_csrf(app):
    client = FlaskClient(app)
    response = client.post("/growth/research", data={"query": "storage"})
    assert response.status_code == 403


def test_build_without_preview_receives_research_direction(growth_client, project, monkeypatch):
    client, _ = growth_client
    db = growth.Store(project)
    brief = db.add("brief", {"product": "test-rack", "angle": "Start with a crowded room", "sources": []})
    from reelfactory import cli
    build = Mock(return_value=[])
    monkeypatch.setattr(cli, "build_one", build)
    response = client.post("/products/test-rack/build", data={"lang": "en", "script": "ai", "tts": "silent",
        "research_brief_id": brief["id"], "use_research": "on"})
    assert response.status_code == 200
    assert "Start with a crowded room" in build.call_args.args[5].steer


def test_brief_can_be_excluded_and_cannot_cross_product_boundaries(growth_client, project):
    client, _ = growth_client
    row = growth.Store(project).add("brief", {"product": "different", "angle": "Secret brief", "sources": []})
    with client.application.test_request_context():
        assert web.research_instructions("test-rack", {"research_brief_id": row["id"], "use_research": "on"}) == ""
        assert web.research_instructions("different", {"research_brief_id": row["id"]}) == ""


def test_external_markup_is_escaped(growth_client, project):
    client, _ = growth_client
    db = growth.Store(project)
    db.add("research", {"query": "<script>bad()</script>", "source": "news", "days": 7,
        "searched_at": growth.now(), "results": [{"title": "<img src=x onerror=alert(1)>",
        "url": "https://source.test", "publisher": "Publisher", "published": "today", "summary": "<script>bad()</script>"}]})
    page = client.get("/growth")
    assert page.status_code == 200
    assert b"<script>bad()" not in page.data and b"&lt;script&gt;bad()" in page.data


def test_csv_neutralizes_spreadsheet_formulas(growth_client, project):
    client, _ = growth_client
    db = growth.Store(project)
    db.add("post", dict(title="=HYPERLINK(bad)", platform="instagram", account_name="@bad",
        scheduled_at="2035-01-01", status="published", metrics=[{"label": "Views", "value": 1, "date": "today"}]))
    data = client.get("/growth/analytics.csv").get_data(as_text=True)
    assert "'=HYPERLINK(bad)" in data and "'@bad" in data
