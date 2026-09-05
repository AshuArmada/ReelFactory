"""Calendar, queue state, publishers, and runner behavior."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from reelfactory import calendar as cal
from reelfactory import cli, publish
from reelfactory.runner import Runner


NOW = datetime(2026, 9, 5, 19, 30)


def entry(**changes):
    values = dict(product="chair", lang="en", when=NOW, platform="dryrun", aspect="9:16")
    values.update(changes)
    return cal.Entry(**values)


def test_calendar_loads_valid_entries_in_time_order(tmp_path):
    path = tmp_path / "calendar.yaml"
    path.write_text(
        "- product: later\n  lang: hi\n  when: 2026-09-06 10:00\n  platform: folder\n"
        "- product: earlier\n  lang: en\n  when: 2026-09-05 09:00\n  aspect: 4:5\n",
        encoding="utf-8",
    )

    loaded = cal.load(path)

    assert [item.product for item in loaded] == ["earlier", "later"]
    assert loaded[0].aspect == "4:5"
    assert loaded[1].platform == "folder"


def test_plan_writes_an_aspect_yaml_will_keep_as_text(tmp_path):
    calendar = tmp_path / "calendar.yaml"

    result = cli.main([
        "plan", str(tmp_path / "chair"), "--start", "2026-09-05",
        "--days", "sat", "--aspect", "4:5", "--write", str(calendar),
    ])

    assert result == 0
    assert "aspect: '4:5'" in calendar.read_text(encoding="utf-8")
    assert cal.load(calendar)[0].aspect == "4:5"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product", "../outside"),
        ("product", r"..\outside"),
        ("lang", "fr"),
        ("aspect", "3:2"),
        ("platform", "tiktok"),
    ],
)
def test_calendar_rejects_unsafe_or_unsupported_values(tmp_path, field, value):
    values = {
        "product": "chair",
        "lang": "en",
        "when": "2026-09-05 19:30",
        "aspect": "9:16",
        "platform": "dryrun",
    }
    values[field] = value
    path = tmp_path / "calendar.yaml"
    path.write_text(
        "\n".join(f"{('- ' if i == 0 else '  ')}{key}: {item}" for i, (key, item) in enumerate(values.items())),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=f"entry 1.*{field}"):
        cal.load(path)


def test_calendar_and_state_corruption_are_plain_value_errors(tmp_path):
    calendar = tmp_path / "calendar.yaml"
    calendar.write_text("- product: [broken\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid calendar at line"):
        cal.load(calendar)
    with pytest.raises(ValueError, match="expected a JSON object"):
        cal.State.load(state)


def test_due_skips_stale_entries_and_persists_the_state(tmp_path):
    state_path = tmp_path / "state.json"
    state = cal.State.load(state_path)
    recent = entry(when=NOW - timedelta(hours=1))
    stale = entry(product="old-chair", when=NOW - timedelta(hours=49))

    assert cal.due([recent, stale], state, NOW, grace_hours=48) == [recent]
    assert cal.State.load(state_path).status(stale) == "skipped"


def test_folder_publisher_copies_video_caption_and_checklist(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    item = entry(platform="folder", note="approve first")

    written = publish.FolderPublisher(tmp_path / "drop").publish(item, source, "caption text")
    dest = tmp_path / "drop" / "2026-09-05"

    assert written == str(dest / "1930_chair_en.mp4")
    assert (dest / "1930_chair_en.mp4").read_bytes() == b"video"
    assert (dest / "1930_chair_en_caption.txt").read_text(encoding="utf-8") == "caption text"
    assert "approve first" in (dest / "TO_POST.txt").read_text(encoding="utf-8")


def test_runner_builds_missing_media_and_publishes_it(tmp_path, monkeypatch):
    out = tmp_path / "out"
    state = cal.State(tmp_path / "state.json")
    item = entry()
    built = []

    def build(product, lang, aspect):
        built.append((product, lang, aspect))
        target = out / product
        target.mkdir(parents=True)
        (target / item.video_name).write_bytes(b"video")
        (target / item.caption_name).write_text("caption", encoding="utf-8")

    monkeypatch.setattr(publish, "get", lambda *_args: publish.DryRunPublisher())
    runner = Runner(None, out, tmp_path / "drop", build)

    assert runner.run([item], state, NOW, grace=48) == (1, 0)
    assert built == [("chair", "en", "9:16")]
    assert state.status(item) == "published"


def test_runner_retries_temporary_publish_errors_but_not_unwired_platforms(tmp_path):
    item = entry()
    state = cal.State(tmp_path / "state.json")
    runner = Runner(None, tmp_path / "out", tmp_path / "drop", lambda *_args: None, retries=3)

    runner._handle_failure(item, state, publish.PublishError("disk busy"))
    assert state.status(item) == "pending"
    assert state.attempts(item) == 1

    runner._handle_failure(item, state, publish.PermanentPublishError("not connected"))
    assert state.status(item) == "failed"
    assert state.attempts(item) == 2
