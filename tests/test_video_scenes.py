"""Video scenes keep motion and loop in both previews and final exports."""
from io import BytesIO
import subprocess

import pytest

from conftest import form, needs_ffmpeg


@pytest.fixture
def clip(tmp_path):
    dest = tmp_path / "motion.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=lime:s=180x320:d=0.75:r=30",
        "-f", "lavfi", "-i", "color=c=blue:s=180x320:d=0.75:r=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(dest),
    ], check=True, capture_output=True)
    return dest


@needs_ffmpeg
def test_broken_video_upload_is_rejected_without_changing_library(client, project):
    photos = project / "products/test-rack/photos"
    before = set(photos.iterdir())
    response = client.post("/products/test-rack/scenes/media", data={
        "media": (BytesIO(b"this is not a video"), "broken.mp4")})
    assert response.status_code == 400
    assert "video could not be read" in response.json["error"]
    assert set(photos.iterdir()) == before


@needs_ffmpeg
@pytest.mark.slow
def test_uploaded_video_moves_and_loops_beside_photos_in_preview_and_export(client, project, clip):
    from test_end_to_end import channel_at, video_size
    from reelfactory.web.app import _load_saved_scripts
    response = client.post("/products/test-rack/scenes/media", data={
        "media": (BytesIO(clip.read_bytes()), "motion.MP4")})
    assert response.status_code == 201
    name = response.json["name"]
    assert name.endswith(".mp4")
    data = form(
        ("lang", "en"), ("preview_lang", "en"), ("preview_aspect", "9:16"),
        ("aspect", "9:16"), ("tts", "silent"), ("preset", "ultrafast"),
        ("template", "classic"), ("no_music", "on"),
        ("seg_role_en", "hook"), ("seg_vo_en", "See how this product works in your home."),
        ("seg_overlay_en", "Moving video"), ("seg_photo_en", name),
        ("seg_role_en", "usp"), ("seg_vo_en", "A photo comes next."),
        ("seg_overlay_en", "Photo"), ("seg_photo_en", "1.jpg"),
        ("save_name", "Video and photo"),
    )
    saved = client.post("/products/test-rack/script/save", data=data)
    assert saved.status_code == 200
    assert _load_saved_scripts(project / "products", "test-rack")["en"][0]["segments"][0]["photo"] == name
    preview = client.post("/products/test-rack/preview", data=data)
    assert preview.status_code == 200, preview.json
    preview_file = project / "out" / preview.json["url"].removeprefix("/out/")
    built = client.post("/products/test-rack/build", data=data)
    assert built.status_code == 200
    exported = project / "out/test-rack/test-rack_en_9x16.mp4"
    assert exported.exists()
    assert video_size(exported) == "1080,1920"
    for video in (preview_file, exported):
        assert [channel_at(video, time) for time in (0.5, 1.1, 2.0)] == ["green", "blue", "green"]
        assert channel_at(video, preview.json["scenes"][1]["start"] + 0.8) == "red"
        audio = subprocess.run(["ffmpeg", "-v", "info", "-i", str(video),
                                "-af", "volumedetect", "-vn", "-f", "null", "-"],
                               capture_output=True, text=True, check=True)
        # The source has a loud sine wave; scene audio must not leak over narration.
        assert "max_volume: -91.0 dB" in audio.stderr
