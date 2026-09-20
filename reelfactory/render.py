"""FFmpeg render pipeline: photos + voiceover -> a finished social video.

Two passes. Pass one turns each photo into a moving shot of exactly the right
length. Pass two cross-fades the shots together, lays on the logo and burned-in
text, and mixes the voiceover over ducked background music.
"""
from __future__ import annotations

import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import templates
from .config import VIDEO_EXTS

FPS = 30
XFADE = 0.5          # seconds of cross-dissolve between shots
TAIL = 0.9           # extra seconds held on the final CTA shot
LEAD = 0.05          # subtitle appears a beat before the voice
MIN_PAUSE = 0.08     # never close the gap between lines completely

# Pass one renders each shot on a canvas this many times the output size, so
# that zoompan -- which works on integer pixels -- has room to move smoothly.
# It is also what decides how big a photo has to be to stay sharp: see
# photo_notes().
OVERSAMPLE = 2
MAX_ZOOM = 1.30      # the furthest any Ken Burns move pushes in
PAN_ZOOM = 1.18      # the fixed zoom the two panning moves sit at

# Where "this photo is not good enough" starts, as used by photo_notes(). Named
# constants rather than bare defaults because stock.py filters search results by
# the same numbers -- a stock photo offered as "big enough" has to mean exactly
# what the warning on the product page means.
MIN_KEPT = 0.68      # fraction of the photo still on screen after the crop
MAX_UPSCALE = 1.15   # how far it may be enlarged before it looks soft

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

# How much of a photo a centre crop is allowed to throw away. A portrait photo
# in a 9:16 frame loses a harmless sliver; a landscape phone shot loses well
# over half its width, which is usually exactly where the product is. Past this
# limit we crop only as far as the budget allows and fill the leftover with a
# blurred copy of the photo, so nothing is silently cut off.
MAX_CROP_LOSS = 0.35

# Ceilings on colour matching. A photo that really is meant to look different --
# a night shot among daylight ones -- should be nudged, not dragged all the way
# to the middle of the set.
MATCH_Y_LIMIT = 18.0        # brightness, out of 255
MATCH_UV_LIMIT = 10.0       # colour, where 128 is neutral


class RenderError(RuntimeError):
    pass


@dataclass
class Shot:
    photo: Path
    duration: float     # on-screen length including its share of the cross-fade
    still: bool = False # skip the camera move: already composed at frame size
    move: str | None = None   # set by _assign_moves; None falls back to rotation
    correct: str = ""         # set by _match_colours; an ffmpeg filter, or blank


