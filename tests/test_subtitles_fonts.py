"""Font detection.

The bug this guards: on Windows the check matched font *filenames*, so
"Nirmala UI" -- which every Windows PC has, inside Nirmala.ttc -- was reported
missing, and the tool told people to install a font they already had.
"""
from __future__ import annotations

import platform

import pytest

from reelfactory import subtitles


@pytest.fixture(autouse=True)
def clear_caches():
    """Detection memoises hard (it is called per candidate family); tests that
    fake the environment have to start from cold."""
    subtitles._cache.clear()
    subtitles._families = None
    subtitles._win_fonts = None
    yield
    subtitles._cache.clear()
    subtitles._families = None
    subtitles._win_fonts = None


def test_pick_font_always_returns_a_single_family():
    # ASS allows exactly one family per style: a comma list corrupts the style
    # line and silently disables all on-screen text.
    for lang in ("hi", "en"):
        assert "," not in subtitles.pick_font(lang)
        assert subtitles.pick_font(lang).strip()


def test_override_wins_and_is_reduced_to_one_family():
    assert subtitles.pick_font("hi", "My Font, Fallback") == "My Font"


def test_family_name_is_found_even_when_the_file_is_named_differently(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    # The registry knows "Nirmala UI"; the folder only ever shows Nirmala.ttc.
    monkeypatch.setattr(subtitles, "_windows_font_names", lambda: {"nirmalaui", "nirmala.ttc"})
    monkeypatch.setattr(subtitles.os, "listdir", lambda p: ["Nirmala.ttc"])

    assert subtitles._installed("Nirmala UI") is True
    assert subtitles.missing_devanagari() is False


def test_unregistered_font_dropped_in_the_folder_still_counts(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subtitles, "_windows_font_names", lambda: set())
    monkeypatch.setattr(subtitles.os, "listdir", lambda p: ["NotoSansDevanagari-Regular.ttf"])

    assert subtitles._installed("Noto Sans Devanagari") is True


def test_genuinely_missing_font_is_reported_missing(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subtitles, "_windows_font_names", lambda: {"segoeui", "arial.ttf"})
    monkeypatch.setattr(subtitles.os, "listdir", lambda p: ["segoeui.ttf", "arial.ttf"])

    assert subtitles.missing_devanagari() is True
    # ...and picking one still yields something renderable rather than crashing.
    assert subtitles.pick_font("hi")


def test_unreadable_font_folder_does_not_raise(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subtitles, "_windows_font_names", lambda: set())
    monkeypatch.setattr(subtitles.os, "listdir",
                        lambda p: (_ for _ in ()).throw(OSError("denied")))
    assert subtitles._installed("Nirmala UI") is False


def test_registry_read_survives_a_missing_winreg(monkeypatch):
    # Non-Windows CI importing this module must not explode.
    monkeypatch.setitem(__import__("sys").modules, "winreg", None)
    assert isinstance(subtitles._windows_font_names(), set)


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows font stack")
def test_this_windows_machine_can_draw_hindi():
    # The regression itself: Windows ships Devanagari, so this must be False.
    assert subtitles.missing_devanagari() is False
