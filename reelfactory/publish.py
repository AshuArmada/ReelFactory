"""Where a finished video goes.

Every backend exposes the same call, so the runner does not care which one is in
use and a new platform is a single small class:

    publish(entry, video_path, caption) -> str   # a URL, or a description of
                                                 # where the file ended up

Two backends work today. `dryrun` logs what would have happened and touches
nothing, which is how you should watch the schedule for the first week.
`folder` drops the video and caption into a dated folder ready for you to
upload by hand -- useful the whole time the API access is still in review.

The API backends are deliberately stubs. Wiring them up is a paperwork job
rather than a coding one; PHASE2.md explains what it involves.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


class PublishError(RuntimeError):
    """Something went wrong that the runner should record and move past."""


class Publisher:
    name = "base"

    def publish(self, entry, video: Path, caption: str) -> str:
        raise NotImplementedError


class DryRunPublisher(Publisher):
    """Logs the post it would have made. Never touches a network."""

    name = "dryrun"

    def publish(self, entry, video: Path, caption: str) -> str:
        size = video.stat().st_size / 1_000_000 if video.exists() else 0
        first = caption.splitlines()[0] if caption.strip() else "(no caption)"
        print(f"      would post {video.name} ({size:.1f} MB) to {entry.platform}")
        print(f"      caption starts: {first[:70]}")
        return f"dry-run:{entry.id}"


class FolderPublisher(Publisher):
    """Copies the video and caption into a dated drop folder for manual posting."""

    name = "folder"

    def __init__(self, root: Path):
        self.root = Path(root)

    def publish(self, entry, video: Path, caption: str) -> str:
        if not video.exists():
            raise PublishError(f"video is missing: {video}")
        dest = self.root / f"{entry.when:%Y-%m-%d}"
        dest.mkdir(parents=True, exist_ok=True)
        stem = f"{entry.when:%H%M}_{entry.product}_{entry.lang}"
        try:
            shutil.copy2(video, dest / f"{stem}.mp4")
            (dest / f"{stem}_caption.txt").write_text(caption, encoding="utf-8")
        except OSError as exc:
            raise PublishError(f"could not write to {dest}: {exc}") from exc
        self._log(dest, entry, stem)
        return str(dest / f"{stem}.mp4")

    def _log(self, dest: Path, entry, stem: str) -> None:
        """A tick-list in the drop folder, so you know what still needs posting."""
        index = dest / "TO_POST.txt"
        lines = []
        if not index.exists():
            lines.append(f"Ready to post -- {entry.when:%A %d %B}\n{'=' * 46}\n")
        lines.append(f"[ ] {entry.when:%H:%M}  {entry.platform:<10} {stem}.mp4")
        lines.append(f"        caption: {stem}_caption.txt")
        if entry.note:
            lines.append(f"        note: {entry.note}")
        with open(index, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


class NotWiredPublisher(Publisher):
    """A platform that has been asked for but whose access is not set up yet."""

    def __init__(self, platform: str):
        self.name = platform

    def publish(self, entry, video: Path, caption: str) -> str:
        raise PublishError(
            f"posting to {self.name} is not connected yet.\n"
            f"        Until it is, set 'platform: folder' on this entry and upload by hand.\n"
            f"        See PHASE2.md for what connecting {self.name} actually requires."
        )


def get(platform: str, drop_root: Path) -> Publisher:
    if platform == "dryrun":
        return DryRunPublisher()
    if platform == "folder":
        return FolderPublisher(drop_root)
    return NotWiredPublisher(platform)