def plan(durations, pause: float, xfade: float = XFADE,
         bpm: float = 0.0, beat_offset: float = 0.0, snap: float = 0.25):
    """Given per-segment speech lengths, return shot lengths and subtitle windows.

    Each shot is padded by one transition length so that, after cross-fading,
    shot i starts exactly when segment i's speech starts in the audio track.

    `pause` is either one number used after every segment, or one per segment
    (from `voice.pauses_for`). It has to be the same value `voice.concat` was
    given: these two functions are the only reason picture and voice stay in
    step, and they derive it independently.

    Timings are (start, end, speech_start): the first two are when the text is
    on screen, the third is when this segment's audio actually begins. Word-level
    captions are timed from speech_start, so it cannot be inferred from `start`
    -- that one is nudged early by LEAD, and clamped at zero for the first shot.

    With `bpm` set, each cut is pulled onto the nearest beat by stretching or
    trimming the pause before it, but only when the beat is within `snap`
    seconds. The speech itself is never cut into: only the silence between lines
    moves, so the words still land on their own pictures.

    Returns (shots, timings, pauses) -- the pauses matter because the voice
    track has to be joined with the same gaps the plan assumed.
    """
    shots, timings, pauses, cursor = [], [], [], 0.0
    n = len(durations)
    beat = 60.0 / bpm if bpm and bpm > 0 else 0.0
    for i, d in enumerate(durations):
        gap = float(pause) if isinstance(pause, (int, float)) else float(pause[i])
        if beat and i < n - 1:
            natural = cursor + d + gap
            k = round((natural - beat_offset) / beat)
            shift = (beat_offset + k * beat) - natural
            if abs(shift) <= snap:
                gap = max(MIN_PAUSE, gap + shift)
        span = d + gap + (TAIL if i == n - 1 else 0.0)
        shots.append(round(span + xfade, 3))
        timings.append((
            round(max(0.0, cursor - LEAD), 3),
            round(cursor + span, 3),
            round(cursor, 3),
        ))
        pauses.append(round(gap, 3))
        cursor += span
    return shots, timings, pauses


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
    crf: int | None = None,
    preset: str = "medium",
    template=None,
    accent_times=None,
) -> Path:
    _require("ffmpeg")
    tpl = template or templates.Template()
    width, height = size
    # An explicit crf always wins; otherwise it follows the preset, so that
    # asking for a slower render actually returns a better-looking video
    # rather than the same picture in a smaller file.
    crf = PRESET_CRF.get(preset, DEFAULT_CRF) if crf is None else crf
    workdir.mkdir(parents=True, exist_ok=True)
    _assign_moves(shots, tpl)
    _match_colours(shots, tpl.match)
    clips = [
        _render_shot(s, i, width, height, workdir, tpl)
        for i, s in enumerate(shots)
    ]
    return _compose(
        clips, [s.duration for s in shots], subtitle_file, voice_track, outfile,
        width, height, workdir, logo, music, music_volume, fonts_dir, crf, preset,
        tpl, scrim_color, accent_times or [],
    )


# --------------------------------------------------------------------------- pass 1


