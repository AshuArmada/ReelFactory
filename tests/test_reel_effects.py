"""Optional reel effects travel with previews, queued jobs and portable presets."""
from __future__ import annotations

import subprocess
import shutil
from dataclasses import replace

import pytest
import yaml

from conftest import needs_ffmpeg
from reelfactory import effects, render, subtitles, templates


@pytest.mark.parametrize("setting,value", [
    ("effects", "sparks"), ("effects", ["unknown"]),
    ("effects", [{}]),
])
def test_reject_invalid_effect_configuration(tmp_path, setting, value):
    (tmp_path / "custom.yaml").write_text(yaml.safe_dump({setting: value}), encoding="utf-8")
    with pytest.raises(ValueError, match=setting):
        templates.load("custom", root=tmp_path)


def test_lights_are_repeatable_and_stay_within_video(tmp_path):
    first = effects.write(tmp_path / "a.ass", 640, 360, 3.0, [.9, 1.8, 2.9],
                          ["gold_sweep", "sparks", "glints"])
    second = effects.write(tmp_path / "b.ass", 640, 360, 3.0, [.9, 1.8, 2.9],
                           ["gold_sweep", "sparks", "glints"])
    assert first.read_bytes() == second.read_bytes()
    events = [line.split(",", 9) for line in first.read_text().splitlines() if line.startswith("Dialogue:")]

    def seconds(stamp):
        h, m, s = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    assert all(0 <= seconds(row[1]) < seconds(row[2]) <= 3 for row in events)
    assert effects.write(tmp_path / "off.ass", 640, 360, 3, [], [], .7) is None
    assert effects.write(tmp_path / "off.ass", 640, 360, 3, [], ["sparks"], 0) is None


@pytest.mark.parametrize("look", ["classic", "bold", "premium"])
def test_effect_selection_keeps_the_existing_look(look):
    original = templates.load(look)
    decorated = templates.with_effects(original, "gold", .35)
    assert replace(decorated, effects=original.effects, effect_strength=original.effect_strength) == original
    assert original.effects == []  # No mutation of shared defaults.
    assert templates.with_effects(decorated, "none").effects == []


def test_effects_are_optional_and_separate_from_look(client):
    response = client.get("/products/test-rack/build")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="visual_effects"' in html and 'name="effect_strength"' in html
    assert 'value="none" selected' in html
    assert 'value="gold"' in html and 'value="sparks"' in html
    assert 'value="tv-ad"' not in html


def test_preview_receives_effects_without_changing_look_or_words(client, monkeypatch):
    from reelfactory import cli
    from test_reel_preview import draft
    calls = []

    def build(prod, brand, lang, aspects, outroot, args, **kwargs):
        calls.append((args, kwargs))
        video = outroot / prod.slug / "preview.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"preview")
        args.preview_results.append({"path": video, "scenes": [
            {"start": 0, "photo": "2.jpg", "end_card": False}]})
        return [video]

    monkeypatch.setattr(cli, "build_one", build)
    response = client.post("/products/test-rack/preview", data=draft(visual_effects="glints", effect_strength="0.35"))
    assert response.status_code == 200, response.json
    args, values = calls[0]
    assert args.visual_effects == "glints" and args.effect_strength == .35
    assert args.template == "classic"
    assert values["segments"][0].overlay == "Sturdy"


@pytest.mark.parametrize("value", ["nan", "inf", "-0.1", "1.1", "bad"])
def test_invalid_strength_rejected_before_preview(client, monkeypatch, value):
    from test_reel_preview import draft
    monkeypatch.setattr("reelfactory.cli.build_one", lambda *a, **k: pytest.fail("Must validate before rendering"))
    response = client.post("/products/test-rack/preview", data=draft(effect_strength=value))
    assert response.status_code == 400 and "intensity" in response.json["error"]


def test_queued_recipe_and_replay_keep_effect_selection(client, app, monkeypatch):
    from test_render_jobs import reviewed, stub_renderer
    queue = app.extensions["render_queue"]
    monkeypatch.setattr(queue, "start", lambda: None)
    response = client.post("/products/test-rack/queue", data=reviewed(visual_effects="sparks", effect_strength="0.4"))
    assert response.status_code == 302
    recipe = queue.recipe(queue.jobs()[0])
    assert recipe["settings"]["visual_effects"] == "sparks"
    assert recipe["look"]["effects"] == ["sparks"]
    assert recipe["look"]["effect_strength"] == .4
    calls = []
    stub_renderer(monkeypatch, calls)
    queue.drain()
    args = calls[0][2]
    assert args.visual_effects == "sparks"
    assert args.render_template.effects == ["sparks"]


def test_effects_round_trip_through_portable_preset(client):
    payload = {"schema": 1, "name": "Instagram glints", "settings": {
        "tts": "silent", "preset": "ultrafast", "template": "classic", "voice_rate": "",
        "voice_delivery": "", "no_music": True, "lang": ["en"], "aspect": ["9:16"],
        "visual_effects": "glints", "effect_strength": .45}}
    response = client.post("/renders/presets", json=payload)
    assert response.status_code == 201
    assert client.get("/renders/presets/" + response.json["id"]).json == payload
    payload["settings"]["visual_effects"] = "unknown"
    assert client.post("/renders/presets", json=payload).status_code == 400


@needs_ffmpeg
@pytest.mark.slow
def test_lights_move_in_real_render_and_preserve_duration(tmp_path):
    """Compare decoded pixels, not just filter strings, including a scene cut."""
    size = (320, 180)
    source = tmp_path / "source.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x203040:s=320x180", "-frames:v", "1", str(source)], check=True)
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=mono:d=3", str(voice)], check=True)
    captions = subtitles.write(tmp_path / "text.ass", [subtitles.Cue("hook", "")],
                               [(0, 3, 0)], *size, "#E4572E", "#FFFFFF", "en")
    look = replace(templates.with_effects(templates.load("classic"), "gold"),
                   whoosh=0, accent_hit=0, scrim=0, transition_seconds=.35)
    outputs = []
    for enabled in (False, True):
        work = tmp_path / str(enabled)
        work.mkdir()
        # The compositor expects these local files in its work directory.
        shutil.copy(voice, work / voice.name)
        shutil.copy(captions, work / captions.name)
        out = tmp_path / f"{enabled}.mp4"
        render.render([render.Shot(source, 1.7, still=True), render.Shot(source, 1.65, still=True)],
                      work / captions.name, work / voice.name, out, size, work,
                      preset="ultrafast", template=look if enabled else replace(look, effects=[]))
        outputs.append(out)

    def frame(path, at):
        return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
                               "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                              capture_output=True, check=True).stdout

    for at in (.35, 1.70):
        plain, decorated = (frame(path, at) for path in outputs)
        assert len(plain) == len(decorated) == size[0] * size[1] * 3
        # Decorative lights must add visible brightness, including after a cut.
        assert sum(decorated) > sum(plain) + 20000
    assert frame(outputs[1], .35) != frame(outputs[1], .65)
    durations = [float(subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
        "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True).stdout)
        for path in outputs]
    assert durations[0] == pytest.approx(durations[1], abs=.04)
    assert durations[1] == pytest.approx(3.0, abs=.04)
