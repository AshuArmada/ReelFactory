"""The real thing: photos in, an .mp4 out, checked pixel by pixel.

Every other test stops at the boundary of ffmpeg. These render actual video
and then read it back, because the claim being made -- "the photo shown beside
a line is the photo that line is spoken over" -- is only true if it survives
the whole pipeline, and nothing short of looking at the frames proves that.

Each photo is a different saturated colour, so "which photo is on screen at
second N" reduces to "which colour channel is brightest", which ffmpeg answers
by scaling one frame down to a single pixel. Ken Burns, the text, the scrim and
the watermark all change how *bright* that pixel is; none of them change which
channel wins.

Marked slow (a render is 10-30s). Run only these with  -m slow, or skip them
with  -m "not slow".
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from reelfactory import voice
from reelfactory.cli import build_one
from reelfactory.config import Brand, Product
from reelfactory.render import XFADE, plan
from reelfactory.script import Segment

from conftest import form, needs_ffmpeg

pytestmark = [needs_ffmpeg, pytest.mark.slow]

LINES = [
    ("hook", "This is the very first line of the advert."),
    ("usp", "This is the second line of the advert, a little longer."),
    ("cta", "This is the third and final line."),
]


class Args:
    """The subset of the CLI namespace that build_one reads."""
    tts = "silent"
    preset = "ultrafast"
    no_music = True
    script = "template"
    steer = ""
    keep_temp = False
    gemini_key = gemini_backup_key = grok_key = None
    local_url = local_model = local_key = None


# ------------------------------------------------------------------- reading


def channel_at(video: Path, at: float) -> str:
    """Which colour channel dominates the whole frame at `at` seconds."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    assert len(raw) >= 3, f"no frame decoded at {at:.2f}s of {video.name}"
    r, g, b = int(raw[0]), int(raw[1]), int(raw[2])
    return max((r, "red"), (g, "green"), (b, "blue"))[1]


