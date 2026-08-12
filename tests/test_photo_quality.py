"""Judging whether a photo is good enough for the frame it has to fill.

The arithmetic here is the whole point: "the video looks blurry" almost always
turns out to be a photo being enlarged, and "where did the rest of the picture
go" is the centre crop. Both are knowable from the photo's dimensions before
anything is rendered.
"""
from __future__ import annotations

import pytest

from reelfactory.render import MAX_ZOOM, PhotoNote, photo_advice, photo_notes

REEL = (1080, 1920)


class P:
    """photo_notes only reads .name off the key."""
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)


def note_for(w, h, size=REEL) -> PhotoNote:
    return photo_notes({P("x.jpg"): (w, h)}, size)[0]


# ------------------------------------------------------------------ upscale


def test_a_photo_at_exactly_the_zoom_size_is_clean():
    # MAX_ZOOM times the output is the point where even the tightest Ken
    # Burns push is still a 1:1 crop rather than an enlargement.
    note = note_for(int(1080 * MAX_ZOOM), int(1920 * MAX_ZOOM))
    assert note.upscale == pytest.approx(1.0, abs=0.01)
    assert note.ok


def test_a_photo_the_size_of_the_output_is_still_flagged():
    # 1080x1920 sounds like "exactly right" and isn't: the zoom pushes into
    # it, so part of the video is an enlargement.
    note = note_for(1080, 1920)
    assert note.upscale == pytest.approx(MAX_ZOOM, abs=0.01)
    assert any("blown up" in p for p in note.problems)


def test_a_big_photo_is_never_flagged_for_size():
    note = note_for(2160, 3840)
    assert note.upscale < 1.0
    assert not any("blown up" in p for p in note.problems)


def test_a_small_photo_reports_how_far_it_is_stretched():
    note = note_for(540, 960)
    assert note.upscale == pytest.approx(2 * MAX_ZOOM, abs=0.01)
    assert any("2.6" in p for p in note.problems)


def test_the_advice_names_a_size_that_would_actually_work():
    note = note_for(600, 1000)
    fix = next(p for p in note.problems if "blown up" in p)
    assert f"{int(1080 * MAX_ZOOM)}×{int(1920 * MAX_ZOOM)}" in fix


# --------------------------------------------------------------------- crop


def test_a_matching_shape_keeps_everything():
    assert note_for(1440, 2560).kept == pytest.approx(1.0)


def test_a_landscape_photo_in_a_reel_loses_most_of_its_width():
    note = note_for(1280, 720)
    assert note.kept == pytest.approx(0.316, abs=0.005)
    assert any("sides get cut off" in p for p in note.problems)


def test_a_very_tall_photo_loses_top_and_bottom():
    note = note_for(1080, 4000)
    assert any("top and bottom" in p for p in note.problems)


@pytest.mark.parametrize("w,h,kept", [
    (2048, 2731, 0.75),      # 3:4 — a phone held upright
    (2048, 2560, 0.70),      # 4:5 — Instagram portrait
])
def test_ordinary_portrait_shapes_pass_quietly(w, h, kept):
    # These are just what a phone produces. Warning about every one of them
    # would be noise nobody reads.
    note = note_for(w, h)
    assert note.kept == pytest.approx(kept, abs=0.01)
    assert not any("cut off" in p for p in note.problems)


@pytest.mark.parametrize("w,h", [(2000, 2000), (2400, 1800), (1920, 1080)])
def test_square_and_landscape_are_flagged(w, h):
    assert any("cut off" in p for p in note_for(w, h).problems)


def test_both_problems_can_apply_at_once():
    note = note_for(1280, 720)
    assert len(note.problems) == 2
    # Cropping is listed first: it loses information, blur only softens it.
    assert "cut off" in note.problems[0]


# ------------------------------------------------------------- other shapes


def test_the_same_photo_is_judged_per_aspect_ratio():
    # A 16:9 photo is the right shape for a widescreen video and the wrong
    # one for a reel; only the target it is measured against decides.
    landscape = (1920, 1080)
    assert note_for(*landscape, size=(1920, 1080)).kept == pytest.approx(1.0)
    assert note_for(*landscape, size=REEL).kept == pytest.approx(0.316, abs=0.005)


def test_a_square_post_is_judged_against_a_square():
    assert note_for(1400, 1400, size=(1080, 1080)).kept == pytest.approx(1.0)


# ------------------------------------------------------------- from disk


def test_advice_reads_real_files(project):
    photos = sorted((project / "products" / "test-rack" / "photos").glob("*.jpg"))
    notes = photo_advice(photos)
    assert len(notes) == 3
    # The fixtures are 720x1280 -- right shape, too small.
    for n in notes:
        assert (n.width, n.height) == (720, 1280)
        assert n.kept == pytest.approx(1.0)
        assert any("blown up" in p for p in n.problems)


def test_advice_is_silent_rather_than_fatal_without_ffmpeg(monkeypatch, project):
    import reelfactory.render as render
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    photos = sorted((project / "products" / "test-rack" / "photos").glob("*.jpg"))
    assert photo_advice(photos) == []


def test_advice_on_an_unreadable_file_is_silent(tmp_path):
    junk = tmp_path / "notreally.jpg"
    junk.write_text("this is not an image", encoding="utf-8")
    assert photo_advice([junk]) == []


def test_probe_still_raises_on_an_unreadable_file(tmp_path):
    from reelfactory.render import RenderError, validate_photos
    junk = tmp_path / "notreally.jpg"
    junk.write_text("this is not an image", encoding="utf-8")
    with pytest.raises(RenderError, match="notreally.jpg"):
        validate_photos([junk])


# ---------------------------------------------------------------- in the UI


def test_the_product_page_warns_on_each_bad_photo(client):
    html = client.get("/products/test-rack/edit").get_data(as_text=True)
    assert "will lose quality" in html
    assert "720×1280" in html
    assert html.count("has-problem") == 3


def test_the_build_page_warns_before_the_time_is_spent(client):
    html = client.get("/products/test-rack/build").get_data(as_text=True)
    assert "will lose quality" in html
    assert "Replace the photos" in html


def test_a_good_photo_produces_no_warning_anywhere(client, project, photo_cache):
    import shutil
    import subprocess
    photo_dir = project / "products" / "test-rack" / "photos"
    for old in photo_dir.iterdir():
        old.unlink()
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0xFF0000:s=1440x2560", "-frames:v", "1", str(photo_dir / "1.jpg")],
        check=True, capture_output=True,
    )
    for page in (f"/products/test-rack/edit", f"/products/test-rack/build"):
        assert "will lose quality" not in client.get(page).get_data(as_text=True), page
