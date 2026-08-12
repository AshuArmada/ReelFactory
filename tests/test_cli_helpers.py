"""The two build_one helpers: which photo a line gets, and where it is written."""
from __future__ import annotations

import dataclasses

from reelfactory.cli import _free_path, _shot_photos
from reelfactory.config import Product


def load(project, slug="test-rack"):
    return Product.load(project / "products" / slug)


# ------------------------------------------------------------- _shot_photos


def test_photos_cycle_when_nothing_is_chosen(project):
    prod = load(project)                      # 3 photos
    got = _shot_photos(prod, 7)
    assert [p.name for p in got] == ["1.jpg", "2.jpg", "3.jpg", "1.jpg", "2.jpg", "3.jpg", "1.jpg"]


def test_explicit_names_are_used_verbatim(project):
    prod = load(project)
    got = _shot_photos(prod, 3, ["3.jpg", "3.jpg", "1.jpg"])
    assert [p.name for p in got] == ["3.jpg", "3.jpg", "1.jpg"]


def test_unknown_name_falls_back_to_the_cycled_default(project):
    # A photo deleted after the script was written must not break the build.
    prod = load(project)
    got = _shot_photos(prod, 3, ["3.jpg", "deleted.jpg", "1.jpg"])
    assert [p.name for p in got] == ["3.jpg", "2.jpg", "1.jpg"]


def test_short_name_list_falls_back_for_the_rest(project):
    prod = load(project)
    got = _shot_photos(prod, 4, ["3.jpg"])
    assert [p.name for p in got] == ["3.jpg", "2.jpg", "3.jpg", "1.jpg"]


def test_empty_name_list_is_the_same_as_none(project):
    prod = load(project)
    assert _shot_photos(prod, 3, []) == _shot_photos(prod, 3)


def test_single_photo_product_repeats_it(project):
    prod = load(project)
    prod = dataclasses.replace(prod, photos=prod.photos[:1])
    assert [p.name for p in _shot_photos(prod, 3)] == ["1.jpg"] * 3


# ---------------------------------------------------------------- _free_path


def test_first_build_uses_the_plain_name(tmp_path):
    assert _free_path(tmp_path, "rack_hi_9x16", ".mp4").name == "rack_hi_9x16.mp4"


def test_rebuilds_never_overwrite(tmp_path):
    names = []
    for _ in range(4):
        p = _free_path(tmp_path, "rack_hi_9x16", ".mp4")
        p.write_bytes(b"x")            # simulate the render landing
        names.append(p.name)
    assert names == [
        "rack_hi_9x16.mp4", "rack_hi_9x16_2.mp4",
        "rack_hi_9x16_3.mp4", "rack_hi_9x16_4.mp4",
    ]


def test_a_gap_in_the_sequence_is_filled(tmp_path):
    # Deleting _2 and rebuilding must not overwrite _3.
    for n in ("rack.mp4", "rack_3.mp4"):
        (tmp_path / n).write_bytes(b"x")
    assert _free_path(tmp_path, "rack", ".mp4").name == "rack_2.mp4"


def test_different_variants_and_shapes_do_not_collide(tmp_path):
    (tmp_path / "rack_hi_9x16.mp4").write_bytes(b"x")
    assert _free_path(tmp_path, "rack_hi_1x1", ".mp4").name == "rack_hi_1x1.mp4"
    assert _free_path(tmp_path, "rack_hi_v2_9x16", ".mp4").name == "rack_hi_v2_9x16.mp4"


def test_a_directory_in_the_way_still_yields_a_free_name(tmp_path):
    (tmp_path / "rack.mp4").mkdir()
    assert _free_path(tmp_path, "rack", ".mp4").name == "rack_2.mp4"