def is_video(path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def _render_shot(shot: Shot, idx: int, w: int, h: int, workdir: Path, tpl) -> Path:
    dest = workdir / f"shot{idx:02d}.mp4"
    frames = max(2, int(round(shot.duration * FPS)))
    # Oversample before zoompan; it works on integer pixels, so a bigger canvas
    # is what keeps slow moves from stepping visibly.
    ow, oh = w * OVERSAMPLE, h * OVERSAMPLE

    if shot.still:
        # Already built at exactly frame size, and a brand card should sit
        # still rather than drift: no framing, no camera move, no grade.
        source = ["-loop", "1", "-i", str(shot.photo)]
        chain = f"scale={w}:{h},fps={FPS},format=yuv420p,setsar=1"
    elif is_video(shot.photo):
        # A clip already moves, so it gets no camera move of its own -- just
        # the framing and grade every other shot gets. Looping covers a clip
        # shorter than its slot; its own audio is dropped, the voiceover owns
        # the soundtrack.
        source = ["-stream_loop", "-1", "-i", str(shot.photo), "-an"]
        chain = f"{_lead(shot)}{_framing(shot.photo, w, h, w, h, tpl.crop_budget)},fps={FPS}"
        if tpl.grade:
            chain += f",{tpl.grade}"
        chain += ",format=yuv420p,setsar=1"
    else:
        move = shot.move or tpl.move_for(idx)
        z, x, y = _motion(move, frames, tpl.zoom)
        source = ["-loop", "1", "-i", str(shot.photo)]
        chain = (
            f"{_lead(shot)}{_framing(shot.photo, ow, oh, w, h, tpl.crop_budget)},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS}"
        )
        if tpl.grade:
            chain += f",{tpl.grade}"
        chain += f",scale={w}:{h}:out_range=tv,format=yuv420p,setsar=1"
    if idx == 0:
        chain += ",fade=t=in:st=0:d=0.25"

    _run([
        "ffmpeg", "-y", "-v", "error",
        *source,
        "-vf", chain,
        "-t", f"{shot.duration:.3f}",
        "-r", str(FPS), "-c:v", "libx264", "-preset", SHOT_PRESET, "-crf", str(SHOT_CRF),
        "-pix_fmt", "yuv420p", "-color_range", "tv", str(dest),
    ], what=f"rendering shot {idx + 1} from {shot.photo.name}", timeout=120)
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


def _file_signature(path) -> tuple[int, int] | None:
    """Cheap cache identity that changes when a media file is replaced."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _probe_size(photo: Path):
    key = str(Path(photo).resolve())
    signature = _file_signature(photo)
    cached = _sizes.get(key)
    if cached is None or cached[0] != signature:
        try:
            out = _run([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(photo),
            ], what=f"reading the dimensions of {photo.name}")
            pw, ph = (int(v) for v in out.strip().splitlines()[0].split("x")[:2])
            value = (pw, ph) if pw and ph else None
        except (RenderError, ValueError, IndexError):
            value = None
        _sizes[key] = (signature, value)
    return _sizes[key][1]


def _lead(shot: Shot) -> str:
    """Any per-photo correction, as a prefix for that shot's filter chain."""
    return f"{shot.correct}," if shot.correct else ""


def _match_colours(shots, strength: float) -> None:
    """Pull every photo toward the middle of the set.

    Client photos arrive from different phones at different times of day: one
    warm, the next cool, one under-exposed. Individually each is fine; cut
    together they look like several different shoots. Each photo is measured,
    the set's median becomes the target, and each is moved a fraction of the way
    there -- a fraction, and capped, so a deliberately different photo is nudged
    rather than flattened into the rest.
    """
    if strength <= 0:
        return
    sources = [s for s in shots if not s.still]
    stats = {}
    for shot in sources:
        stats.setdefault(str(shot.photo), _signal_stats(shot.photo))
    usable = [v for v in stats.values() if v]
    if len(usable) < 2:
        return                      # one photo has nothing to be matched to
    target = [statistics.median(v[i] for v in usable) for i in range(3)]

    for shot in sources:
        measured = stats.get(str(shot.photo))
        if not measured:
            continue
        dy = _capped((target[0] - measured[0]) * strength, MATCH_Y_LIMIT)
        du = _capped((target[1] - measured[1]) * strength, MATCH_UV_LIMIT)
        dv = _capped((target[2] - measured[2]) * strength, MATCH_UV_LIMIT)
        if max(abs(dy), abs(du), abs(dv)) < 0.5:
            continue                # already in line; leave the pixels untouched
        shot.correct = (
            f"lutyuv=y='clip(val{dy:+.1f},0,255)'"
            f":u='clip(val{du:+.1f},0,255)'"
            f":v='clip(val{dv:+.1f},0,255)'"
        )


def _capped(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


_stats: dict = {}


def _signal_stats(photo):
    """(brightness, U, V) averaged over the first frame, or None if unreadable."""
    key = str(Path(photo).resolve())
    signature = _file_signature(photo)
    cached = _stats.get(key)
    if cached is None or cached[0] != signature:
        try:
            out = _run([
                "ffmpeg", "-v", "error", "-i", str(photo),
                "-vf", "signalstats,metadata=print:file=-",
                "-frames:v", "1", "-f", "null", "-",
            ], what=f"measuring the colour of {Path(photo).name}")
            found = [re.search(rf"signalstats\.{n}=([0-9.]+)", out) for n in ("YAVG", "UAVG", "VAVG")]
            value = tuple(float(m.group(1)) for m in found) if all(found) else None
        except (RenderError, ValueError):
            value = None
        _stats[key] = (signature, value)
    return _stats[key][1]


def _assign_moves(shots, tpl) -> None:
    """Give every shot a camera move, never repeating one on the same photo.

    With fewer photos than segments some photos come round again, and the plain
    rotation can hand a photo the same move it had the first time -- the same
    picture moving the same way, which reads as a mistake. Start from where the
    rotation would have landed and step forward until this photo gets something
    it has not had yet, so unrepeated photos keep the normal variety.
    """
    seen: dict = {}
    for i, shot in enumerate(shots):
        if shot.still or is_video(shot.photo):
            continue        # nothing to move: a card sits still, a clip moves itself
        used = seen.setdefault(str(shot.photo), [])
        if len(used) >= len(tpl.moves):
            used.clear()          # exhausted: start the photo's cycle again
        move = tpl.move_for(i)
        for step in range(len(tpl.moves)):
            candidate = tpl.move_for(i + step)
            if candidate not in used:
                move = candidate
                break
        used.append(move)
        shot.move = move


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


def end_card(w: int, h: int, workdir: Path, ground: str) -> Path:
    """The closing frame: the brand's own colour, lifted slightly at the top so
    it reads as lit rather than flat. The text on it comes from the subtitle
    file like every other beat, and the scrim and logo are laid over it by the
    normal composite -- so the card is only ever the ground.
    """
    r, g, b = _rgb(ground)
    # Lift the top toward white rather than by a fixed amount, so a dark brand
    # colour gets a visible gradient and a light one is left nearly flat.
    top = tuple(min(255, int(c + (255 - c) * 0.13)) for c in (r, g, b))
    dest = workdir / f"endcard_{w}x{h}_{r:02x}{g:02x}{b:02x}.png"
    if dest.exists():
        return dest
    _run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", (f"gradients=s={w}x{h}:n=2"
               f":c0=0x{top[0]:02x}{top[1]:02x}{top[2]:02x}"
               f":c1=0x{r:02x}{g:02x}{b:02x}"
               f":x0=0:y0=0:x1=0:y1={h}"),
        # A gradient this gentle spans only ~30 levels over the full height,
        # which bands badly. A little grain dithers it away; the seed keeps
        # repeat builds identical.
        "-vf", "noise=alls=9:all_seed=7",
        "-frames:v", "1", str(dest),
    ], what="building the end card")
    return dest


