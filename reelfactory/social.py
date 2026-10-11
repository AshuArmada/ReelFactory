"""Postiz public API adapter. No network requests happen at import time."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

PLATFORMS = {"instagram": "Instagram", "instagram-standalone": "Instagram",
             "facebook": "Facebook", "youtube": "YouTube"}
DEFAULT_URL = "https://api.postiz.com/public/v1"


class SocialError(ValueError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def setting(root: Path, name: str, default="") -> str:
    if name in os.environ:
        return os.environ[name].strip()
    path = Path(root) / ".env"
    if path.exists():
        for line in reversed(path.read_text(encoding="utf-8-sig").splitlines()):
            key, sep, value = line.strip().partition("=")
            if sep and key.strip().lower() == name.lower():
                return value.strip().strip("\"'")
    return default


def safe_url(value: str) -> str:
    """URLs may be displayed as links, never interpreted as executable schemes."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ""


class Postiz:
    def __init__(self, root: Path):
        self.key = setting(root, "POSTIZ_API_KEY")
        self.base = setting(root, "POSTIZ_BASE_URL", DEFAULT_URL).rstrip("/")
        parsed = urlsplit(self.base)
        if (not safe_url(self.base) or parsed.query or parsed.fragment or
                (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise SocialError("Postiz URL must use HTTPS (HTTP is allowed for localhost).")
        if not self.key:
            raise SocialError("Add your Postiz API key in API settings, then connect your social accounts in Postiz.")

    def call(self, method, path, **kwargs):
        try:
            response = requests.request(method, self.base + path,
                headers={"Authorization": self.key}, timeout=(10, 120),
                allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise SocialError("Postiz did not confirm the request. Check your connection and the Postiz calendar.",
                              uncertain=method != "GET") from None
        if not 200 <= response.status_code < 300:
            code = response.status_code
            detail = {401: "Check your API key.", 403: "Check API access and account permissions.",
                      429: "Rate limit reached; wait before trying again."}.get(code, "Check the account and post settings in Postiz.")
            # Never echo response bodies, which can contain credentials or remote HTML.
            raise SocialError(f"Postiz returned HTTP {code}. {detail}",
                              uncertain=method != "GET" and (code >= 500 or code == 408))
        try:
            return response.json()
        except ValueError:
            raise SocialError("Postiz returned an unreadable response.", uncertain=method != "GET") from None

    def accounts(self):
        data = self.call("GET", "/integrations")
        if not isinstance(data, list):
            raise SocialError("Postiz returned an unexpected account list.")
        return [{"id": str(x["id"]), "name": str(x.get("name", "Account")),
                 "platform": x["identifier"]} for x in data if isinstance(x, dict)
                and x.get("id") and x.get("identifier") in PLATFORMS and not x.get("disabled")]

    def upload(self, video: Path):
        with video.open("rb") as stream:
            data = self.call("POST", "/upload", files={"file": (video.name, stream, "video/mp4")})
        if not isinstance(data, dict) or not data.get("id") or not safe_url(str(data.get("path", ""))):
            raise SocialError("Postiz did not return a usable media ID and URL.")
        return {"id": str(data["id"]), "path": data["path"]}

    def schedule(self, draft, media):
        platform = draft["platform"]
        settings = {"__type": platform}
        if platform == "youtube":
            settings.update(title=draft["title"], type=draft["visibility"],
                            selfDeclaredMadeForKids=draft["made_for_kids"])
        else:
            settings["post_type"] = "post"
            if platform == "facebook":
                settings["title"] = draft["title"]
        data = self.call("POST", "/posts", json={
            "type": "schedule", "date": draft["scheduled_at"], "shortLink": False, "tags": [],
            "posts": [{"integration": {"id": draft["account_id"]}, "settings": settings,
                       "value": [{"content": draft["caption"], "image": [media]}]}]})
        if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
                or not data[0].get("postId") or data[0].get("integration") != draft["account_id"]):
            raise SocialError("Postiz may have accepted this post, but returned no matching post ID.", uncertain=True)
        return str(data[0]["postId"])

    def posts(self, start, end):
        data = self.call("GET", "/posts", params={"startDate": start, "endDate": end})
        if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
            raise SocialError("Postiz returned an unexpected post list.")
        return data["posts"]

    def analytics(self, post_id):
        return self.call("GET", "/analytics/post/" + quote(post_id, safe=""), params={"date": "30"})

    def cancel(self, post_id):
        return self.call("DELETE", "/posts/" + quote(post_id, safe=""))


def metric_snapshot(raw):
    """Keep provider units and missing values. Never sum cumulative snapshots."""
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("data"), list):
            continue
        points = [p for p in item["data"] if isinstance(p, dict) and p.get("total") is not None]
        if not points:
            continue
        point = max(points, key=lambda p: str(p.get("date", "")))
        try:
            value = float(point["total"])
        except (ValueError, TypeError):
            continue
        import math
        if not math.isfinite(value) or value < 0:
            continue
        result.append({"label": str(item.get("label", "Metric"))[:100],
                       "value": value, "date": str(point.get("date", ""))[:100]})
    return result
