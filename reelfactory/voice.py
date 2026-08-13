"""Text-to-speech for the ad script.

Each segment is synthesised separately so its exact duration is known; that
duration then drives how long the matching photo stays on screen and when the
subtitle appears. Backends:

  edge   - Microsoft Edge neural voices. Free, no API key, needs internet.
  gtts   - Google Translate TTS. Free, needs internet. Flatter delivery.
  silent - Estimated-length silence. For testing the render with no internet.
"""
from __future__ import annotations

import asyncio
import base64
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from . import gemini

# Words per second used only by the 'silent' backend to fake a realistic pace.
WPS = {"hi": 2.6, "en": 2.9}
MIN_SEG = 1.6      # seconds; nothing on screen for less than this
PAUSE = 0.28       # default gap after a segment, when the role is not known

# How long to hold *after* each kind of line. A single gap everywhere reads
# like a list being recited: the hook gets no room to land, and the benefit
# lines -- which should rattle along -- sit as far apart as the punchline.
#
# A beat that asks the viewer to take something in (the hook, the closing
# instruction) is followed by a longer silence; the middle of the ad, where
# the job is to keep moving, is followed by a short one.
PAUSE_BY_ROLE = {
    "hook":    0.60,   # the whole reel depends on this line landing
    "reveal":  0.45,
    "usp":     0.30,   # benefits should feel like a run, not a list
    "proof":   0.40,
    "price":   0.50,   # a number needs a moment to register
    "offer":   0.50,
    "urgency": 0.45,
    "cta":     0.35,   # trailing gap before the video ends
    "custom":  PAUSE,
}


def pauses_for(roles, default: float = PAUSE) -> list:
    """The gap to leave after each segment, from its role."""
    return [PAUSE_BY_ROLE.get(r, default) for r in roles]


def _gap(pause, i: int) -> float:
    """`pause` may be one number for every gap, or one per segment."""
    if isinstance(pause, (int, float)):
        return float(pause)
    return float(pause[i]) if i < len(pause) else PAUSE

DEFAULT_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_GEMINI_VOICE = "Kore"


@dataclass
class Clip:
    path: Path
    duration: float   # speech only, excluding the trailing pause


class TTSError(RuntimeError):
    pass


def synthesize(
    lines,
    lang: str,
    voice: str,
    rate: str,
    outdir: Path,
    backend: str = "edge",
    *,
    gemini_voice: str | None = None,
    gemini_model: str | None = None,
    gemini_key: str | None = None,
    gemini_backup_key: str | None = None,
):
    """Render one audio file per line. Returns list[Clip] in the same order."""
    outdir.mkdir(parents=True, exist_ok=True)
    if backend == "edge":
        paths = _edge(lines, voice, rate, outdir)
    elif backend == "gtts":
        paths = _gtts(lines, lang, outdir)
    elif backend == "silent":
        paths = _silent(lines, lang, outdir)
    elif backend == "gemini":
        paths = _gemini(
            lines, outdir,
            voice=gemini_voice or DEFAULT_GEMINI_VOICE,
            model=gemini_model or DEFAULT_GEMINI_TTS_MODEL,
            api_key=gemini_key,
            backup_key=gemini_backup_key,
        )
    else:
        raise TTSError(f"Unknown TTS backend {backend!r}. Use edge, gtts, gemini or silent.")
    return [Clip(p, max(MIN_SEG, probe_duration(p))) for p in paths]


def concat(clips, outfile: Path, pause=PAUSE) -> Path:
    """Join clips into one WAV with a pause between each.

    `pause` is either one number used after every clip, or one number per
    clip (see `pauses_for`). It must match whatever `render.plan` is given,
    or the pictures drift out of step with the voice.
    """
    args = ["ffmpeg", "-y", "-v", "error"]
    for c in clips:
        args += ["-i", str(c.path)]
    n = len(clips)
    filt = []
    labels = []
    for i in range(n):
        filt.append(f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=mono[s{i}]")
        labels.append(f"[s{i}]")
        if i < n - 1:
            filt.append(f"aevalsrc=0:d={_gap(pause, i)}:s=44100:c=mono[p{i}]")
            labels.append(f"[p{i}]")
    filt.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]")
    args += ["-filter_complex", ";".join(filt), "-map", "[out]", "-ar", "44100", "-ac", "1", str(outfile)]
    _run(args)
    return outfile