def _make_whoosh(workdir: Path) -> Path:
    """A short upward swish for a cut.

    Three bands of pink noise crossfaded low to high: an actual rising sweep
    rather than a flat noise burst, which is what makes it read as movement.
    The bandpasses cost about 11dB, so it is gained back to a -6dBFS peak --
    that way a template's `whoosh` value means the same thing every time.
    """
    dest = workdir / "sfx_whoosh.wav"
    if dest.exists():
        return dest
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "anoisesrc=d=0.22:c=pink:a=0.6:r=44100:s=11",
        "-f", "lavfi", "-i", "anoisesrc=d=0.22:c=pink:a=0.6:r=44100:s=22",
        "-f", "lavfi", "-i", "anoisesrc=d=0.26:c=pink:a=0.6:r=44100:s=33",
        "-filter_complex",
        "[0:a]bandpass=f=700:width_type=o:w=2[a];"
        "[1:a]bandpass=f=2200:width_type=o:w=2[b];"
        "[2:a]bandpass=f=5200:width_type=o:w=2[c];"
        "[a][b]acrossfade=d=0.12:c1=tri:c2=tri[ab];"
        "[ab][c]acrossfade=d=0.12:c1=tri:c2=tri[sw];"
        "[sw]afade=t=in:st=0:d=0.06,afade=t=out:st=0.30:d=0.16,volume=11dB[out]",
        "-map", "[out]", "-ar", "44100", "-ac", "1", str(dest),
    ], what="building the transition swish")
    return dest


def _make_hit(workdir: Path) -> Path:
    """A soft low thump for the price beat: two sines with a percussive decay."""
    dest = workdir / "sfx_hit.wav"
    if dest.exists():
        return dest
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "sine=frequency=190:duration=0.45:sample_rate=44100",
        "-f", "lavfi", "-i", "sine=frequency=95:duration=0.45:sample_rate=44100",
        "-filter_complex",
        "[0:a]volume=0.7[a];[1:a]volume=1.0[b];"
        "[a][b]amix=inputs=2:normalize=0,"
        "afade=t=in:st=0:d=0.006,afade=t=out:st=0:d=0.45:curve=exp,volume=10dB[out]",
        "-map", "[out]", "-ar", "44100", "-ac", "1", str(dest),
    ], what="building the accent hit")
    return dest


