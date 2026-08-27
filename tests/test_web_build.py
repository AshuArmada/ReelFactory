"""The build page: wizard layout, what gets passed to the renderer, the done
panel, and deleting finished files.

The renderer itself is stubbed here so these stay fast -- what is under test
is the wiring between the form and `build_one`. `test_end_to_end.py` runs the
real thing.
"""
from __future__ import annotations

import re

import pytest

from conftest import form, live_html


BUILD = "/products/test-rack/build"


@pytest.fixture
def calls(monkeypatch, project):
    """Record every build_one call and fake its output files."""
    import reelfactory.cli as rf_cli
    seen = []

    def fake_build_one(prod, brand, lang, aspects, outroot, args,
                       segments=None, variant_tag="", photo_names=None):
        seen.append({
            "lang": lang, "aspects": list(aspects), "variant_tag": variant_tag,
            "segments": [(s.role, s.vo, s.overlay) for s in (segments or [])],
            "photo_names": list(photo_names or []),
            "tts": args.tts, "preset": args.preset, "no_music": args.no_music,
            "script": args.script, "steer": args.steer,
        })
        outdir = outroot / prod.slug
        outdir.mkdir(parents=True, exist_ok=True)
        written = []
        for aspect in aspects:
            p = rf_cli._free_path(outdir, f"{prod.slug}_{lang}{variant_tag}_{aspect.replace(':','x')}", ".mp4")
            p.write_bytes(b"fake")
            written.append(p)
        cap = outdir / f"{prod.slug}_{lang}_caption.txt"
        cap.write_text("Caption line\n#tag", encoding="utf-8")
        written.append(cap)
        return written

    monkeypatch.setattr(rf_cli, "build_one", fake_build_one)
    return seen


# ------------------------------------------------------------ wizard layout


def steps(html):
    """The page split into its three wizard steps."""
    parts = re.split(r"<!-- =+ step \d+ — [^>]*-->", live_html(html))
    return parts[1:4]


def test_step_one_asks_only_language_and_writer(client):
    choose, _script, _review = steps(client.get(BUILD).get_data(as_text=True))
    assert 'name="lang"' in choose and 'name="script"' in choose
    for moved in ('name="aspect"', 'name="tts"', 'name="preset"', 'name="no_music"'):
        assert moved not in choose, f"{moved} should have moved to the review step"


def test_the_moved_options_are_on_the_review_step(client):
    _choose, _script, review = steps(client.get(BUILD).get_data(as_text=True))
    for moved in ('name="aspect"', 'name="tts"', 'name="preset"', 'name="no_music"'):
        assert moved in review


def test_every_option_still_posts_from_one_form(client):
    html = client.get(BUILD).get_data(as_text=True)
    assert html.count('<form method="post" action="/products/test-rack/build"') == 1


# ---------------------------------------------------------------- the build


def test_a_plain_build_asks_the_writer(client, calls):
    client.post(BUILD, data={"lang": "hi", "aspect": "9:16", "script": "template",
                             "tts": "silent", "preset": "ultrafast"})
    assert len(calls) == 1
    assert calls[0]["segments"] == []          # no edited script passed
    assert calls[0]["photo_names"] == []
    assert calls[0]["tts"] == "silent" and calls[0]["preset"] == "ultrafast"


def test_an_edited_script_is_passed_through_verbatim(client, calls):
    data = form(("lang", "hi"), ("aspect", "9:16"), ("script", "template"),
                ("seg_role_hi", "hook"), ("seg_vo_hi", "First line"),
                ("seg_overlay_hi", "First"), ("seg_photo_hi", "3.jpg"),
                ("seg_role_hi", "cta"), ("seg_vo_hi", "Second line"),
                ("seg_overlay_hi", "Second"), ("seg_photo_hi", "1.jpg"))
    client.post(BUILD, data=data)

    assert calls[0]["segments"] == [("hook", "First line", "First"),
                                    ("cta", "Second line", "Second")]
    assert calls[0]["photo_names"] == ["3.jpg", "1.jpg"]


def test_a_blank_line_removes_its_photo_from_the_call(client, calls):
    data = form(("lang", "hi"), ("aspect", "9:16"), ("script", "template"),
                ("seg_role_hi", "hook"), ("seg_vo_hi", "Kept"),
                ("seg_overlay_hi", ""), ("seg_photo_hi", "1.jpg"),
                ("seg_role_hi", "usp"), ("seg_vo_hi", "  "),
                ("seg_overlay_hi", ""), ("seg_photo_hi", "2.jpg"),
                ("seg_role_hi", "cta"), ("seg_vo_hi", "Also kept"),
                ("seg_overlay_hi", ""), ("seg_photo_hi", "3.jpg"))
    client.post(BUILD, data=data)
    assert calls[0]["photo_names"] == ["1.jpg", "3.jpg"]


def test_one_call_per_language(client, calls):
    client.post(BUILD, data={"lang": ["hi", "en"], "aspect": "9:16", "script": "template"})
    assert [c["lang"] for c in calls] == ["hi", "en"]


def test_all_shapes_go_in_one_call(client, calls):
    # Shapes share the voiceover, so they must not re-synthesise per aspect.
    client.post(BUILD, data={"lang": "hi", "aspect": ["9:16", "1:1"], "script": "template"})
    assert len(calls) == 1 and calls[0]["aspects"] == ["9:16", "1:1"]


