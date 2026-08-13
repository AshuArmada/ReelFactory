"""The stock photo finder in the web UI.

Offline, like every other web test: `stock.requests` is replaced, so these
prove the routes and the form contract rather than that Pexels is up.
"""
from __future__ import annotations

import pytest

from reelfactory import stock

from conftest import form, live_html
from test_stock import FakeApi, FakeResponse, pexels_payload, pixabay_payload


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(stock, "requests", fake)
    monkeypatch.setenv("PEXELS_API_KEY", "pex-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "pix-key")
    fake.reply("api.pexels.com", FakeResponse(pexels_payload(
        (1, 1500, 2600, "Ann Example"), (2, 1500, 2600, "Bo Example"))))
    fake.reply("pixabay.com/api", FakeResponse(pixabay_payload((11, 4000, 6000, "Cy Example"))))
    fake.reply("images.pexels.com", FakeResponse(content=b"jpegbytes"))
    fake.reply("pixabay.com/get", FakeResponse(content=b"jpegbytes"))
    return fake


def results_form(client, query="shelving", **extra):
    """Search, then rebuild the form the results page would post back."""
    html = live_html(client.post(
        "/products/test-rack/photos/stock",
        data={"query": query, "source": "pexels", "sharp": "on"},
    ).get_data(as_text=True))
    import re
    fields = re.findall(r'name="(res_\w+)" value="([^"]*)"', html)
    return html, form(*fields, query=query, **extra)


# --------------------------------------------------------------- the page


def test_the_page_opens_from_the_product(client, api):
    html = client.get("/products/test-rack/edit").get_data(as_text=True)
    assert "/products/test-rack/photos/stock" in html

    page = client.get("/products/test-rack/photos/stock").get_data(as_text=True)
    assert 'name="query"' in page
    assert "no credit required" in page


def test_a_missing_product_is_404(client, api):
    assert client.get("/products/nope/photos/stock").status_code == 404
    assert client.post("/products/nope/photos/stock", data={"query": "x"}).status_code == 404


def test_without_a_key_the_page_explains_how_to_get_one(client, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.setattr(stock, "_load_dotenv", lambda names: None)
    html = client.get("/products/test-rack/photos/stock").get_data(as_text=True)
    assert "PEXELS_API_KEY" in html
    assert "pixabay.com/api/docs" in html


# ------------------------------------------------------------------ search


def test_a_search_shows_what_it_found(client, api):
    html = live_html(client.post(
        "/products/test-rack/photos/stock",
        data=form(("source", "pexels"), ("source", "pixabay"), query="shelving"),
    ).get_data(as_text=True))
    assert "Ann Example" in html
    assert "1500×2600" in html
    # Pixabay's file is capped at 1280, and that is the size shown -- not the
    # 4000x6000 original its API advertises.
    assert "853×1280" in html and "4000×6000" not in html


def test_the_size_filter_hides_photos_that_would_be_blown_up(client, api):
    api.reply("api.pexels.com", FakeResponse(pexels_payload(
        (1, 1500, 2600, "Big Example"), (2, 400, 700, "Tiny Example"))))

    with_filter = client.post("/products/test-rack/photos/stock",
                              data={"query": "x", "source": "pexels", "sharp": "on"})
    without = client.post("/products/test-rack/photos/stock",
                          data={"query": "x", "source": "pexels"})

    assert "Tiny Example" not in with_filter.get_data(as_text=True)
    assert "Tiny Example" in without.get_data(as_text=True)


def test_a_failed_search_keeps_the_query_on_screen(client, api):
    api.reply("api.pexels.com", FakeResponse(None, 429))
    html = client.post("/products/test-rack/photos/stock",
                       data={"query": "shelving", "source": "pexels"}).get_data(as_text=True)
    assert "rate limiting" in html
    assert 'value="shelving"' in html


def test_a_search_with_no_matches_says_so(client, api):
    api.reply("api.pexels.com", FakeResponse({"photos": []}))
    html = client.post("/products/test-rack/photos/stock",
                       data={"query": "asdfgh", "source": "pexels"}).get_data(as_text=True)
    assert "Nothing matched" in html


# -------------------------------------------------------------------- add


def test_only_the_ticked_photos_are_downloaded(client, api, project):
    _html, data = results_form(client)
    data.add("pick", "pexels-2")
    resp = client.post("/products/test-rack/photos/stock/add", data=data)

    photos = sorted(p.name for p in (project / "products" / "test-rack" / "photos").iterdir())
    # One added, after the three the product already had.
    assert photos == ["1.jpg", "2.jpg", "3.jpg", "4.jpg"]
    assert resp.status_code == 302
    assert "Added+1+photo" in resp.headers["Location"]


def test_adding_never_runs_the_search_again(client, api):
    # A second search can legitimately return a different set, which would
    # mean adding photos nobody looked at.
    _html, data = results_form(client)
    data.add("pick", "pexels-1")
    api.calls.clear()
    client.post("/products/test-rack/photos/stock/add", data=data)

    assert not [c for c in api.calls if "/v1/search" in c["url"] or "api/" == c["url"][-4:]]
    assert [c["url"] for c in api.calls] == ["https://images.pexels.com/photos/1/x.jpg"]


def test_the_exact_photo_that_was_shown_is_the_one_fetched(client, api):
    html, data = results_form(client)
    assert 'value="https://images.pexels.com/photos/2/x.jpg"' in html
    data.add("pick", "pexels-2")
    client.post("/products/test-rack/photos/stock/add", data=data)

    fetched = [c["url"] for c in api.calls if "images.pexels.com" in c["url"]]
    assert fetched == ["https://images.pexels.com/photos/2/x.jpg"]


def test_ticking_nothing_is_a_plain_message_not_a_download(client, api, project):
    _html, data = results_form(client)
    resp = client.post("/products/test-rack/photos/stock/add", data=data)
    assert resp.status_code == 400
    assert "Tick the photos" in resp.get_data(as_text=True)
    assert len(list((project / "products" / "test-rack" / "photos").iterdir())) == 3


def test_a_url_pointing_somewhere_else_is_refused(client, api, project):
    # The URLs come back from a form, so they are input, not fact.
    data = form(
        ("res_key", "pexels-9"), ("res_url", "https://evil.example.com/x.jpg"),
        ("res_source", "pexels"), ("res_credit", "X"), ("res_page", ""),
        ("res_w", "1500"), ("res_h", "2600"),
        ("pick", "pexels-9"), ("query", "shelving"),
    )
    resp = client.post("/products/test-rack/photos/stock/add", data=data)
    assert "could not be fetched" in resp.headers["Location"].replace("+", " ")
    assert len(list((project / "products" / "test-rack" / "photos").iterdir())) == 3
    assert not [c for c in api.calls if "evil" in c["url"]]


def test_add_to_a_missing_product_is_404(client, api):
    assert client.post("/products/nope/photos/stock/add", data={"pick": "x"}).status_code == 404


# --------------------------------------------------------------- provenance


def test_the_product_page_shows_where_a_photo_came_from(client, api, project):
    _html, data = results_form(client)
    data.add("pick", "pexels-1")
    client.post("/products/test-rack/photos/stock/add", data=data)

    assert stock.load_credits(project / "products" / "test-rack")["4.jpg"]["query"] == "shelving"
    html = client.get("/products/test-rack/edit").get_data(as_text=True)
    assert "Ann Example" in html


def test_deleting_a_photo_forgets_its_credit(client, api, project):
    _html, data = results_form(client)
    data.add("pick", "pexels-1")
    client.post("/products/test-rack/photos/stock/add", data=data)

    client.post("/products/test-rack/edit", data=form(
        ("name_en", "Test Rack"), ("name_hi", "टेस्ट रैक"), ("delete_photo", "4.jpg"),
    ))
    assert stock.load_credits(project / "products" / "test-rack") == {}


# -------------------------------------------------------------- dashboard


def test_the_dashboard_reports_whether_a_key_is_set(client, api):
    assert "Stock photos" in client.get("/").get_data(as_text=True)
