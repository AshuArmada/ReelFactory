"""Bounded disposable previews; active renders and final exports are preserved."""
from contextlib import contextmanager
from pathlib import Path
import re
import secrets
import shutil
import time

from ..storage import file_lock


class PreviewCache:
    def __init__(self, output_dir, keep=3):
        output_dir = Path(output_dir).resolve()
        self.root = output_dir / ".previews"
        if self.root.resolve().parent != output_dir:
            raise ValueError("Preview storage must stay inside the product output folder.")
        self.keep = keep

    def _remove(self, folder):
        # Verify the actual recursive-delete target, including symlink/junction resolution.
        if (not re.fullmatch(r"[0-9a-f]{24}", folder.name)
                or folder.resolve() != self.root.resolve() / folder.name):
            return False
        try:
            shutil.rmtree(folder)
            return True
        except OSError:
            return False  # Windows may still have a streamed video open; retry later.

    def _prune(self, keep):
        folders = sorted((p for p in self.root.iterdir() if p.is_dir()
                          and re.fullmatch(r"[0-9a-f]{24}", p.name)
                          and not (p / ".active").exists()),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        removed = failed = 0
        for index, folder in enumerate(folders):
            if index >= keep or folder.stat().st_mtime < time.time() - 86400:
                if self._remove(folder):
                    removed += 1
                else:
                    failed += 1
        return {"removed": removed, "failed": failed}

    def clear(self):
        if not self.root.exists():
            return {"removed": 0, "failed": 0}
        with file_lock(self.root / "cache"):
            return self._prune(0)

    @contextmanager
    def job(self):
        with file_lock(self.root / "cache"):
            folder = self.root / secrets.token_hex(12)
            folder.mkdir()
            (folder / ".active").touch()
        try:
            yield folder
        except BaseException:
            with file_lock(self.root / "cache"):
                (folder / ".active").unlink(missing_ok=True)
                self._remove(folder)
            raise
        else:
            with file_lock(self.root / "cache"):
                (folder / ".active").unlink(missing_ok=True)
                folder.touch()
                self._prune(self.keep)
