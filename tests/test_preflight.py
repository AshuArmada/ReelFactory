"""The dashboard readiness checks.

These run on every dashboard load and describe the user's machine, so the
contract under test is mostly "never raises, never lies, stays cheap" rather
than any particular answer -- the answers legitimately differ per machine.
"""
from __future__ import annotations

import socket
import time

import pytest

from reelfactory import preflight
from reelfactory.config import Brand


def by_key(checks):
    return {c.key: c for c in checks}


def test_every_check_reports_something(project):
    checks = preflight.run(Brand.load(project / "brand.yaml"))
    assert {c.key for c in checks} == {
        "ffmpeg", "font_hi", "tts", "gemini", "grok", "local", "stock",
    }
    for c in checks:
        assert c.label and c.detail, f"{c.key} reported nothing readable"
        assert c.ok in (True, False, None)
        # A failure the user cannot act on is just an alarm.
        if c.ok is False:
            assert c.fix, f"{c.key} failed without telling the user what to do"


def test_runs_without_a_brand_at_all(monkeypatch):
    # The dashboard shows this panel even when brand.yaml is broken or absent,
    # so run(None) has to work.
    checks = preflight.run(None)
    assert len(checks) == 7


def test_a_broken_check_cannot_take_the_page_down(monkeypatch):
    monkeypatch.setattr(preflight.subtitles, "missing_devanagari",
                        lambda: (_ for _ in ()).throw(OSError("no fontconfig")))
    assert by_key(preflight.run(None))["font_hi"].ok is None


def test_font_override_is_trusted(project):
    brand = Brand.load(project / "brand.yaml")
    brand.font_hi = "Some Custom Font"
    check = by_key(preflight.run(brand))["font_hi"]
    assert check.ok is True and "Some Custom Font" in check.detail


def test_missing_ffmpeg_is_reported_as_blocking(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    checks = preflight.run(None)
    assert by_key(checks)["ffmpeg"].ok is False
    assert "ffmpeg" in {c.key for c in preflight.blocking(checks)}


def test_missing_tts_is_blocking_but_a_missing_key_is_not(monkeypatch):
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(preflight, "_gemini",
                        lambda: preflight.Check("gemini", "Gemini key", None, "not set"))
    checks = preflight.run(None)
    blocking = {c.key for c in preflight.blocking(checks)}
    assert "tts" in blocking            # nothing can be voiced
    assert "gemini" not in blocking     # other writers still work


def test_a_key_that_is_absent_is_unknown_not_failed(monkeypatch):
    import reelfactory.gemini as gem
    monkeypatch.setattr(gem, "resolve_key",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    check = by_key(preflight.run(None))["gemini"]
    # None, not False: not having a Gemini key is a normal way to use the tool.
    assert check.ok is None
    assert not check.fix


def test_local_server_check_does_not_hang_on_a_dead_port(monkeypatch):
    brand = Brand()
    brand.local_base_url = "http://127.0.0.1:9/v1"   # discard port: never listening
    start = time.monotonic()
    check = by_key(preflight.run(brand))["local"]
    assert check.ok is None
    assert time.monotonic() - start < 5


def test_local_server_check_sees_a_real_listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert preflight._listening(f"http://127.0.0.1:{port}/v1") is True
    finally:
        srv.close()
    assert preflight._listening(f"http://127.0.0.1:{port}/v1") is False


@pytest.mark.parametrize("url", ["", "not a url", "http://", "ftp://x:99999"])
def test_listening_never_raises_on_junk(url):
    assert preflight._listening(url) is False
