"""FFmpeg render pipeline: photos + voiceover -> a finished social video.

Two passes. Pass one turns each photo into a moving shot of exactly the right
length. Pass two cross-fades the shots together, lays on the logo and burned-in
text, and mixes the voiceover over ducked background music.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FPS = 30
XFADE = 0.5          # seconds of cross-dissolve between shots
TAIL = 0.9           # extra seconds held on the final CTA shot
LEAD = 0.05          # subtitle appears a beat before the voice

ASPECTS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "16:9": (1920, 1080),
}

# Ken Burns moves, cycled across shots so consecutive photos never match.
MOVES = ["in_center", "out_center", "in_left", "pan_right", "in_right", "pan_left"]


class RenderError(RuntimeError):
    pass


@dataclass
class Shot:
    photo: Path
    duration: float     # on-screen length including its share of the cross-fade


def plan(durations, pause: float):
    """Given per-segment speech lengths, return shot lengths and subtitle windows.

    Each shot is padded by one transition length so that, after cross-fading,
    shot i starts exactly when segment i's speech starts in the audio track.

    Timings are (start, end, speech_start): the first two are when the text is
    on screen, the third is when this segment's audio actually begins. Word-level
    captions are timed from speech_start, so it cannot be inferred from `start`
    -- that one is nudged early by LEAD, and clamped at zero for the first shot.
    """
    shots, timings, cursor = [], [], 0.0
    n = len(durations)
    for i, d in enumerate(durations):
        span = d + pause + (TAIL if i == n - 1 else 0.0)
        shots.append(round(span + XFADE, 3))
        timings.append((
            round(max(0.0, cursor - LEAD), 3),
            round(cursor + span, 3),
            round(cursor, 3),
        ))
        cursor += span
    return shots, timings


def render(
    shots,
    subtitle_file: Path,
    voice_track: Path,
    outfile: Path,
    size,
    workdir: Path,
    logo: str | None = None,
    music: str | None = None,
    music_volume: float = 0.12,
    letterbox_color: str = "#0B0B0F",
    scrim: bool = True,
    fonts_dir: str | None = None,
    crf: int = 20,
    preset: str = "medium",
) -> Path:
    _require("ffmpeg")
    width, height = size
    workdir.mkdir(parents=True, exist_ok=True)
    clips = [
        _render_shot(s, i, width, height, workdir, letterbox_color)
        for i, s in enumerate(shots)
    ]
    return _compose(
        clips, [s.duration for s in shots], subtitle_file, voice_track, outfile,
        width, height, workdir, logo, music, music_volume, fonts_dir, crf, preset, scrim,
    )


# --------------------------------------------------------------------------- pass 1


def _render_shot(shot: Shot, idx: int, w: int, h: int, workdir: Path, bg: str) -> Path:
    dest = workdir / f"shot{idx:02d}.mp4"
    frames = max(2, int(round(shot.duration * FPS)))
    # Oversample before zoompan; it works on integer pixels, so a bigger canvas
    # is what keeps slow moves from stepping visibly.
    ow, oh = w * 2, h * 2
    move = MOVES[idx % len(MOVES)]
    z, x, y = _motion(move, frames)

    chain = (
        f"scale={ow}:{oh}:force_original_aspect_ratio=increase,"
        f"crop={ow}:{oh},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS},"
        f"format=yuv420p,setsar=1"
    )
    if idx == 0:
        chain += ",fade=t=in:st=0:d=0.25"

    _run([
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(shot.photo),
        "-vf", chain,
        "-t", f"{shot.duration:.3f}",
        "-r", str(FPS), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
        "-pix_fmt", "yuv420p", str(dest),
    ], what=f"rendering shot {idx + 1} from {shot.photo.name}")
    return dest


def _motion(move: str, frames: int):
    """Return zoompan z/x/y expressions for a named camera move."""
    step = 0.30 / max(frames, 1)          # total zoom travel of 30%
    zin = f"min(1.0+{step:.6f}*on,1.30)"
    zout = f"max(1.30-{step:.6f}*on,1.0)"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if move == "in_center":
        return zin, cx, cy
    if move == "out_center":
        return zout, cx, cy
    if move == "in_left":
        return zin, "0", cy
    if move == "in_right":
        return zin, "iw-(iw/zoom)", cy
    if move == "pan_right":
        return "1.18", f"(iw-(iw/zoom))*on/{max(frames-1,1)}", cy
    if move == "pan_left":
        return "1.18", f"(iw-(iw/zoom))*(1-on/{max(frames-1,1)})", cy
    return zin, cx, cy


# --------------------------------------------------------------------------- pass 2


def _make_scrim(w: int, h: int, workdir: Path) -> Path:
    """A soft black gradient over the lower half so text stays legible."""
    dest = workdir / f"scrim_{w}x{h}.png"
    if dest.exists():
        return dest
    # The backslash escapes the comma for ffmpeg's expression parser, so this
    # must stay a raw string -- '\,' is not a Python escape and warns without it.
    alpha = r"if(lt(Y,H*0.46),0,200*pow((Y-H*0.46)/(H*0.54)\,1.6))"
    _run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}",
        "-vf", f"format=rgba,geq=r=0:g=0:b=0:a='{alpha}'",
        "-frames:v", "1", str(dest),
    ], what="building the text backdrop")
    return dest


def _compose(
    clips, durations, subtitle_file, voice_track, outfile,
    w, h, workdir, logo, music, music_volume, fonts_dir, crf, preset, scrim,
):
    inputs, filters = [], []
    for c in clips:
        inputs += ["-i", c.name]
    voice_idx = len(clips)
    inputs += ["-i", Path(voice_track).name]
    music_idx = None
    if music:
        music_idx = len(clips) + 1
        inputs += ["-stream_loop", "-1", "-i", str(music)]
    next_idx = len(clips) + 1 + (1 if music else 0)
    scrim_idx = None
    if scrim:
        scrim_idx = next_idx
        next_idx += 1
        inputs += ["-i", _make_scrim(w, h, workdir).name]
    logo_idx = None
    if logo:
        logo_idx = next_idx
        inputs += ["-i", str(logo)]

    # Cross-fade the shots into one continuous stream.
    if len(clips) == 1:
        filters.append("[0:v]null[vid]")
        total = durations[0]
    else:
        prev, offset = "[0:v]", 0.0
        for i in range(1, len(clips)):
            offset += durations[i - 1] - XFADE
            label = "[vid]" if i == len(clips) - 1 else f"[x{i}]"
            filters.append(
                f"{prev}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}{label}"
            )
            prev = label
        total = sum(durations) - XFADE * (len(clips) - 1)

    stage = "[vid]"
    if scrim_idx is not None:
        filters.append(f"{stage}[{scrim_idx}:v]overlay=0:0:format=auto[scr]")
        stage = "[scr]"
    if logo_idx is not None:
        filters.append(f"[{logo_idx}:v]scale={int(w*0.20)}:-1[lg]")
        filters.append(f"{stage}[lg]overlay=x={int(w*0.055)}:y={int(h*0.045)}[wm]")
        stage = "[wm]"

    sub = Path(subtitle_file).name
    subs = f"subtitles={sub}"
    if fonts_dir:
        subs += f":fontsdir={_esc(fonts_dir)}"
    filters.append(f"{stage}{subs},fade=t=out:st={max(0.0, total-0.45):.3f}:d=0.45[vout]")

    # Voice on top, music underneath, ducked whenever the voice is speaking.
    afmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    filters.append(
        f"[{voice_idx}:a]aresample=44100,{afmt},apad,atrim=0:{total:.3f},asetpts=N/SR/TB[vo]"
    )
    if music_idx is not None:
        filters.append(
            f"[{music_idx}:a]aresample=44100,{afmt},volume={music_volume},"
            f"atrim=0:{total:.3f},asetpts=N/SR/TB,afade=t=out:st={max(0.0,total-1.2):.3f}:d=1.2[bg]"
        )
        filters.append("[vo]asplit=2[vo1][vokey]")
        filters.append(
            "[bg][vokey]sidechaincompress=threshold=0.03:ratio=9:attack=6:release=320,"
            f"{afmt}[bgduck]"
        )
        filters.append("[vo1][bgduck]amix=inputs=2:normalize=0:duration=first[aout]")
    else:
        filters.append("[vo]anull[aout]")

    args = ["ffmpeg", "-y", "-v", "error", *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-g", str(FPS * 2), "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-t", f"{total:.3f}", str(Path(outfile).resolve())]
    _run(args, cwd=workdir, what="composing the final video")
    return Path(outfile)


# --------------------------------------------------------------------------- util


def _esc(path: str) -> str:
    return str(path).replace("\\", "/").replace(":", "\\\\:")


def _require(binary: str):
    if shutil.which(binary) is None:
        raise RenderError(
            f"'{binary}' was not found on your PATH.\n"
            "Windows:  winget install Gyan.FFmpeg   (then reopen the terminal)\n"
            "macOS:    brew install ffmpeg"
        )


def _run(args, cwd=None, what: str = "running ffmpeg"):
    proc = subprocess.run(args, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RenderError(f"FFmpeg failed while {what}:\n{proc.stderr.strip()[:1500]}")
    return proc.stdout
