"""Brand settings, including the logo/music uploads."""
from __future__ import annotations

import io

import pytest

from reelfactory.config import Brand

from conftest import read_yaml, write_yaml

# Smallest valid 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000105fe02fedccc59e"
    "70000000049454e44ae426082"
)
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64


def save(client, **fields):
    """Post the settings form. music_volume is always sent because the real
    form always sends it."""
    data = {"music_volume": "0.12", "name": "Test Steel Works"}
    data.update(fields)
    return client.post("/brand", data=data, content_type="multipart/form-data")


def test_page_offers_the_file_fields(client):
    html = client.get("/brand").get_data(as_text=True)
    for field in ('name="logo_file"', 'name="music_file"', 'name="font_hi"', 'name="font_en"'):
        assert field in html
    assert 'enctype="multipart/form-data"' in html


def test_upload_logo_stores_a_relative_path(client, project):
    resp = save(client, logo_file=(io.BytesIO(PNG), "My Logo.png"))
    assert resp.status_code == 302
    assert read_yaml(project / "brand.yaml")["logo"] == "logo/My_Logo.png"
    assert (project / "logo" / "My_Logo.png").exists()


def test_uploaded_logo_leaves_the_brand_loadable(client, project):
    save(client, logo_file=(io.BytesIO(PNG), "logo.png"))
    brand = Brand.load(project / "brand.yaml")
    assert brand.logo.endswith("logo.png")


def test_upload_music(client, project):
    save(client, music_file=(io.BytesIO(MP3), "track.mp3"))
    assert read_yaml(project / "brand.yaml")["music"] == "music/track.mp3"
    assert (project / "music" / "track.mp3").exists()


@pytest.mark.parametrize("filename", ["virus.exe", "notes.txt", "archive.zip", "noext"])
def test_unsupported_file_types_are_refused(client, project, filename):
    resp = save(client, logo_file=(io.BytesIO(b"x"), filename))
    assert resp.status_code == 400
    assert "supported format" in resp.get_data(as_text=True)
    assert not read_yaml(project / "brand.yaml").get("logo")


def test_a_refused_upload_keeps_the_rest_of_the_form(client, project):
    # This form is four tabs saved in one go; re-reading disk on failure would
    # silently discard everything else the user had typed.
    resp = save(client, name="Edited Name", city="kanpur",
                logo_file=(io.BytesIO(b"x"), "bad.exe"))
    html = resp.get_data(as_text=True)
    assert 'value="Edited Name"' in html
    assert 'value="kanpur"' in html
    assert read_yaml(project / "brand.yaml")["name"] == "Test Steel Works"   # nothing written


def test_music_wrong_type_is_refused(client):
    resp = save(client, music_file=(io.BytesIO(PNG), "cover.png"))
    assert resp.status_code == 400


def test_removing_an_asset(client, project):
    save(client, logo_file=(io.BytesIO(PNG), "logo.png"))
    save(client, remove_logo="on")
    assert read_yaml(project / "brand.yaml")["logo"] == ""
    assert Brand.load(project / "brand.yaml").logo == ""


def test_remove_wins_over_a_simultaneous_upload(client, project):
    save(client, logo_file=(io.BytesIO(PNG), "first.png"))
    save(client, remove_logo="on", logo_file=(io.BytesIO(PNG), "second.png"))
    assert read_yaml(project / "brand.yaml")["logo"] == ""


def test_saving_without_touching_the_file_keeps_it(client, project):
    save(client, logo_file=(io.BytesIO(PNG), "logo.png"))
    save(client, name="Renamed")
    data = read_yaml(project / "brand.yaml")
    assert data["logo"] == "logo/logo.png"
    assert data["name"] == "Renamed"


def test_asset_is_served_back_for_the_preview(client):
    save(client, logo_file=(io.BytesIO(PNG), "logo.png"))
    resp = client.get("/brand/asset/logo")
    assert resp.status_code == 200
    assert resp.data == PNG


def test_asset_route_refuses_anything_but_the_two_known_keys(client):
    for key in ("font_hi", "name", "..%2fbrand.yaml"):
        assert client.get(f"/brand/asset/{key}").status_code in (404, 308)


def test_asset_route_404s_when_unset_or_missing(client, project):
    assert client.get("/brand/asset/music").status_code == 404

    data = read_yaml(project / "brand.yaml")
    data["logo"] = "logo/gone.png"
    write_yaml(project / "brand.yaml", data)
    assert client.get("/brand/asset/logo").status_code == 404


def test_fonts_and_volume_round_trip(client, project):
    save(client, font_hi="Nirmala UI", font_en="Arial", music_volume="0.45")
    data = read_yaml(project / "brand.yaml")
    assert data["font_hi"] == "Nirmala UI"
    assert data["font_en"] == "Arial"
    assert data["music_volume"] == pytest.approx(0.45)


@pytest.mark.parametrize("raw,expected", [("2", 1.0), ("-1", 0.0), ("abc", 0.12), ("", 0.12)])
def test_music_volume_is_clamped(client, project, raw, expected):
    client.post("/brand", data={"music_volume": raw, "name": "X"},
                content_type="multipart/form-data")
    assert read_yaml(project / "brand.yaml")["music_volume"] == pytest.approx(expected)