def video_size(video: Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def shot_midpoints(segments, lang: str) -> list:
    """A safe instant inside each shot, in seconds.

    Derived by running the *production* timing code -- the silent TTS backend
    and render.plan -- rather than re-deriving the arithmetic here. If the
    pacing rules ever change, this follows them instead of going quietly
    wrong and sampling the neighbouring shot.
    """
    tmp = Path(tempfile.mkdtemp(prefix="rf_timing_"))
    clips = voice.synthesize([s.vo for s in segments], lang, "x", "+0%",
                             tmp / "vo", backend="silent")
    shots, _timings = plan([c.duration for c in clips], voice.PAUSE)

    # plan() pads each shot by one cross-fade; the un-padded span is what is
    # solely on screen, and its midpoint is the furthest point from both
    # transitions.
    times, cursor = [], 0.0
    for span in (s - XFADE for s in shots):
        times.append(cursor + span / 2)
        cursor += span
    return times


# ------------------------------------------------------------------ building


def segments_of(*indexes):
    return [Segment(LINES[i][0], LINES[i][1], LINES[i][1][:24]) for i in indexes]


def render(project, segments, photo_names=None, lang="en", aspects=("9:16",)):
    prod = Product.load(project / "products" / "test-rack")
    brand = Brand.load(project / "brand.yaml")
    written = build_one(prod, brand, lang, list(aspects), project / "out", Args(),
                        segments=segments, photo_names=photo_names)
    return [p for p in written if p.suffix == ".mp4"]


# -------------------------------------------------------------------- tests


def test_default_build_uses_the_photos_in_order(project):
    segs = segments_of(0, 1, 2)
    videos = render(project, segs)
    assert len(videos) == 1
    assert [channel_at(videos[0], t) for t in shot_midpoints(segs, "en")] \
        == ["red", "green", "blue"]


def test_per_line_photos_reach_the_screen(project):
    """The headline claim: choose photo 3 for line 1, and line 1 shows it."""
    segs = segments_of(0, 1, 2)
    videos = render(project, segs, ["3.jpg", "1.jpg", "2.jpg"])
    assert [channel_at(videos[0], t) for t in shot_midpoints(segs, "en")] \
        == ["blue", "red", "green"]


def test_the_same_photo_can_be_used_for_several_lines(project):
    segs = segments_of(0, 1, 2)
    videos = render(project, segs, ["2.jpg", "2.jpg", "1.jpg"])
    assert [channel_at(videos[0], t) for t in shot_midpoints(segs, "en")] \
        == ["green", "green", "red"]


def test_a_deleted_photo_falls_back_instead_of_failing(project):
    segs = segments_of(0, 1, 2)
    videos = render(project, segs, ["3.jpg", "gone.jpg", "1.jpg"])
    got = [channel_at(videos[0], t) for t in shot_midpoints(segs, "en")]
    assert got[0] == "blue" and got[2] == "red"
    assert got[1] == "green"          # the cycled default for row 2


def test_photo_order_changes_the_default_without_renaming_files(project):
    spec = project / "products" / "test-rack" / "product.yaml"
    data = yaml.safe_load(spec.read_text(encoding="utf-8"))
    data["photo_order"] = ["3.jpg", "2.jpg", "1.jpg"]
    spec.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    segs = segments_of(0, 1, 2)
    videos = render(project, segs)
    assert [channel_at(videos[0], t) for t in shot_midpoints(segs, "en")] \
        == ["blue", "green", "red"]


def test_rebuilding_keeps_the_earlier_take_untouched(project):
    segs = segments_of(0, 1, 2)
    first = render(project, segs, ["1.jpg"] * 3)
    second = render(project, segs, ["3.jpg"] * 3)

    assert first[0].name == "test-rack_en_9x16.mp4"
    assert second[0].name == "test-rack_en_9x16_2.mp4"

    at = shot_midpoints(segs, "en")[0]
    assert channel_at(first[0], at) == "red", "the first take was overwritten"
    assert channel_at(second[0], at) == "blue"


def test_a_variant_tag_does_not_collide_with_the_plain_name(project):
    prod = Product.load(project / "products" / "test-rack")
    brand = Brand.load(project / "brand.yaml")
    render(project, segments_of(0))
    written = build_one(prod, brand, "en", ["9:16"], project / "out", Args(),
                        segments=segments_of(0), photo_names=["2.jpg"], variant_tag="_v2")
    names = {p.name for p in written if p.suffix == ".mp4"}
    assert names == {"test-rack_en_v2_9x16.mp4"}


def test_each_aspect_renders_at_its_real_size(project):
    videos = render(project, segments_of(0, 1), aspects=("9:16", "1:1"))
    assert {video_size(v) for v in videos} == {"1080,1920", "1080,1080"}


def test_a_caption_file_is_written_beside_the_video(project):
    render(project, segments_of(0, 1))
    cap = project / "out" / "test-rack" / "test-rack_en_caption.txt"
    assert cap.exists() and cap.read_text(encoding="utf-8").strip()


def test_hindi_renders_and_leaves_no_temp_behind(project, tmp_path):
    before = set(Path(tempfile.gettempdir()).glob("rf_test-rack_*"))
    videos = render(project, segments_of(0, 1), lang="hi")
    assert videos[0].exists()
    assert not (set(Path(tempfile.gettempdir()).glob("rf_test-rack_*")) - before)


# ---------------------------------------------- the whole path through the UI


def test_build_through_the_web_ui(client, project):
    """What a person actually does: edit the script, point two lines at
    specific photos, press Build, and get a playable file plus its caption."""
    data = form(("lang", "en"), ("aspect", "9:16"), ("script", "template"),
                ("tts", "silent"), ("preset", "ultrafast"), ("no_music", "on"),
                ("seg_role_en", "hook"), ("seg_vo_en", LINES[0][1]),
                ("seg_overlay_en", "One"), ("seg_photo_en", "3.jpg"),
                ("seg_role_en", "cta"), ("seg_vo_en", LINES[1][1]),
                ("seg_overlay_en", "Two"), ("seg_photo_en", "1.jpg"))
    html = client.post("/products/test-rack/build", data=data).get_data(as_text=True)

    assert 'class="done-panel"' in html
    assert "1 video ready" in html

    video = project / "out" / "test-rack" / "test-rack_en_9x16.mp4"
    assert video.exists() and video.stat().st_size > 10_000

    segs = [Segment("hook", LINES[0][1], "One"), Segment("cta", LINES[1][1], "Two")]
    assert [channel_at(video, t) for t in shot_midpoints(segs, "en")] == ["blue", "red"]