def _make_sfx(workdir: Path, total: float, cuts, accents, whoosh_vol, accent_vol):
    """One mono track with every effect already placed at its moment.

    Rendered as its own file rather than a dozen more inputs on the composite,
    which would make an already long filtergraph much harder to follow.
    Returns None when the template asks for no sound, so the audio graph is
    left exactly as it was.
    """
    placed = []
    if whoosh_vol > 0:
        # Slightly ahead of the cut, so the swish peaks as the picture changes.
        placed += [(_make_whoosh(workdir), t - 0.10, whoosh_vol) for t in cuts]
    if accent_vol > 0:
        placed += [(_make_hit(workdir), t, accent_vol) for t in accents]
    placed = [(src, max(0.0, at), vol) for src, at, vol in placed if at < total]
    if not placed:
        return None

    tag = f"{int(total * 100)}_{len(placed)}_{int(whoosh_vol * 100)}_{int(accent_vol * 100)}"
    dest = workdir / f"sfx_{tag}.wav"
    if dest.exists():
        return dest
    args = ["ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={total:.3f}"]
    filt, labels = [], ["[0:a]"]
    for i, (src, at, vol) in enumerate(placed, start=1):
        args += ["-i", str(src)]
        filt.append(f"[{i}:a]volume={vol:.3f},adelay={int(at * 1000)}[s{i}]")
        labels.append(f"[s{i}]")
    filt.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=first[out]")
    args += ["-filter_complex", ";".join(filt), "-map", "[out]",
             "-ar", "44100", "-ac", "1", "-t", f"{total:.3f}", str(dest)]
    _run(args, what="building the sound effects track")
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
    tpl, scrim_color, accent_times,
):
    xfade = tpl.transition_seconds
    # Where the cuts land, and how long the whole thing runs. Both are needed
    # before the inputs are built, because the effects track is one of them.
    cuts, offset = [], 0.0
    for i in range(1, len(clips)):
        offset += durations[i - 1] - xfade
        cuts.append(offset)
    total = sum(durations) - xfade * max(0, len(clips) - 1)

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
    sfx_idx = None
    sfx = _make_sfx(workdir, total, cuts, accent_times, tpl.whoosh, tpl.accent_hit)
    if sfx:
        sfx_idx = next_idx
        next_idx += 1
        inputs += ["-i", sfx.name]
    logo_idx = None
    if logo:
        logo_idx = next_idx
        inputs += ["-i", str(logo)]

    # Cross-fade the shots into one continuous stream.
    if len(clips) == 1:
        filters.append("[0:v]null[vid]")
    else:
        prev = "[0:v]"
        for i in range(1, len(clips)):
            label = "[vid]" if i == len(clips) - 1 else f"[x{i}]"
            kind = tpl.transition_for(i - 1)
            filters.append(
                f"{prev}[{i}:v]xfade=transition={kind}:duration={xfade}"
                f":offset={cuts[i - 1]:.3f}{label}"
            )
            prev = label

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
    mix = ["[vo]"]
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
        mix = ["[vo1]", "[bgduck]"]
    if sfx_idx is not None:
        filters.append(
            f"[{sfx_idx}:a]aresample=44100,{afmt},apad,"
            f"atrim=0:{total:.3f},asetpts=N/SR/TB[sfx]"
        )
        mix.append("[sfx]")
    if len(mix) == 1:
        filters.append(f"{mix[0]}anull[aout]")
    else:
        filters.append(
            "".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first[aout]"
        )

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
    bad = [p.name for p in photos if not is_video(p) and not _looks_like_image(p)]
    if bad:
        lead = "This photo is not a readable image" if len(bad) == 1 else "These photos are not readable images"
        raise RenderError(f"{lead}: {', '.join(bad)}.")
    probe_photos([p for p in photos if not is_video(p)])


def _looks_like_image(path: Path) -> bool:
    """Reject obvious non-images before requiring ffprobe for full decoding."""
    try:
        with Path(path).open("rb") as fh:
            header = fh.read(16)
    except OSError:
        return False
    return (
        header.startswith(b"\xff\xd8\xff")
        or header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"BM")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


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


def photo_notes(sizes: dict, size=(1080, 1920), min_kept: float = MIN_KEPT,
                max_upscale: float = MAX_UPSCALE) -> list:
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
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(cwd) if cwd else None, timeout=timeout,
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
