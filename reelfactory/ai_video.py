"""ElevenLabs Image & Video API, independent of narration and rendering.

Contract: https://elevenlabs.io/docs/api-reference/flows/video/create
Creation is never retried automatically: a lost response may still be billed.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
from pathlib import Path
import re
import socket
import subprocess
from urllib.parse import urlsplit

import requests

from .elevenlabs import ElevenLabsError, resolve_keys

MODELS = {"veo-3.1-fast-generate-001": "Veo 3.1 Fast", "veo-3.1-generate-001": "Veo 3.1"}
BASE = "https://api.elevenlabs.io/v1/flows/video"
MAX_VIDEO_BYTES = 96 * 1024 * 1024
MAX_REFERENCE_BYTES = 25 * 1024 * 1024


class VideoError(RuntimeError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def credentials(fingerprint=None):
    try:
        keys = resolve_keys()
    except ElevenLabsError:
        raise VideoError("Add your ElevenLabs API key in API settings to generate AI video.") from None
    for key in keys:
        digest = hashlib.sha256(key.encode()).hexdigest()
        if fingerprint is None or digest == fingerprint:
            return key, digest
    raise VideoError("Restore the ElevenLabs key used for this generation in API settings, then refresh it.")


def settings(values):
    prompt = values.get("prompt", "")
    model = values.get("model", "veo-3.1-fast-generate-001")
    aspect = values.get("aspect", "9:16")
    resolution = values.get("resolution", "720p")
    duration = str(values.get("duration", "6"))
    if not isinstance(prompt, str) or not 5 <= len(prompt.strip()) <= 3000:
        raise VideoError("Describe the clip using 5 to 3000 characters.")
    if model not in tuple(MODELS) or aspect not in ("9:16", "16:9") or resolution not in ("720p", "1080p") or duration not in {"4", "6", "8"}:
        raise VideoError("Choose a supported video model, shape, resolution and duration.")
    return dict(prompt=prompt.strip(), model_id=model, aspect_ratio=aspect, resolution=resolution,
                duration_secs=int(duration), generate_audio=False, enhance_prompt=False)


def image_reference(path):
    """Use a product picture or a clip's opening frame as the visual anchor."""
    path = Path(path)
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower())
    if mime:
        if path.stat().st_size > MAX_REFERENCE_BYTES:
            raise VideoError("Choose a reference picture smaller than 25 MB.")
        content = path.read_bytes()
    else:
        try:
            result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
                                     "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                                    capture_output=True, check=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise VideoError("Could not read a reference frame. Choose a picture or check FFmpeg.") from None
        content, mime = result.stdout, "image/jpeg"
    if not content or len(content) > MAX_REFERENCE_BYTES:
        raise VideoError("Choose a nonempty reference picture smaller than 25 MB.")
    return dict(type="inline_base64", content_base64=base64.b64encode(content).decode("ascii"), mime_type=mime)


def _call(method, url, key, **kwargs):
    creating = method == "POST"
    try:
        response = requests.request(method, url, headers={"xi-api-key": key},
                                    timeout=(10, 90 if creating else 30), allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise VideoError("ElevenLabs did not confirm the request. Refresh an existing generation; check ElevenLabs history before submitting another.",
                         uncertain=creating) from None
    with response:
        if not 200 <= response.status_code < 300:
            hint = {401: "Check your ElevenLabs API key.", 402: "Check your paid plan and video credits.",
                    403: "Check Image & Video API access and key permissions.",
                    404: "Check that this video model is available to your account.",
                    422: "Check the prompt, reference and selected video settings.",
                    429: "Your rate limit or quota was reached. Wait before generating again."}.get(response.status_code, "Refresh later and check ElevenLabs history.")
            raise VideoError(f"ElevenLabs video returned HTTP {response.status_code}. {hint}",
                             uncertain=creating and (response.status_code >= 500 or response.status_code == 408))
        try:
            data = response.json()
        except ValueError:
            raise VideoError("ElevenLabs returned an unreadable video response. Check its history before generating again.", uncertain=creating) from None
    if not isinstance(data, dict):
        raise VideoError("ElevenLabs returned an unexpected video response.", uncertain=creating)
    return data


def create(payload, key):
    data = _call("POST", BASE, key, json=payload)
    if not isinstance(data.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", data['id']) or data.get("status") != "pending":
        raise VideoError("ElevenLabs did not return a generation ID. Check its history before generating again.", uncertain=True)
    return data["id"]


def status(generation_id, key):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(generation_id)):
        raise VideoError("This generation has an invalid provider ID.")
    data = _call("GET", f"{BASE}/{generation_id}", key)
    if data.get("id") != generation_id or data.get("status") not in ("pending", "generating", "completed", "failed"):
        raise VideoError("ElevenLabs returned an unexpected generation status. Refresh later.")
    if data["status"] == "failed":
        reason = data.get("failure_reason")
        hint = {"moderated": "Revise the prompt or reference.", "charging_failed": "Check your ElevenLabs credits.",
                "invalid_parameters": "Check the model settings and reference."}.get(reason, "Try another prompt or refresh your ElevenLabs history.")
        return dict(status="failed", error="AI video generation failed. " + hint)
    if data["status"] == "completed" and (data.get("content_mime_type") != "video/mp4" or not isinstance(data.get("content_url"), str) or not data['content_url']):
        raise VideoError("ElevenLabs returned no usable MP4. Refresh to retrieve the result again.")
    return data


def download(url, destination):
    """Download a bounded public HTTPS result without sending API credentials."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError
    except (ValueError, OSError):
        raise VideoError("ElevenLabs returned an invalid download address. Refresh this generation.") from None
    try:
        with requests.get(url, stream=True, timeout=(10, 120), allow_redirects=False) as response:
            if response.status_code != 200 or not response.headers.get("Content-Type", "").split(";", 1)[0].lower() in {"video/mp4", "application/octet-stream"}:
                raise VideoError("Could not download the generated MP4. Refresh this generation without generating a new clip.")
            if int(response.headers.get("Content-Length", "0")) > MAX_VIDEO_BYTES:
                raise VideoError("The generated clip exceeds the 96 MB import limit.")
            size = 0
            with Path(destination).open("wb") as stream:
                for chunk in response.iter_content(256 * 1024):
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        raise VideoError("The generated clip exceeds the 96 MB import limit.")
                    stream.write(chunk)
            if not size:
                raise VideoError("The generated clip is empty. Refresh this generation.")
    except (requests.RequestException, ValueError):
        raise VideoError("Video download failed. Refresh this generation; a new generation is not needed.") from None
