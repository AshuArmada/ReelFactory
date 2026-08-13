"""Stock photo search and download.

Nothing here touches the network. `fake_api` stands in for `requests`, which
also lets a test hand back the awkward shapes the real APIs produce -- a
Pixabay hit whose advertised size is far larger than the file it will
actually serve, a result with no usable URL at all.
"""
from __future__ import annotations

import pytest

from reelfactory import stock
from reelfactory.config import next_photo_index
from reelfactory.render import ASPECTS


# ------------------------------------------------------------------ doubles


class FakeResponse:
    def __init__(self, payload=None, status=200, content=b"", headers=None):
        self._payload = payload
        self.status_code = status
        self.content = content
        self.headers = headers if headers is not None else {"Content-Type": "image/jpeg"}
        self.text = "" if payload is None else str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise stock.requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        yield self.content


class FakeApi:
    """Records every call and replies from a per-host script."""

    def __init__(self):
        self.replies = {}
        self.calls = []
        self.RequestException = stock.requests.RequestException
        self.HTTPError = stock.requests.HTTPError

    def reply(self, needle, response):
        self.replies[needle] = response

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        for needle, response in self.replies.items():
            if needle in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return FakeResponse({}, 200)


def pexels_payload(*photos):
    return {"photos": [
        {"id": i, "width": w, "height": h, "photographer": who,
         "url": f"https://www.pexels.com/photo/{i}/",
         "src": {"original": f"https://images.pexels.com/photos/{i}/x.jpg",
                 "medium": f"https://images.pexels.com/photos/{i}/thumb.jpg"}}
        for i, w, h, who in photos
    ]}