def probe_duration(path) -> float:
    out = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ])
    try:
        return float(out.strip())
    except ValueError:
        raise TTSError(f"Could not read the duration of {path}; the audio file looks empty.")


# --------------------------------------------------------------------------- backends


def _edge(lines, voice: str, rate: str, outdir: Path):
    try:
        import edge_tts
    except ImportError as exc:
        raise TTSError(
            "edge-tts is not installed. Run:  pip install edge-tts\n"
            "Or build with --tts silent to test the video without a voiceover."
        ) from exc

    async def run():
        paths = []
        for i, line in enumerate(lines):
            dest = outdir / f"seg{i:02d}.mp3"
            comm = edge_tts.Communicate(line, voice, rate=rate)
            await comm.save(str(dest))
            if not dest.exists() or dest.stat().st_size == 0:
                raise TTSError(f"Edge TTS returned no audio for: {line!r}")
            paths.append(dest)
        return paths

    try:
        return asyncio.run(run())
    except TTSError:
        raise
    except Exception as exc:
        raise TTSError(
            f"Edge TTS failed ({exc.__class__.__name__}: {exc}). It needs an internet "
            "connection. Check the voice name is valid — list them with:  edge-tts --list-voices"
        ) from exc


def _gtts(lines, lang: str, outdir: Path):
    try:
        from gtts import gTTS
    except ImportError as exc:
        raise TTSError("gTTS is not installed. Run:  pip install gTTS") from exc
    paths = []
    for i, line in enumerate(lines):
        dest = outdir / f"seg{i:02d}.mp3"
        try:
            gTTS(text=line, lang=lang, tld="co.in").save(str(dest))
        except Exception as exc:
            raise TTSError(f"gTTS failed on segment {i}: {exc}") from exc
        paths.append(dest)
    return paths


def _gemini(lines, outdir: Path, voice: str, model: str, api_key: str | None, backup_key: str | None = None):
    try:
        key = gemini.resolve_key(api_key)
        backup = gemini.resolve_backup_key(backup_key)
    except gemini.GeminiError as exc:
        raise TTSError(str(exc)) from exc

    paths = []
    for i, line in enumerate(lines):
        payload = {
            "contents": [{"parts": [{"text": line}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        try:
            data = gemini.generate_content(model, key, payload, backup_key=backup)
            part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
            pcm = base64.b64decode(part["data"])
        except gemini.GeminiError as exc:
            raise TTSError(str(exc)) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise TTSError(f"Gemini TTS returned no audio for: {line!r}") from exc
        if not pcm:
            raise TTSError(f"Gemini TTS returned empty audio for: {line!r}")

        dest = outdir / f"seg{i:02d}.wav"
        _write_wav(dest, pcm, sample_rate=_pcm_rate(part.get("mimeType", "")))
        paths.append(dest)
    return paths


def _pcm_rate(mime_type: str) -> int:
    m = re.search(r"rate=(\d+)", mime_type)
    return int(m.group(1)) if m else 24000


def _write_wav(path: Path, pcm_bytes: bytes, sample_rate: int, channels: int = 1, sample_width: int = 2) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)


def _silent(lines, lang: str, outdir: Path):
    paths = []
    wps = WPS.get(lang, 2.8)
    for i, line in enumerate(lines):
        secs = max(MIN_SEG, round(len(line.split()) / wps + 0.5, 2))
        dest = outdir / f"seg{i:02d}.wav"
        _run([
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=mono", "-t", str(secs), str(dest),
        ])
        paths.append(dest)
    return paths


# --------------------------------------------------------------------------- util


def _run(args, timeout: int = 120) -> str:
    if shutil.which(args[0]) is None:
        raise TTSError(
            f"'{args[0]}' was not found. Install FFmpeg and make sure it is on your PATH."
        )
    # These calls (ffprobe, silence generation, concatenating already-short
    # voice clips) are all normally sub-second; a generous but finite bound
    # still catches a genuinely stuck process instead of hanging forever.
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise TTSError(f"'{args[0]}' did not finish within {timeout}s and was stopped.") from exc
    if proc.returncode != 0:
        raise TTSError(f"{args[0]} failed:\n{proc.stderr.strip()[:800]}")
    return proc.stdout