def test_defaults_when_nothing_is_ticked(client, calls):
    client.post(BUILD, data={"script": "template"})
    assert calls[0]["lang"] == "hi" and calls[0]["aspects"] == ["9:16"]


def test_multi_version_build_makes_one_video_per_version(client, calls):
    data = form(("script", "template"), ("aspect", "9:16"),
                ("build_versions", "hi:0"), ("build_versions", "hi:1"),
                ("ver0_seg_role_hi", "hook"), ("ver0_seg_vo_hi", "A"),
                ("ver0_seg_overlay_hi", "A"), ("ver0_seg_photo_hi", "1.jpg"),
                ("ver1_seg_role_hi", "hook"), ("ver1_seg_vo_hi", "B"),
                ("ver1_seg_overlay_hi", "B"), ("ver1_seg_photo_hi", "3.jpg"))
    client.post(BUILD, data=data)

    assert [c["variant_tag"] for c in calls] == ["_v1", "_v2"]
    assert [c["photo_names"] for c in calls] == [["1.jpg"], ["3.jpg"]]
    assert [c["segments"][0][1] for c in calls] == ["A", "B"]


def test_chosen_options_are_echoed_back_after_a_build(client, calls):
    html = client.post(BUILD, data={"lang": "en", "aspect": "1:1", "script": "template",
                                    "tts": "gtts", "preset": "slow", "no_music": "on"}
                       ).get_data(as_text=True)
    live = live_html(html)
    assert re.search(r'name="aspect" value="1:1" checked', live)
    assert re.search(r'value="gtts" selected', live)
    assert re.search(r'value="slow" selected', live)
    assert re.search(r'name="no_music" checked', live)


# ------------------------------------------------------------- done panel


def test_done_panel_shows_the_video_and_the_caption(client, calls):
    html = client.post(BUILD, data={"lang": "hi", "aspect": "9:16", "script": "template"}
                       ).get_data(as_text=True)
    assert 'class="done-panel"' in html
    assert "1 video ready" in html
    assert "test-rack_hi_9x16.mp4" in html
    assert "Caption line" in html                 # the text itself, not a filename
    assert 'data-copy-target="caption-0"' in html


def test_done_panel_counts_every_video_once(client, calls):
    html = client.post(BUILD, data={"lang": ["hi", "en"], "aspect": ["9:16", "1:1"],
                                    "script": "template"}).get_data(as_text=True)
    assert "4 videos ready" in html


def test_the_caption_is_listed_once_across_several_variants(client, calls):
    # Every variant of a product+language writes the same caption file.
    data = form(("script", "template"), ("aspect", "9:16"),
                ("build_versions", "hi:0"), ("build_versions", "hi:1"),
                ("ver0_seg_role_hi", "hook"), ("ver0_seg_vo_hi", "A"),
                ("ver0_seg_overlay_hi", ""), ("ver0_seg_photo_hi", "1.jpg"),
                ("ver1_seg_role_hi", "hook"), ("ver1_seg_vo_hi", "B"),
                ("ver1_seg_overlay_hi", ""), ("ver1_seg_photo_hi", "1.jpg"))
    html = client.post(BUILD, data=data).get_data(as_text=True)
    assert html.count('id="caption-0"') == 1
    assert 'id="caption-1"' not in html


def test_a_render_failure_is_shown_and_keeps_the_words(client, monkeypatch):
    import reelfactory.cli as rf_cli
    from reelfactory.render import RenderError
    monkeypatch.setattr(rf_cli, "build_one",
                        lambda *a, **k: (_ for _ in ()).throw(RenderError("ffmpeg died")))

    html = client.post(BUILD, data=form(
        ("lang", "hi"), ("aspect", "9:16"), ("script", "template"),
        ("seg_role_hi", "hook"), ("seg_vo_hi", "Keep me"),
        ("seg_overlay_hi", ""), ("seg_photo_hi", "2.jpg"),
    )).get_data(as_text=True)

    assert "ffmpeg died" in html
    assert "Keep me" in html
    assert 'class="done-panel"' not in html


# ------------------------------------------------------------ deleting files


def test_deleting_a_finished_file(client, project):
    out = project / "out" / "test-rack"
    out.mkdir(parents=True)
    (out / "a.mp4").write_bytes(b"x")
    (out / "b.mp4").write_bytes(b"x")

    html = client.post("/products/test-rack/build/delete",
                       data={"delete_file": "a.mp4"}).get_data(as_text=True)
    assert not (out / "a.mp4").exists()
    assert (out / "b.mp4").exists()
    assert "Deleted a.mp4" in html


def test_delete_cannot_reach_outside_the_output_folder(client, project):
    out = project / "out" / "test-rack"
    out.mkdir(parents=True)
    client.post("/products/test-rack/build/delete",
                data={"delete_file": "../../brand.yaml"})
    assert (project / "brand.yaml").exists()


def test_deleting_nothing_is_a_harmless_no_op(client, project):
    (project / "out" / "test-rack").mkdir(parents=True)
    assert client.post("/products/test-rack/build/delete", data={}).status_code == 200
