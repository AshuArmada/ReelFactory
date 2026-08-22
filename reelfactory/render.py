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

from . import templates

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

# How much of a photo a centre crop is allowed to throw away. A portrait photo
# in a 9:16 frame loses a harmless sliver; a landscape phone shot loses well
# over half its width, which is usually exactly where the product is. Past this
# limit we crop only as far as the budget allows and fill the leftover with a
# blurred copy of the photo, so nothing is silently cut off.
MAX_CROP_LOSS = 0.35


class RenderError(RuntimeError):
    pass


@dataclass
class Shot:
    photo: Path
    duration: float     # on-screen length including its share of the cross-fade


def plan(durations, pause: float, xfade: float = XFADE):
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
        shots.append(round(span + xfade, 3))
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
    scrim_color: str = "#0B0B0F",
    fonts_dir: str | None = None,
    crf: int = 20,
    preset: str = "medium",
    template=None,
) -> Path:
    _require("ffmpeg")
    tpl = template or templates.Template()
    width, height = size
    workdir.mkdir(parents=True, exist_ok=True)
    clips = [
        _render_shot(s, i, width, height, workdir, tpl)
        for i, s in enumerate(shots)
    ]
    return _compose(
        clips, [s.duration for s in shots], subtitle_file, voice_track, outfile,
        width, height, workdir, logo, music, music_volume, fonts_dir, crf, preset,
        tpl, scrim_color,
    )


# --------------------------------------------------------------------------- pass 1


def _render_shot(shot: Shot, idx: int, w: int, h: int, workdir: Path, tpl) -> Path:
    dest = workdir / f"shot{idx:02d}.mp4"
    frames = max(2, int(round(shot.duration * FPS)))
    # Oversample before zoompan; it works on integer pixels, so a bigger canvas
    # is what keeps slow moves from stepping visibly.
    ow, oh = w * 2, h * 2
    z, x, y = _motion(tpl.move_for(idx), frames, tpl.zoom)

    chain = (
        f"{_framing(shot.photo, ow, oh, w, h, tpl.crop_budget)},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS}"
    )
    if tpl.grade:
        chain += f",{tpl.grade}"
    chain += ",format=yuv420p,setsar=1"
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


_CROP = "scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh}"


def _framing(photo: Path, ow: int, oh: int, w: int, h: int,
             budget: float = MAX_CROP_LOSS) -> str:
    """How one photo is fitted to the frame.

    A plain centre crop when little would be lost. Otherwise crop only as far
    as `budget` allows -- which keeps the subject big -- and let a blurred,
    darkened copy of the photo fill whatever gap is left, instead of cutting
    further into the picture.
    """
    size = _probe_size(photo)
    if not size:
        return _CROP.format(ow=ow, oh=oh)     # unreadable: behave as before
    pw, ph = size
    src, dst = pw / ph, w / h
    if 1.0 - min(src, dst) / max(src, dst) <= budget:
        return _CROP.format(ow=ow, oh=oh)

    # The shape to crop to: as close to the frame as the budget reaches.
    mid = (max(dst, src * (1.0 - budget)) if src > dst
           else min(dst, src / (1.0 - budget)))
    if src > mid:
        cw, ch = int(round(ph * mid)), ph      # too wide: trim the sides
    else:
        cw, ch = pw, int(round(pw / mid))      # too tall: trim top and bottom
    cw, ch = max(2, min(cw, pw)), max(2, min(ch, ph))

    # The backdrop is blurred at an eighth size and scaled back up: same look
    # as blurring at full resolution, a fraction of the work.
    bw, bh = max(2, ow // 8), max(2, oh // 8)
    return (
        "split=2[fill_src][fit_src];"
        f"[fill_src]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
        f"crop={bw}:{bh},boxblur=6:2,scale={ow}:{oh},"
        "eq=brightness=-0.14:saturation=0.70[fill_bg];"
        f"[fit_src]crop={cw}:{ch},"
        f"scale={ow}:{oh}:force_original_aspect_ratio=decrease[fit_fg];"
        "[fill_bg][fit_fg]overlay=(W-w)/2:(H-h)/2"
    )


_sizes: dict = {}


def _probe_size(photo: Path):
    key = str(photo)
    if key not in _sizes:
        try:
            out = _run([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(photo),
            ], what=f"reading the dimensions of {photo.name}")
            pw, ph = (int(v) for v in out.strip().splitlines()[0].split("x")[:2])
            _sizes[key] = (pw, ph) if pw and ph else None
        except (RenderError, ValueError, IndexError):
            _sizes[key] = None
    return _sizes[key]


def _motion(move: str, frames: int, travel: float = 0.30):
    """Return zoompan z/x/y expressions for a named camera move."""
    step = travel / max(frames, 1)
    top = 1.0 + travel
    zin = f"min(1.0+{step:.6f}*on,{top:.3f})"
    zout = f"max({top:.3f}-{step:.6f}*on,1.0)"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if move == "in_center":
        return zin, cx, cy
    if move == "out_center":
        return zout, cx, cy
    if move == "in_left":
        return zin, "0", cy
    if move == "in_right":
        return zin, "iw-(iw/zoom)", cy
    pan = f"{1.0 + travel * 0.6:.3f}"     # pans hold a steadier zoom than they travel
    if move == "pan_right":
        return pan, f"(iw-(iw/zoom))*on/{max(frames-1,1)}", cy
    if move == "pan_left":
        return pan, f"(iw-(iw/zoom))*(1-on/{max(frames-1,1)})", cy
    return zin, cx, cy


# --------------------------------------------------------------------------- pass 2


def _make_scrim(w: int, h: int, workdir: Path, strength: float, color: str) -> Path:
    """A soft gradient over the lower half so text stays legible.

    `strength` is how dark it gets at the very bottom, `color` what it darkens
    towards -- the brand's secondary colour, so the backdrop belongs to the
    brand rather than always being flat black.
    """
    r, g, b = _rgb(color)
    peak = max(0, min(255, int(round(255 * strength))))
    dest = workdir / f"scrim_{w}x{h}_{peak}_{r:02x}{g:02x}{b:02x}.png"
    if dest.exists():
        return dest
    # The backslash escapes the comma for ffmpeg's expression parser, so this
    # must stay a raw string -- '\,' is not a Python escape and warns without it.
    alpha = rf"if(lt(Y,H*0.46),0,{peak}*pow((Y-H*0.46)/(H*0.54)\,1.6))"
    _run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}",
        "-vf", f"format=rgba,geq=r={r}:g={g}:b={b}:a='{alpha}'",
        "-frames:v", "1", str(dest),
    ], what="building the text backdrop")
    return dest


def _rgb(hex_rgb: str):
    """'#RRGGBB' -> (r, g, b), falling back to near-black on anything odd."""
    h = str(hex_rgb or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0, 0, 0)


def _compose(
    clips, durations, subtitle_file, voice_track, outfile,
    w, h, workdir, logo, music, music_volume, fonts_dir, crf, preset,
    tpl, scrim_color,
):
    xfade = tpl.transition_seconds
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
    if tpl.scrim > 0:
        scrim_idx = next_idx
        next_idx += 1
        inputs += ["-i", _make_scrim(w, h, workdir, tpl.scrim, scrim_color).name]
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
            offset += durations[i - 1] - xfade
            label = "[vid]" if i == len(clips) - 1 else f"[x{i}]"
            kind = tpl.transition_for(i - 1)
            filters.append(
                f"{prev}[{i}:v]xfade=transition={kind}:duration={xfade}"
                f":offset={offset:.3f}{label}"
            )
            prev = label
        total = sum(durations) - xfade * (len(clips) - 1)

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
