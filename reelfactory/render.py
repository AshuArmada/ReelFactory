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

# Pass one renders each shot on a canvas this many times the output size, so
# that zoompan -- which works on integer pixels -- has room to move smoothly.
# It is also what decides how big a photo has to be to stay sharp: see
# photo_notes().
OVERSAMPLE = 2
MAX_ZOOM = 1.30      # the furthest any Ken Burns move pushes in
PAN_ZOOM = 1.18      # the fixed zoom the two panning moves sit at

# Pass one's output is not a deliverable -- pass two re-encodes it -- so it is
# kept near-transparent. Compressing it hard was a false economy: pass two
# then spent its bitrate faithfully reproducing the first encoder's artefacts.
# veryfast rather than ultrafast because ultrafast needs far more bits for the
# same quality, and these intermediates are written for every shot.
SHOT_CRF = 12
SHOT_PRESET = "veryfast"

# A preset alone does NOT change how good the picture looks: at a fixed CRF a
# slower preset spends longer finding a *smaller* file of the same quality.
# The UI has always described the slow end as "best picture", so each preset
# is paired with a CRF that makes that true.
PRESET_CRF = {
    "ultrafast": 23,
    "veryfast": 21,
    "faster": 20,
    "medium": 18,
    "slow": 16,
}
DEFAULT_CRF = 18

