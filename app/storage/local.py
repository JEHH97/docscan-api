"""Filesystem-backed artifact storage for local development and tests."""

import contextlib
import time
from pathlib import Path


class LocalStorage:
    """Store artifacts under a configured root, preventing path traversal."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Storage key escapes the configured root")
        return path

    def save(self, key: str, data: bytes, content_type: str) -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def load(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def prune_expired(self, ttl_seconds: int) -> int:
        """Delete files whose last write is older than the configured artifact TTL."""
        cutoff = time.time() - ttl_seconds
        removed = 0
        for path in self.root.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.is_dir():
                with contextlib.suppress(OSError):
                    path.rmdir()
        return removed
