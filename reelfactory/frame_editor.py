"""Read and replace individual decoded video frames using local FFmpeg."""
from fractions import Fraction
import json
from pathlib import Path
import subprocess

from .render import RenderError

MAX_PATCHES = 30
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def _run(command, timeout=60):
    try:
        return subprocess.run(command, capture_output=True, check=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        raise RenderError('Could not process the video frames. Check FFmpeg and try a playable video.') from None


def info(path):
    raw = _run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                '-show_entries', 'stream=width,height,nb_read_frames,avg_frame_rate:stream_side_data=rotation',
                '-of', 'json', str(path)])
    try:
        stream = json.loads(raw)['streams'][0]
        width, height = int(stream['width']), int(stream['height'])
        rotation = next((entry.get('rotation', 0) for entry in stream.get('side_data_list', [])
                         if 'rotation' in entry), 0)
        if abs(rotation) % 180 == 90:
            width, height = height, width
        count = int(stream['nb_read_frames'])
        fps = float(Fraction(stream['avg_frame_rate']))
        if count < 1 or fps <= 0 or width * height > 4096 * 4096:
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        raise RenderError('This video cannot be edited. Choose a readable video up to 4096 × 4096 pixels.') from None
    stat = Path(path).stat()
    return dict(width=width, height=height, frame_count=count, fps=fps,
                version=f'{stat.st_size}-{stat.st_mtime_ns}')


def read_frame(path, index):
    content = _run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', f'select=eq(n\\,{index})',
                    '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'])
    if not content:
        raise RenderError('That frame is no longer available. Reopen the frame editor.')
    return content


def replace_frames(source, destination, patches):
    """Each patch is a full-size PNG; only its selected frame is replaced."""
    if not patches or len(patches) > MAX_PATCHES:
        raise RenderError(f'Edit between 1 and {MAX_PATCHES} frames at a time.')
    command = ['ffmpeg', '-y', '-v', 'error', '-i', str(source)]
    filters, previous = [], '0:v:0'
    for number, (index, patch) in enumerate(sorted(patches.items()), 1):
        command += ['-i', str(patch)]
        # The generic timeline enable expression counts decoded frames from zero.
        label = f'frame{number}'
        filters.append(f"[{previous}][{number}:v]overlay=0:0:eof_action=repeat:enable='eq(n,{index})'[{label}]")
        previous = label
    command += ['-filter_complex', ';'.join(filters), '-map', f'[{previous}]', '-map', '0:a?',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                '-fps_mode', 'passthrough', '-c:a', 'aac', '-movflags', '+faststart', str(destination)]
    _run(command, timeout=240)
