"""Processes the queue: render what is due, hand it to a publisher, log the result."""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from . import calendar as cal
from . import publish
from .config import Product


class Runner:
    def __init__(self, brand, outroot: Path, drop_root: Path, build_video, retries: int = 3):
        self.brand = brand
        self.outroot = Path(outroot)
        self.drop_root = Path(drop_root)
        self.build_video = build_video   # callable(product_dir, lang, aspect) -> (video, caption)
        self.retries = retries

    # ------------------------------------------------------------------ public

    def prepare(self, entries, state: cal.State, now: datetime, days: int) -> int:
        """Render videos for posts coming up, so posting time is not render time."""
        pending = cal.upcoming(entries, state, now, days)
        built = 0
        for entry in pending:
            video = self.video_path(entry)
            if video.exists():
                continue
            print(f"   pre-rendering {entry.product} [{entry.lang}] for {entry.when:%a %d %b}")
            try:
                self._ensure(entry)
                built += 1
            except Exception as exc:
                print(f"   could not pre-render {entry.product} [{entry.lang}]: {exc}")
        return built

    def run(self, entries, state: cal.State, now: datetime, grace: int) -> tuple[int, int]:
        due = cal.due(entries, state, now, grace)
        if not due:
            print("   nothing due")
            return 0, 0
        ok = bad = 0
        for entry in due:
            print(f"\n   {entry}")
            try:
                video, caption = self._ensure(entry)
                publisher = publish.get(entry.platform, self.drop_root)
                url = publisher.publish(entry, video, caption)
                state.record(entry, "published", url=url)
                print(f"      published -> {url}")
                ok += 1
            except Exception as exc:
                bad += 1
                self._handle_failure(entry, state, exc)
        return ok, bad

    def video_path(self, entry) -> Path:
        return self.outroot / entry.product / entry.video_name

    # ----------------------------------------------------------------- private

    def _ensure(self, entry) -> tuple[Path, str]:
        """Return (video, caption), rendering them if they are not there yet."""
        video = self.video_path(entry)
        caption_file = self.outroot / entry.product / entry.caption_name
        if not video.exists() or not caption_file.exists():
            self.build_video(entry.product, entry.lang, entry.aspect)
        if not video.exists():
            raise FileNotFoundError(
                f"the render finished but {video.name} is not there; "
                "check the aspect ratio on this calendar entry"
            )
        caption = caption_file.read_text(encoding="utf-8") if caption_file.exists() else ""
        return video, caption

    def _handle_failure(self, entry, state: cal.State, exc: Exception) -> None:
        attempts = state.bump_attempt(entry)
        message = str(exc) or exc.__class__.__name__
        if isinstance(exc, publish.PublishError):
            # A platform that is not connected will never succeed on a retry.
            state.record(entry, "failed", error=message)
            print(f"      FAILED {message}")
            return
        if attempts < self.retries:
            print(f"      error: {message}")
            print(f"      attempt {attempts} of {self.retries}, will try again on the next run")
            return
        state.record(entry, "failed", error=message)
        print(f"      FAILED after {attempts} attempts: {message}")
        if not isinstance(exc, (FileNotFoundError, ValueError)):
            traceback.print_exc()
