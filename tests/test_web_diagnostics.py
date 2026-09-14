"""Unexpected errors remain diagnosable after the browser page is gone."""
from pathlib import Path

import pytest
from flask import render_template_string

from reelfactory.web.app import create_app
from reelfactory.web.diagnostics import record_failure


@pytest.fixture
def diagnostic_app(tmp_path):
    app = create_app(tmp_path / "brand.yaml", tmp_path / "products", tmp_path / "out")
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    yield app
    for handler in app.extensions["error_logger"].handlers:
        handler.close()


def test_unhandled_error_has_persistent_traceback_and_matching_reference(diagnostic_app, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "private-test-key")
    workspace = Path(diagnostic_app.config["ERROR_LOG_PATH"]).parent.parent
    (workspace / ".env").write_text("GEMINI_API_KEY=private-dotenv-key\n", encoding="utf-8")

    @diagnostic_app.post("/test/failure")
    def failure():
        raise RuntimeError("Provider rejected private-test-key and private-dotenv-key")

    response = diagnostic_app.test_client().post("/test/failure?token=private-query", data={"script": "private draft"})
    assert response.status_code == 500
    reference = response.headers["X-Request-ID"]
    assert reference in response.get_data(as_text=True)
    text = Path(diagnostic_app.config["ERROR_LOG_PATH"]).read_text(encoding="utf-8")
    for expected in (reference, "method=POST", "route=/test/failure", "Traceback", "RuntimeError", "Provider rejected"):
        assert expected in text
    for private in ("private-test-key", "private-dotenv-key", "private-query", "private draft"):
        assert private not in text
    assert "RuntimeError" not in response.get_data(as_text=True)


def test_caught_failure_keeps_traceback_even_when_page_returns_200(diagnostic_app):
    @diagnostic_app.get("/test/caught")
    def caught():
        try:
            raise ValueError("The script provider failed")
        except ValueError as exc:
            record_failure(exc)
            error = str(exc)
        return render_template_string("{{ error }}", error=error)

    response = diagnostic_app.test_client().get("/test/caught")
    text = Path(diagnostic_app.config["ERROR_LOG_PATH"]).read_text(encoding="utf-8")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] in text
    assert "Traceback" in text
    assert text.count("method=GET") == 1


def test_logs_rotate_and_remain_readable_after_app_restart(diagnostic_app, tmp_path):
    handler = diagnostic_app.extensions["error_logger"].handlers[0]
    handler.maxBytes = 400

    @diagnostic_app.get("/test/rotate")
    def rotate():
        raise RuntimeError("Failure that should be kept across restarts")

    client = diagnostic_app.test_client()
    for _ in range(4):
        client.get("/test/rotate")
    handler.close()
    path = Path(diagnostic_app.config["ERROR_LOG_PATH"])
    assert path.with_name(path.name + ".1").exists()
    before = path.read_bytes()
    restarted = create_app(tmp_path / "brand.yaml", tmp_path / "products", tmp_path / "out")
    try:
        assert Path(restarted.config["ERROR_LOG_PATH"]).read_bytes().startswith(before)
    finally:
        for item in restarted.extensions["error_logger"].handlers:
            item.close()