# A hair of temporal noise, added last. The text scrim is a smooth black
# gradient over ~half the frame, which is exactly what 8-bit video bands on --
# visible stair-steps across an evenly lit photo. Noise this faint is
# invisible in itself and breaks the steps up.
GRAIN = 2

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
    """
    shots, timings, cursor = [], [], 0.0
    n = len(durations)
    for i, d in enumerate(durations):
        span = d + pause + (TAIL if i == n - 1 else 0.0)
        shots.append(round(span + XFADE, 3))
        timings.append((round(max(0.0, cursor - LEAD), 3), round(cursor + span, 3)))
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
    crf: int | None = None,
    preset: str = "medium",
) -> Path:
    _require("ffmpeg")
    width, height = size
    # An explicit crf always wins; otherwise it follows the preset, so that
    # asking for a slower render actually returns a better-looking video
    # rather than the same picture in a smaller file.
    crf = PRESET_CRF.get(preset, DEFAULT_CRF) if crf is None else crf
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
    ow, oh = w * OVERSAMPLE, h * OVERSAMPLE
    move = MOVES[idx % len(MOVES)]
    z, x, y = _motion(move, frames)

    # out_range=tv is not cosmetic. JPEGs decode as full-range (yuvj420p), and
    # without an explicit conversion that range travels all the way into the
    # finished file, which then plays back washed out -- lifted blacks, flat
    # whites -- on anything that assumes broadcast range, as most players and
    # platforms do. Converting here, once, is the only place it can be done
    # while the pixels are still at full size.
    chain = (
        f"scale={ow}:{oh}:force_original_aspect_ratio=increase,"
        f"crop={ow}:{oh},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS},"
        f"scale={w}:{h}:out_range=tv,format=yuv420p,setsar=1"
    )
    if idx == 0:
        chain += ",fade=t=in:st=0:d=0.25"

    _run([
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(shot.photo),
        "-vf", chain,
        "-t", f"{shot.duration:.3f}",
        "-r", str(FPS), "-c:v", "libx264", "-preset", SHOT_PRESET, "-crf", str(SHOT_CRF),
        "-pix_fmt", "yuv420p", "-color_range", "tv", str(dest),
    ], what=f"rendering shot {idx + 1} from {shot.photo.name}", timeout=120)
    return dest


def _motion(move: str, frames: int):
    """Return zoompan z/x/y expressions for a named camera move."""
    step = (MAX_ZOOM - 1.0) / max(frames, 1)
    zin = f"min(1.0+{step:.6f}*on,{MAX_ZOOM})"
    zout = f"max({MAX_ZOOM}-{step:.6f}*on,1.0)"
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
        return str(PAN_ZOOM), f"(iw-(iw/zoom))*on/{max(frames-1,1)}", cy
    if move == "pan_left":
        return str(PAN_ZOOM), f"(iw-(iw/zoom))*(1-on/{max(frames-1,1)})", cy
    return zin, cx, cy


# --------------------------------------------------------------------------- pass 2


def _make_scrim(w: int, h: int, workdir: Path) -> Path:
    """A soft black gradient over the lower half so text stays legible."""
    dest = workdir / f"scrim_{w}x{h}.png"
    if dest.exists():
        return dest
    # Raw string: the backslash escapes the comma for ffmpeg's expression
    # parser, and is not a Python escape at all. Spelled "\," it happened to
    # survive only because Python leaves unknown escapes alone -- which is a
    # SyntaxWarning today and an error in a future version.
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
    # Grain goes on last, after the scrim and the text, because the banding it
    # is there to hide is created by those overlays rather than by the photo.
    grain = f",noise=alls={GRAIN}:allf=t" if GRAIN else ""
    filters.append(
        f"{stage}{subs}{grain},fade=t=out:st={max(0.0, total-0.45):.3f}:d=0.45[vout]"
    )

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
            # Tagged as well as converted, so a player has nothing to guess at.
            "-color_range", "tv",
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


def probe_photos(photos) -> dict:
    """{path: (width, height)} for every photo, raising on any that isn't one.

    Photos are only ever discovered by file extension (config.Product.load),
    never opened -- so a corrupt or truncated file otherwise only surfaces as
    a cryptic ffmpeg failure deep into pass 1 of the render, several minutes
    and possibly a paid TTS call later, with no indication of which photo was
    at fault. ffprobe (installed alongside ffmpeg, so this adds no new
    dependency) can check every photo in well under a second each.

    The sizes are returned rather than discarded because they are also what
    photo_notes() needs to say whether a photo is big enough to look good.
    """
    _require("ffprobe")
    sizes, bad = {}, []
    for p in photos:
        try:
            out = _run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", str(p)],
                what=f"checking {p.name}", timeout=15,
            )
        except RenderError:
            out = ""
        # ffprobe exits 0 and prints "0,0" for a file it could not decode at
        # all (e.g. a text file with a .jpg extension) rather than failing --
        # a non-empty check alone would let that straight through.
        parts = out.strip().split(",")
        valid = len(parts) == 2 and all(part.isdigit() and int(part) > 0 for part in parts)
        if valid:
            sizes[p] = (int(parts[0]), int(parts[1]))
        else:
            bad.append(p.name)
    if bad:
        lead = ("This photo is not a readable image" if len(bad) == 1
                else "These photos are not readable images")
        raise RenderError(
            f"{lead}, and would only fail partway through rendering instead of "
            f"here: {', '.join(bad)}.\n"
            "Check the file opens normally in an image viewer, or replace/remove it."
        )
    return sizes


def validate_photos(photos) -> None:
    """Raise unless every photo is a real, decodable image."""
    probe_photos(photos)


@dataclass
class PhotoNote:
    """What a photo will look like once the render has had it."""
    name: str
    width: int
    height: int
    upscale: float   # 1.0 = pixel perfect; 2.0 = blown up to twice its size
    kept: float      # fraction of the photo left after the crop, 1.0 = all
    problems: list   # plain-language sentences, worst first; empty if fine

    @property
    def ok(self) -> bool:
        return not self.problems


def photo_notes(sizes: dict, size=(1080, 1920), min_kept: float = 0.68,
                max_upscale: float = 1.15) -> list:
    """Judge each photo against the shape and size the render actually needs.

    Two separate things go wrong, and a photo can suffer both at once:

    * **Upscaling.** Pass one covers a canvas OVERSAMPLE times the output and
      then Ken Burns pushes in as far as MAX_ZOOM, so a photo is only ever
      truly sharp if it has MAX_ZOOM times the output's pixels along its
      tightest dimension. Below that it is being enlarged, and enlarging is
      what "the video looks blurry" almost always turns out to be.

    * **Cropping.** The shot is a centre crop that *covers* the frame, so
      anything not matching the target shape loses its edges. A landscape
      photo in a 9:16 reel keeps about a third of its width -- and whatever
      was at the sides is simply not in the video.

    `min_kept` is set below what a 4:5 or 3:4 photo keeps against a reel
    (0.70 and 0.75), because those are simply what a phone held upright
    produces -- warning about every one of them would be noise nobody reads.
    Square (0.56) and any landscape shape fall well under it.
    """
    w_out, h_out = size
    target_ar = w_out / h_out
    notes = []
    for path, (w, h) in sizes.items():
        # Scale needed to cover the frame; the same figure, before
        # oversampling cancels out, is how much the output is enlarged.
        upscale = max(w_out / w, h_out / h) * MAX_ZOOM
        source_ar = w / h
        kept = (target_ar / source_ar) if source_ar > target_ar else (source_ar / target_ar)

        problems = []
        if kept < min_kept:
            side = "sides" if source_ar > target_ar else "top and bottom"
            problems.append(
                f"Only {kept * 100:.0f}% of it fits this shape — the {side} get cut off. "
                f"Crop it yourself first if something important is there."
            )
        if upscale > max_upscale:
            need_w, need_h = int(w_out * MAX_ZOOM), int(h_out * MAX_ZOOM)
            problems.append(
                f"It has to be blown up {upscale:.1f}× to fill the frame, which looks "
                f"soft. {need_w}×{need_h} or bigger stays sharp."
            )
        notes.append(PhotoNote(path.name, w, h, round(upscale, 2), round(kept, 3), problems))
    return notes


def photo_advice(photos, size=(1080, 1920)) -> list:
    """photo_notes() straight from the files. Returns [] if ffmpeg is missing
    rather than raising -- this is advice, and a page that shows it must still
    render on a machine that cannot probe anything."""
    try:
        return photo_notes(probe_photos(photos), size)
    except (RenderError, OSError):
        return []


def _run(args, cwd=None, what: str = "running ffmpeg", timeout: int = 600):
    # A stuck ffmpeg process (a corrupt photo, an exotic codec, a filter that
    # never terminates) would otherwise hang the build forever with no way to
    # recover short of killing it by hand. `timeout` is generous -- long
    # enough for a slow-preset, multi-minute video -- so it only ever fires
    # on something that was genuinely never going to finish.
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, cwd=str(cwd) if cwd else None, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderError(
            f"FFmpeg did not finish {what} within {timeout}s and was stopped.\n"
            "This usually means a corrupt or unusually large photo, or an ffmpeg build\n"
            "stuck on an unsupported input. Try a faster --preset, or check the photos\n"
            "for this product open normally in an image viewer."
        ) from exc
    if proc.returncode != 0:
        raise RenderError(f"FFmpeg failed while {what}:\n{proc.stderr.strip()[:1500]}")
    return proc.stdout
