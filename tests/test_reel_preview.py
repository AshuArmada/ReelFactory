"""Playable draft previews use exact scene edits and leave exports intact."""
import pytest

from conftest import form, needs_ffmpeg


URL = "/products/test-rack/preview"


def draft(**updates):
    data = {"preview_lang": "en", "preview_aspect": "9:16", "tts": "silent",
            "template": "classic", "seg_role_en": "hook", "seg_vo_en": "A sturdy rack.",
            "seg_overlay_en": "Sturdy", "seg_photo_en": "2.jpg", "preset": "slow"}
    data.update(updates)
    return data


@pytest.fixture
def renderer(monkeypatch):
    calls = []
    def build(prod, brand, lang, aspects, outroot, args, **kwargs):
        calls.append((lang, aspects, outroot, args, kwargs))
        video = outroot / prod.slug / "preview.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"preview")
        args.preview_results.append({"path": video, "scenes": [
            {"start": 0, "photo": kwargs["photo_names"][0], "end_card": False}]})
        return [video]
    monkeypatch.setattr("reelfactory.cli.build_one", build)
    return calls


def test_preview_uses_exact_draft_and_settings_in_separate_output(client, project, renderer):
    export = project / "out/test-rack/final.mp4"
    export.parent.mkdir()
    export.write_bytes(b"finished")
    response = client.post(URL, data=draft(voice_delivery="Warm", voice_rate="-10%", no_music="on"))
    assert response.status_code == 200
    lang, aspects, root, args, kwargs = renderer[0]
    assert lang == "en" and aspects == ["9:16"]
    assert root.is_relative_to(export.parent / ".previews")
    assert args.preview and args.preset == "ultrafast"
    assert args.tts == "silent" and args.voice_delivery == "Warm" and args.voice_rate == "-10%"
    assert args.no_music and args.template == "classic"
    assert kwargs["photo_names"] == ["2.jpg"]
    assert kwargs["segments"][0].vo == "A sturdy rack."
    assert response.json["scenes"][0]["photo"] == "2.jpg"
    assert client.get(response.json["url"]).data == b"preview"
    assert export.read_bytes() == b"finished"
    assert "preview.mp4" not in client.get("/products/test-rack/build").get_data(as_text=True)
    second = client.post(URL, data=draft())
    assert second.json["url"] != response.json["url"]


@pytest.mark.parametrize("changes", [
    {"preview_lang": "bad"}, {"preview_aspect": "bad"}, {"tts": "bad"},
    {"seg_vo_en": " "}, {"seg_vo_en": ["First", " "]},
    {"seg_photo_en": "missing.jpg"}, {"seg_photo_en": "../1.jpg"},
])
def test_invalid_preview_does_not_render(client, renderer, changes):
    response = client.post(URL, data=draft(**changes))
    assert response.status_code == 400 and response.json["error"]
    assert not renderer


def test_preview_errors_are_json_and_product_is_required(client, monkeypatch):
    from reelfactory.render import RenderError
    def fail(*args, **kwargs):
        raise RenderError("Preview renderer unavailable")
    monkeypatch.setattr("reelfactory.cli.build_one", fail)
    response = client.post(URL, data=draft())
    assert response.status_code == 400
    assert response.json["error"] == "Preview renderer unavailable"
    assert client.post("/products/missing/preview", data=draft()).status_code == 404


@needs_ffmpeg
@pytest.mark.slow
def test_real_preview_plays_selected_photos_and_refreshes_edits(client, project):
    from test_end_to_end import channel_at, video_size
    data = form(*draft(no_music="on").items(),
                ("seg_role_en", "usp"), ("seg_vo_en", "Plenty of storage."),
                ("seg_overlay_en", "Storage"), ("seg_photo_en", "3.jpg"))
    first = client.post(URL, data=data)
    assert first.status_code == 200, first.json
    def path(result):
        return project / "out" / result["url"].removeprefix("/out/")
    video = path(first.json)
    assert video_size(video) == "360,640"
    scenes = first.json["scenes"]
    assert len(scenes) == 2 and scenes[0]["start"] == 0 and scenes[1]["start"] > 0
    assert channel_at(video, 0.8) == "green"
    assert channel_at(video, scenes[1]["start"] + 0.8) == "blue"
    data.setlist("seg_photo_en", ["1.jpg", "3.jpg"])
    second = client.post(URL, data=data)
    assert second.status_code == 200, second.json
    assert channel_at(path(second.json), 0.8) == "red"
    assert channel_at(video, 0.8) == "green"  # Previous playback stays intact.