def pixabay_payload(*hits):
    return {"hits": [
        {"id": i, "imageWidth": w, "imageHeight": h, "user": who,
         "pageURL": f"https://pixabay.com/photos/{i}/",
         "largeImageURL": f"https://pixabay.com/get/{i}_1280.jpg",
         "webformatURL": f"https://pixabay.com/get/{i}_640.jpg"}
        for i, w, h, who in hits
    ]}


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(stock, "requests", fake)
    monkeypatch.setenv("PEXELS_API_KEY", "pex-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "pix-key")
    return fake


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    # The .env beside brand.yaml is a real file on the developer's machine;
    # a key in it must not decide whether these tests pass.
    monkeypatch.setattr(stock, "_load_dotenv", lambda names: None)


# -------------------------------------------------------------------- keys


def test_no_key_at_all_explains_how_to_get_one(no_keys):
    with pytest.raises(stock.StockError) as exc:
        stock.search("shelves")
    message = str(exc.value)
    assert "PEXELS_API_KEY" in message and "PIXABAY_API_KEY" in message
    assert "pexels.com/api" in message


def test_one_key_is_enough(api, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY")
    monkeypatch.setattr(stock, "_load_dotenv", lambda names: None)
    api.reply("pixabay.com/api", FakeResponse(pixabay_payload((7, 1600, 2400, "Ann"))))

    assert stock.configured() == ["pixabay"]
    found = stock.search("shelves")
    assert [p.source for p in found] == ["pixabay"]
    assert not any("pexels" in c["url"] for c in api.calls)


def test_keys_never_come_from_brand_yaml(project, no_keys):
    # Belt and braces on the rule the LLM providers follow: a key written into
    # brand.yaml is not a key, it is an unknown setting that fails to load.
    from reelfactory.config import Brand
    (project / "brand.yaml").write_text("name: X\npexels_api_key: sneaky\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Brand.load(project / "brand.yaml")
    assert stock.resolve_key("pexels") is None


# ------------------------------------------------------------------ search


def test_results_from_both_sources_are_interleaved(api):
    api.reply("api.pexels.com", FakeResponse(pexels_payload(
        (1, 2000, 3000, "A"), (2, 2000, 3000, "B"), (3, 2000, 3000, "C"))))
    api.reply("pixabay.com/api", FakeResponse(pixabay_payload(
        (11, 2000, 3000, "D"), (12, 2000, 3000, "E"), (13, 2000, 3000, "F"))))

    found = stock.search("shelves", count=4)
    # Alternating, so the second key is not left unused every single search.
    assert [p.source for p in found] == ["pexels", "pixabay", "pexels", "pixabay"]
    assert len(found) == 4


def test_a_portrait_search_asks_each_api_in_its_own_words(api):
    api.reply("api.pexels.com", FakeResponse(pexels_payload((1, 2000, 3000, "A"))))
    api.reply("pixabay.com/api", FakeResponse(pixabay_payload((11, 2000, 3000, "B"))))
    stock.search("shelves", orientation="portrait")

    by_host = {c["url"]: c["params"] for c in api.calls}
    assert by_host["https://api.pexels.com/v1/search"]["orientation"] == "portrait"
    assert by_host["https://pixabay.com/api/"]["orientation"] == "vertical"


def test_the_key_travels_the_way_each_api_wants_it(api):
    api.reply("api.pexels.com", FakeResponse(pexels_payload((1, 100, 100, "A"))))
    api.reply("pixabay.com/api", FakeResponse(pixabay_payload((11, 100, 100, "B"))))
    stock.search("shelves")

    pexels = next(c for c in api.calls if "pexels" in c["url"])
    pixabay = next(c for c in api.calls if "pixabay" in c["url"])
    assert pexels["headers"]["Authorization"] == "pex-key"
    assert "key" not in pexels["params"]          # never as a query parameter
    assert pixabay["params"]["key"] == "pix-key"


def test_an_empty_query_is_refused_before_any_request(api):
    with pytest.raises(stock.StockError):
        stock.search("   ")
    assert not api.calls


def test_a_rejected_key_says_so_plainly(api):
    api.reply("api.pexels.com", FakeResponse(None, 401))
    with pytest.raises(stock.StockError) as exc:
        stock.search("shelves", sources=["pexels"])
    assert "PEXELS_API_KEY" in str(exc.value)


def test_a_result_with_no_downloadable_url_is_skipped(api):
    payload = pexels_payload((1, 2000, 3000, "A"))
    payload["photos"][0]["src"] = {}
    api.reply("api.pexels.com", FakeResponse(payload))
    assert stock.search("shelves", sources=["pexels"]) == []


# ---------------------------------------------------- sizes as they arrive


def test_pixabay_sizes_are_reported_as_downloaded_not_as_advertised(api):
    # The API describes a 4000x6000 original; the file it serves is capped at
    # 1280 on the long side. Quoting the original would promise a sharpness
    # the downloaded photo does not have.
    api.reply("pixabay.com/api", FakeResponse(pixabay_payload((11, 4000, 6000, "A"))))
    photo = stock.search("shelves", sources=["pixabay"])[0]
    assert (photo.width, photo.height) == (853, 1280)


def test_pexels_reports_the_original_it_downloads(api):
    api.reply("api.pexels.com", FakeResponse(pexels_payload((1, 3000, 4500, "A"))))
    photo = stock.search("shelves", sources=["pexels"])[0]
    assert (photo.width, photo.height) == (3000, 4500)
    assert photo.url.endswith("/x.jpg")


def test_results_are_judged_by_the_renderers_own_rule(api):
    api.reply("api.pexels.com", FakeResponse(pexels_payload(
        (1, 1500, 2600, "big"), (2, 400, 700, "tiny"))))
    found = stock.search("shelves", sources=["pexels"], count=2)
    notes = stock.review(found, ASPECTS["9:16"])

    assert notes["pexels-1"].problems == []
    assert any("blown up" in p for p in notes["pexels-2"].problems)


def test_only_sharp_drops_what_would_have_to_be_enlarged(api):
    api.reply("api.pexels.com", FakeResponse(pexels_payload(
        (1, 1500, 2600, "big"), (2, 400, 700, "tiny"))))
    found = stock.search("shelves", sources=["pexels"], count=2)
    assert [p.key for p in stock.only_sharp(found)] == ["pexels-1"]


# ---------------------------------------------------------------- download


def photo(key="pexels-1", url="https://images.pexels.com/photos/1/x.jpg", **kw):
    kw.setdefault("thumb", url)
    kw.setdefault("source", "pexels")
    kw.setdefault("credit", "Ann Example")
    kw.setdefault("page", "https://www.pexels.com/photo/1/")
    kw.setdefault("width", 1500)
    kw.setdefault("height", 2600)
    return stock.Photo(key=key, url=url, **kw)


def test_downloads_continue_the_existing_numbering(api, tmp_path):
    dest = tmp_path / "photos"
    dest.mkdir()
    (dest / "1.jpg").write_bytes(b"old")
    (dest / "2.jpg").write_bytes(b"old")
    api.reply("images.pexels.com", FakeResponse(content=b"jpegbytes"))

    saved = stock.download([photo(key="pexels-1"), photo(key="pexels-2")], dest)
    assert [p.name for p, _ in saved] == ["3.jpg", "4.jpg"]
    assert (dest / "1.jpg").read_bytes() == b"old"     # nothing overwritten
    assert (dest / "3.jpg").read_bytes() == b"jpegbytes"


def test_one_dead_url_does_not_lose_the_rest(api, tmp_path):
    api.reply("images.pexels.com", FakeResponse(content=b"jpegbytes"))
    api.reply("pixabay.com/get", stock.requests.RequestException("connection reset"))
    problems = []

    saved = stock.download(
        [photo(key="pexels-1"),
         photo(key="pixabay-11", url="https://pixabay.com/get/11_1280.jpg", source="pixabay"),
         photo(key="pexels-2")],
        tmp_path / "photos",
        on_progress=lambda p, path, err: err and problems.append(p.key),
    )
    assert [p.key for _, p in saved] == ["pexels-1", "pexels-2"]
    assert problems == ["pixabay-11"]


@pytest.mark.parametrize("url", [
    "https://evil.example.com/x.jpg",
    "http://images.pexels.com/x.jpg",          # plain http
    "https://images.pexels.com.evil.net/x.jpg",  # suffix, not the real host
    "file:///c:/windows/win.ini",
    "",
])
def test_only_the_two_stock_hosts_are_ever_fetched(api, url):
    # These URLs arrive from a form. A URL in a form is not a reason to make a
    # request to any host in the world.
    with pytest.raises(stock.StockError):
        stock.fetch_bytes(url)
    assert not api.calls


def test_a_non_image_response_is_refused(api, tmp_path):
    api.reply("images.pexels.com",
              FakeResponse(content=b"<html>", headers={"Content-Type": "text/html"}))
    assert stock.download([photo()], tmp_path / "photos") == []
    assert not list((tmp_path / "photos").iterdir())


def test_the_file_extension_follows_the_url(api, tmp_path):
    api.reply("images.pexels.com", FakeResponse(content=b"pngbytes"))
    saved = stock.download(
        [photo(url="https://images.pexels.com/photos/1/x.png")], tmp_path / "photos")
    assert saved[0][0].name == "1.png"


def test_an_odd_url_still_lands_as_a_jpg(api, tmp_path):
    api.reply("images.pexels.com", FakeResponse(content=b"bytes"))
    saved = stock.download(
        [photo(url="https://images.pexels.com/photos/1/download?w=800")], tmp_path / "photos")
    assert saved[0][0].name == "1.jpg"


# ----------------------------------------------------------------- credits


def test_credits_record_where_each_photo_came_from(api, tmp_path):
    api.reply("images.pexels.com", FakeResponse(content=b"jpegbytes"))
    product = tmp_path / "rack"
    saved = stock.download([photo()], product / "photos")
    stock.record_credits(product, saved, "steel shelving")

    entry = stock.load_credits(product)["1.jpg"]
    assert entry["credit"] == "Ann Example"
    assert entry["source"] == "pexels"
    assert entry["query"] == "steel shelving"
    assert entry["page"].startswith("https://www.pexels.com/")


def test_a_second_fetch_adds_to_the_record(api, tmp_path):
    api.reply("images.pexels.com", FakeResponse(content=b"jpegbytes"))
    product = tmp_path / "rack"
    stock.record_credits(product, stock.download([photo()], product / "photos"), "one")
    stock.record_credits(product, stock.download([photo()], product / "photos"), "two")

    kept = stock.load_credits(product)
    assert sorted(kept) == ["1.jpg", "2.jpg"]
    assert kept["2.jpg"]["query"] == "two"


def test_credits_for_a_deleted_photo_are_dropped(api, tmp_path):
    # Numbering reuses filenames after a delete, so a stale entry would
    # eventually be read as the credit for a completely different photo.
    api.reply("images.pexels.com", FakeResponse(content=b"jpegbytes"))
    product = tmp_path / "rack"
    saved = stock.download([photo(key="a"), photo(key="b")], product / "photos")
    stock.record_credits(product, saved, "q")

    stock.forget_credits(product, ["2.jpg"])
    assert sorted(stock.load_credits(product)) == ["1.jpg"]

    stock.forget_credits(product, ["1.jpg"])
    assert stock.load_credits(product) == {}
    assert not stock.credits_path(product).exists()


def test_a_product_that_never_used_stock_photos_has_no_credits(tmp_path):
    assert stock.load_credits(tmp_path) == {}


def test_a_corrupt_credits_file_is_ignored_rather_than_fatal(tmp_path):
    # Same rule as every other hand-editable file here: it must not take down
    # the page you would go to in order to fix it.
    stock.credits_path(tmp_path).write_text("1.jpg: [oops\n", encoding="utf-8")
    assert stock.load_credits(tmp_path) == {}


# -------------------------------------------------------------- numbering


def test_next_photo_index_ignores_non_photos(tmp_path):
    (tmp_path / "1.jpg").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "hero.jpg").write_bytes(b"x")
    assert next_photo_index(tmp_path) == 2


def test_next_photo_index_on_an_empty_or_missing_folder(tmp_path):
    assert next_photo_index(tmp_path / "nope") == 1
    assert next_photo_index(tmp_path) == 1
