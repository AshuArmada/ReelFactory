"""Encoder settings: the quality knobs and what they actually reach.

The thing worth pinning down here is that "slower preset" now means "better
picture". It did not before: at a fixed CRF, a slower preset spends its extra
time finding a *smaller* file of the same quality, so the UI's promise of
"slowest, best picture" was not true of the output.
"""
from __future__ import annotations

import subprocess

import pytest

from reelfactory import render
from reelfactory.render import (
    DEFAULT_CRF, GRAIN, MAX_ZOOM, PRESET_CRF, SHOT_CRF, SHOT_PRESET,
)

from conftest import needs_ffmpeg


@pytest.fixture
def captured(monkeypatch):
    """Every ffmpeg command line render() builds, without running any."""
    calls = []

    def fake_run(args, cwd=None, what="", timeout=600):
        calls.append(list(args))
        # _make_scrim probes for its own output; pretend it appeared.
        for i, a in enumerate(args):
            if str(a).endswith(".png") and i == len(args) - 1:
                from pathlib import Path
                Path(a).parent.mkdir(parents=True, exist_ok=True)
                Path(a).write_bytes(b"x")
        return ""

    monkeypatch.setattr(render, "_run", fake_run)
    monkeypatch.setattr(render, "_require", lambda b: None)
    return calls


def build(tmp_path, captured, **kwargs):
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    sub = tmp_path / "t.ass"
    sub.write_text("", encoding="utf-8")
    voice = tmp_path / "v.wav"
    voice.write_bytes(b"x")
    render.render(
        [render.Shot(photo, 2.0), render.Shot(photo, 2.0)],
        sub, voice, tmp_path / "out.mp4", (1080, 1920), tmp_path / "work", **kwargs,
    )
    return captured


def final_call(calls):
    return next(c for c in calls if "-filter_complex" in c)


def shot_calls(calls):
    return [c for c in calls if "-loop" in c]


def value_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


# --------------------------------------------------------- quality follows preset


@pytest.mark.parametrize("preset,crf", sorted(PRESET_CRF.items()))
def test_each_preset_carries_its_own_quality(tmp_path, captured, preset, crf):
    calls = build(tmp_path, captured, preset=preset)
    cmd = final_call(calls)
    assert value_after(cmd, "-preset") == preset
    assert value_after(cmd, "-crf") == str(crf)


def test_slower_presets_really_are_higher_quality():
    order = ["ultrafast", "veryfast", "faster", "medium", "slow"]
    crfs = [PRESET_CRF[p] for p in order]
    assert crfs == sorted(crfs, reverse=True), "a slower preset must lower the CRF"


def test_an_explicit_crf_overrides_the_preset(tmp_path, captured):
    calls = build(tmp_path, captured, preset="ultrafast", crf=14)
    assert value_after(final_call(calls), "-crf") == "14"


def test_an_unknown_preset_falls_back_to_the_default_quality(tmp_path, captured):
    calls = build(tmp_path, captured, preset="placebo")
    assert value_after(final_call(calls), "-crf") == str(DEFAULT_CRF)


def test_crf_zero_is_honoured_not_treated_as_unset(tmp_path, captured):
    # 0 is falsy; a `crf or default` would silently discard a lossless request.
    calls = build(tmp_path, captured, crf=0)
    assert value_after(final_call(calls), "-crf") == "0"


# ------------------------------------------------------------- the intermediate


def test_the_intermediate_is_higher_quality_than_the_master(tmp_path, captured):
    calls = build(tmp_path, captured, preset="slow")
    shot = shot_calls(calls)[0]
    assert value_after(shot, "-crf") == str(SHOT_CRF)
    assert value_after(shot, "-preset") == SHOT_PRESET
    # Pass two re-encodes pass one's output, so pass one must not be the
    # thing that limits the result.
    assert SHOT_CRF < min(PRESET_CRF.values())


def test_every_shot_is_rendered_at_the_same_quality(tmp_path, captured):
    calls = build(tmp_path, captured)
    assert {value_after(c, "-crf") for c in shot_calls(calls)} == {str(SHOT_CRF)}


# ---------------------------------------------------------------------- grain


def test_grain_is_applied_after_the_overlays(tmp_path, captured):
    chain = value_after(final_call(build(tmp_path, captured)), "-filter_complex")
    assert f"noise=alls={GRAIN}" in chain
    # It must come after the subtitles: the banding it hides is made by the
    # scrim and the text, not by the photo.
    assert chain.index("subtitles=") < chain.index("noise=")
    assert chain.index("noise=") < chain.index("fade=t=out")


def test_the_grain_is_faint_enough_to_be_invisible():
    assert 0 < GRAIN <= 4


# -------------------------------------------------------------- output format


def test_the_container_is_web_ready(tmp_path, captured):
    cmd = final_call(build(tmp_path, captured))
    assert value_after(cmd, "-movflags") == "+faststart"
    assert value_after(cmd, "-pix_fmt") == "yuv420p"
    assert value_after(cmd, "-c:a") == "aac"


def test_full_range_photos_are_converted_not_just_relabelled(tmp_path, captured):
    """JPEGs decode full-range. Carried through untouched, the video plays
    back washed out anywhere broadcast range is assumed."""
    calls = build(tmp_path, captured)
    shot = shot_calls(calls)[0]
    assert "out_range=tv" in value_after(shot, "-vf")
    assert value_after(shot, "-color_range") == "tv"
    assert value_after(final_call(calls), "-color_range") == "tv"


def test_the_zoom_constant_drives_the_motion_expressions():
    z, _x, _y = render._motion("in_center", 60)
    assert str(MAX_ZOOM) in z
    z_out, _x, _y = render._motion("out_center", 60)
    assert str(MAX_ZOOM) in z_out


# -------------------------------------------------- it still actually encodes


@needs_ffmpeg
@pytest.mark.slow
@pytest.mark.parametrize("preset", ["ultrafast", "medium"])
def test_the_settings_produce_a_real_playable_file(project, preset):
    from reelfactory.cli import build_one
    from reelfactory.config import Brand, Product
    from reelfactory.script import Segment

    class Args:
        tts = "silent"
        no_music = True
        script = "template"
        steer = ""
        keep_temp = False
        gemini_key = gemini_backup_key = grok_key = None
        local_url = local_model = local_key = None

    Args.preset = preset
    written = build_one(
        Product.load(project / "products" / "test-rack"),
        Brand.load(project / "brand.yaml"),
        "en", ["9:16"], project / "out", Args(),
        segments=[Segment("hook", "A line of narration here.", "A line")],
    )
    video = next(p for p in written if p.suffix == ".mp4")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,width,height,pix_fmt,color_range", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True).stdout.strip()
    # Broadcast range, not the source JPEGs' full range: anything else plays
    # back with lifted blacks on a player that assumes tv range.
    assert probe == "h264,1080,1920,yuv420p,tv"
