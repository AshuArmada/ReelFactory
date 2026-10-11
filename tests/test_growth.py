"""Publishing state, API contracts, research provenance, and browser flow.

All outbound services are fakes. These tests cannot publish to real accounts.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

from reelfactory import growth, research, social


ACCOUNTS = [{"id": "ig-1", "name": "Our shop", "platform": "instagram"}]


class FakePostiz:
    base = "https://postiz.test/public/v1"

    def __init__(self):
        self.schedule_calls = 0
        self.state = "QUEUE"

    def accounts(self):
        return ACCOUNTS

    def upload(self, video):
        assert video.read_bytes() == b"finished-video"
        return {"id": "media-1", "path": "https://postiz.test/video.mp4"}

    def schedule(self, draft, media):
        self.schedule_calls += 1
        return "remote-1"

    def posts(self, start, end):
        return [{"id": "remote-1", "state": self.state, "integration": {"id": "ig-1"},
                 "releaseURL": "https://instagram.com/reel/test"}]

    def analytics(self, id):
        return [{"label": "Views", "data": [{"total": "120", "date": "2026-10-11"}]},
                {"label": "Likes", "data": [{"total": "7", "date": "2026-10-11"}]}]

    def cancel(self, id):
        self.state = "DELETED"
        return {}


@pytest.fixture
def workspace(tmp_path):
    out = tmp_path / "out"
    video = out / "rack" / "rack_en_9x16.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"finished-video")
    return growth.Store(tmp_path), out


def draft_values(**changes):
    values = dict(account_id="ig-1", video="rack/rack_en_9x16.mp4", title="A better corner",
                  caption="Free up your space.", visibility="private", made_for_kids="no",
                  scheduled_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="minutes"))
    values.update(changes)
    return values


def make_draft(workspace, **changes):
    db, out = workspace
    return growth.create_draft(db, out, draft_values(**changes), ACCOUNTS)


@pytest.mark.parametrize("value", ["2035-01-01T19:00", "not-a-date", "2020-01-01T19:00+05:30"])
def test_schedule_rejects_ambiguous_or_past_times(value):
    with pytest.raises(ValueError):
        growth.schedule_time(value)


def test_schedule_converts_india_time_to_utc():
    assert growth.schedule_time("2035-01-01T19:00+05:30") == "2035-01-01T13:30:00+00:00"


@pytest.mark.parametrize("path", ["../../secret.mp4", r"..\secret.mp4", "rack/missing.mp4", ".private/video.mp4"])
def test_only_finished_output_files_can_be_selected(workspace, path):
    with pytest.raises(ValueError):
        make_draft(workspace, video=path)


def test_draft_validates_account_caption_and_brief(workspace):
    for changes in ({"account_id": "foreign"}, {"caption": "x" * 2201}, {"brief_id": "missing"},
                    {"visibility": "oops"}, {"made_for_kids": "oops"}):
        with pytest.raises(ValueError):
            make_draft(workspace, **changes)
    assert workspace[0].list("post") == []


def test_submit_then_duplicate_request_cannot_publish_again(workspace):
    db, out = workspace
    post = make_draft(workspace)
    client = FakePostiz()
    growth.submit(db, post["id"], out, client)
    with pytest.raises(ValueError, match="already being processed"):
        growth.submit(db, post["id"], out, client)
    assert client.schedule_calls == 1
    assert db.get(post["id"])["status"] == "scheduled"


def test_concurrent_submissions_claim_only_once(workspace):
    db, out = workspace
    post, client = make_draft(workspace), FakePostiz()
    def attempt(_):
        try:
            growth.submit(db, post["id"], out, client)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1
    assert client.schedule_calls == 1


def test_changed_artifact_blocks_upload(workspace):
    db, out = workspace
    post, client = make_draft(workspace), FakePostiz()
    (out / post["video"]).write_bytes(b"new-video")
    with pytest.raises(ValueError, match="video changed"):
        growth.submit(db, post["id"], out, client)
    assert db.get(post["id"])["status"] == "draft"


def test_edit_during_preflight_requires_fresh_review(workspace):
    db, out = workspace
    post, client = make_draft(workspace), FakePostiz()
    def accounts_after_edit():
        db.update(post["id"], {"caption": "A changed caption"}, states={"draft"})
        return ACCOUNTS
    client.accounts = accounts_after_edit
    client.upload = Mock()
    with pytest.raises(ValueError, match="has changed"):
        growth.submit(db, post["id"], out, client)
    client.upload.assert_not_called()
    assert db.get(post["id"])["status"] == "draft"


def test_timeout_requires_reconciliation_and_persists_after_restart(workspace):
    db, out = workspace
    post, client = make_draft(workspace), FakePostiz()
    client.schedule = Mock(side_effect=social.SocialError("timeout", uncertain=True))
    with pytest.raises(social.SocialError):
        growth.submit(db, post["id"], out, client)
    reopened = growth.Store(db.path.parent.parent)
    assert reopened.get(post["id"])["status"] == "uncertain"
    with pytest.raises(ValueError):
        growth.submit(reopened, post["id"], out, client)
    assert client.schedule.call_count == 1
    result = growth.sync(reopened, post["id"], client, "remote-1")
    assert result["status"] == "scheduled"


def test_upload_failure_is_retryable_without_creating_post(workspace):
    db, out = workspace
    post, client = make_draft(workspace), FakePostiz()
    client.upload = Mock(side_effect=social.SocialError("upload timeout", uncertain=True))
    with pytest.raises(ValueError, match="no post was submitted"):
        growth.submit(db, post["id"], out, client)
    assert client.schedule_calls == 0
    assert db.get(post["id"])["status"] == "failed"


def test_sync_records_delivery_separately_from_analytics(workspace):
    db, out = workspace
    post, client = make_draft(workspace), FakePostiz()
    growth.submit(db, post["id"], out, client)
    client.state = "PUBLISHED"
    row = growth.sync(db, post["id"], client)
    assert row["status"] == "published"
    assert row["metrics"][0]["value"] == 120
    assert len(db.list("snapshot")) == 1
    client.analytics = Mock(side_effect=social.SocialError("rate limited"))
    row = growth.sync(db, post["id"], client)
    assert row["status"] == "published" and row["metrics"][0]["value"] == 120
    assert row["analytics_error"] == "rate limited"


def test_sync_rejects_other_account_and_server(workspace):
    db, out = workspace
    post, client = make_draft(workspace), FakePostiz()
    growth.submit(db, post["id"], out, client)
    client.base = "https://different.test"
    with pytest.raises(ValueError, match="server URL"):
        growth.sync(db, post["id"], client)
    client.base = FakePostiz.base
    client.posts = lambda *_: [{"id": "remote-1", "integration": {"id": "foreign"}}]
    with pytest.raises(ValueError, match="different account"):
        growth.sync(db, post["id"], client)


def test_cancel_cannot_delete_a_published_post(workspace):
    db, out = workspace
    post, client = make_draft(workspace), FakePostiz()
    growth.submit(db, post["id"], out, client)
    client.state = "PUBLISHED"
    client.cancel = Mock()
    with pytest.raises(ValueError, match="Only a queued"):
        growth.cancel(db, post["id"], client)
    client.cancel.assert_not_called()
    client.state = "QUEUE"
    growth.cancel(db, post["id"], client)
    assert db.get(post["id"])["status"] == "cancelled"


def test_draft_edit_cannot_modify_a_submitted_post(workspace):
    db, out = workspace
    post = make_draft(workspace)
    edited = make_draft(workspace, draft_id=post["id"], caption="Updated caption")
    assert edited["id"] == post["id"] and len(db.list("post")) == 1
    growth.submit(db, post["id"], out, FakePostiz())
    with pytest.raises(ValueError):
        make_draft(workspace, draft_id=post["id"], caption="Too late")


def test_metrics_keep_missing_distinct_from_zero_and_do_not_sum():
    raw = [{"label": "Views", "data": [{"total": "100", "date": "2026-10-10"}, {"total": "120", "date": "2026-10-11"}]},
           {"label": "Likes", "data": [{"total": "0", "date": "2026-10-11"}]},
           {"label": "Saves", "data": []}, {"label": "Missing", "data": [{"total": None}]},
           {"label": "Bad", "data": [{"total": "NaN"}]}]
    assert [m["value"] for m in social.metric_snapshot(raw)] == [120, 0]
    assert social.metric_snapshot({"missing": True}) == []


def test_insights_compare_only_observed_posts_on_same_account(workspace):
    p1 = make_draft(workspace)
    p2 = dict(p1, id="second", title="Second reel")
    p1.update(status="published", metrics=[{"label": "Views", "value": 100}])
    p2.update(status="published", metrics=[{"label": "Views", "value": 150}])
    assert growth.insights([p1]) == []
    assert growth.insights([p1, dict(p2, account_id="other")]) == []
    assert growth.insights([p1, p2])[0]["title"] == "Second reel"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", "private-secret")
    monkeypatch.setenv("POSTIZ_BASE_URL", social.DEFAULT_URL)
    return social.Postiz(tmp_path)


def test_postiz_payloads_match_public_api_for_all_three_channels(api, monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = [{"postId": "remote", "integration": "ig-1"}]
    call = Mock(return_value=response)
    monkeypatch.setattr(requests, "request", call)
    for platform in ["instagram", "instagram-standalone", "facebook", "youtube"]:
        data = dict(draft_values(), platform=platform)
        assert api.schedule(data, {"id": "m", "path": "https://test/video.mp4"}) == "remote"
        sent = call.call_args.kwargs
        assert sent["headers"] == {"Authorization": "private-secret"}
        assert sent["allow_redirects"] is False
        payload = sent["json"]
        assert payload["type"] == "schedule"
        assert payload["posts"][0]["settings"]["__type"] == platform
        if platform == "youtube":
            assert payload["posts"][0]["settings"]["type"] == "private"
            assert payload["posts"][0]["settings"]["selfDeclaredMadeForKids"] == "no"


@pytest.mark.parametrize("code,uncertain", [(401, False), (429, False), (500, True), (408, True)])
def test_provider_errors_are_redacted_and_classified(api, monkeypatch, code, uncertain):
    monkeypatch.setattr(requests, "request", Mock(return_value=Mock(status_code=code, text="private-secret")))
    with pytest.raises(social.SocialError) as error:
        api.call("POST", "/posts")
    assert error.value.uncertain == uncertain
    assert "private-secret" not in str(error.value)


def test_connection_timeout_does_not_retry_or_expose_request(api, monkeypatch):
    call = Mock(side_effect=requests.Timeout("private-secret"))
    monkeypatch.setattr(requests, "request", call)
    with pytest.raises(social.SocialError) as error:
        api.call("POST", "/posts", json={})
    assert error.value.uncertain and "private-secret" not in str(error.value)
    assert call.call_count == 1


def test_upload_uses_multipart_and_closes_file(api, monkeypatch, tmp_path):
    path = tmp_path / "reel.mp4"
    path.write_bytes(b"video")
    response = Mock(status_code=200)
    response.json.return_value = {"id": "m", "path": "https://uploads.postiz.com/reel.mp4"}
    call = Mock(return_value=response)
    monkeypatch.setattr(requests, "request", call)
    assert api.upload(path)["id"] == "m"
    name, stream, content_type = call.call_args.kwargs["files"]["file"]
    assert name == "reel.mp4" and content_type == "video/mp4" and stream.closed


def test_account_discovery_filters_disconnected_and_unrequested_channels(api, monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = [
        {"id": "1", "identifier": "instagram", "name": "IG", "disabled": False},
        {"id": "2", "identifier": "youtube", "name": "YT", "disabled": True},
        {"id": "3", "identifier": "linkedin", "name": "LI", "disabled": False}]
    monkeypatch.setattr(requests, "request", Mock(return_value=response))
    assert api.accounts() == [{"id": "1", "platform": "instagram", "name": "IG"}]


def test_malformed_success_is_uncertain(api, monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"unexpected": True}
    monkeypatch.setattr(requests, "request", Mock(return_value=response))
    with pytest.raises(social.SocialError) as error:
        api.schedule(dict(draft_values(), platform="instagram"), {})
    assert error.value.uncertain


@pytest.mark.parametrize("url", ["http://remote.test", "https://user:pass@test", "https://test?key=secret", "javascript:alert(1)"])
def test_rejects_unsafe_provider_urls(tmp_path, monkeypatch, url):
    monkeypatch.setenv("POSTIZ_BASE_URL", url)
    with pytest.raises(ValueError):
        social.Postiz(tmp_path)


def test_news_provenance_deduplication_and_unsafe_links(tmp_path, monkeypatch):
    xml = b'''<rss><channel><item><title>New ideas</title><link>https://news.test/a</link><source>Publisher</source><pubDate>Sun, 11 Oct 2026 00:00:00 GMT</pubDate><description>&lt;b&gt;Useful&lt;/b&gt;</description></item><item><link>https://news.test/a</link></item><item><link>javascript:alert(1)</link></item></channel></rss>'''
    monkeypatch.setattr(requests, "get", Mock(return_value=Mock(content=xml)))
    result = research.search(tmp_path, "furniture ideas")
    assert len(result["results"]) == 1
    source = result["results"][0]
    assert source["publisher"] == "Publisher" and source["summary"] == "Useful"
    assert "2026" in source["published"] and result["searched_at"]


def test_web_search_uses_documented_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "research-secret")
    response = Mock()
    response.json.return_value = {"results": [{"title": "Idea", "url": "https://source.test", "content": "Context"}]}
    call = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", call)
    result = research.search(tmp_path, "furniture ideas", "web", 30)
    assert call.call_args.kwargs["json"]["time_range"] == "month"
    assert call.call_args.kwargs["headers"]["Authorization"] == "Bearer research-secret"
    assert result["results"][0]["published"] == "Date not provided"
