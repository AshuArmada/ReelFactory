"""Request protections for the local browser workspace."""
import secrets
from urllib.parse import urlsplit

from flask import request, session


def configure(app):
    app.config.update(
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
        MAX_CONTENT_LENGTH=256 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
    )

    def request_token():
        if "request_csrf" not in session:
            session["request_csrf"] = secrets.token_urlsafe(32)
        return session["request_csrf"]

    app.jinja_env.globals["request_token"] = request_token

    @app.before_request
    def protect_request():
        # Also enforce this on Flask 3.0, which lacks native TRUSTED_HOSTS support.
        host = urlsplit(request.host_url).hostname
        if host not in {value.strip("[]") for value in app.config["TRUSTED_HOSTS"]}:
            return {"error": "Unrecognized server host."}, 400
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return {"error": "Cross-site requests are not allowed."}, 403
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return {"error": "The request must come from this workspace."}, 403
        expected = session.get("request_csrf", "")
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token", "")
        if not expected or not secrets.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
            return {"error": "The page has expired. Reload it before trying again."}, 403

    @app.errorhandler(413)
    def upload_too_large(error):
        return {"error": "Upload too large. The request limit is 256 MB."}, 413
