"""Persistent, bounded error logs for the local workspace."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import uuid
import weakref

from flask import before_render_template, current_app, g, got_request_exception, request


class _RedactingFormatter(logging.Formatter):
    def __init__(self, env_path: Path):
        super().__init__("%(asctime)s %(levelname)s %(message)s")
        self.env_path = env_path

    def format(self, record):
        text = super().format(record)
        # Tracebacks have no local variables, bodies, query strings or headers.
        # Also remove configured secrets if a library includes one in its error.
        values = dict(os.environ)
        try:
            for line in self.env_path.read_text(encoding="utf-8-sig").splitlines():
                name, sep, value = line.strip().partition("=")
                if sep and not name.startswith("#"):
                    values[name.strip()] = value.strip().strip("\"'")
        except OSError:
            pass
        for name, value in values.items():
            if value and re.search(r"KEY|TOKEN|SECRET|PASSWORD", name, re.I):
                text = text.replace(value, "[REDACTED]")
        text = re.sub(r"(?i)([?&](?:key|api_key|token)=)[^&\s]+", r"\1[REDACTED]", text)
        return text


def record_failure(error):
    """Log a caught exception as well as exceptions Flask handles itself."""
    if getattr(g, "failure_logged", False):
        return
    g.failure_logged = True
    details = (type(error), error, error.__traceback__) if isinstance(error, BaseException) else None
    current_app.extensions["error_logger"].error(
        "request_id=%s method=%s route=%s endpoint=%s failure=%s",
        g.request_id, request.method,
        request.url_rule.rule if request.url_rule else "<unmatched>", request.endpoint,
        error, exc_info=details,
    )


def configure_diagnostics(app, workspace: Path):
    log_path = workspace.resolve() / "logs" / "reelfactory.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=5,
                                  encoding="utf-8", delay=True)
    handler.setFormatter(_RedactingFormatter(workspace / ".env"))
    logger = logging.Logger(f"reelfactory.errors.{id(app)}", level=logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    app.extensions["error_logger"] = logger
    app.config["ERROR_LOG_PATH"] = str(log_path)
    weakref.finalize(app, handler.close)

    @app.before_request
    def identify_request():
        g.request_id = uuid.uuid4().hex[:16]
        g.failure_logged = False

    def unhandled(sender, exception, **extra):
        record_failure(exception)

    def template_error(sender, template, context, **extra):
        if context.get("error") and not g.failure_logged:
            record_failure(sys.exc_info()[1] or context["error"])

    got_request_exception.connect(unhandled, app, weak=False)
    before_render_template.connect(template_error, app, weak=False)

    @app.after_request
    def finish_request(response):
        response.headers["X-Request-ID"] = g.request_id
        if response.status_code >= 400 and not g.failure_logged:
            logger.warning("request_id=%s method=%s route=%s status=%s",
                           g.request_id, request.method,
                           request.url_rule.rule if request.url_rule else "<unmatched>",
                           response.status_code)
        return response

    @app.errorhandler(500)
    def internal_error(error):
        # Standalone HTML still works if the failure was in the normal templates.
        return (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Something went wrong — Reel Factory</title>"
            "<main style='max-width:640px;margin:10vh auto;padding:24px;font:18px/1.6 system-ui'>"
            "<h1>Something went wrong</h1><p>The error details have been saved in "
            "<code>logs/reelfactory.log</code>.</p>"
            f"<p>Error reference: <strong>{g.request_id}</strong></p>"
            "<p>You can share this reference to help find the failure.</p>"
            "<a href='/'>Back to products</a></main></html>", 500,
        )
