"""Local-disk storage backend (development and `docker compose` default)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class LocalDiskStorage:
    """Stores objects under STORAGE_ROOT, served back out via the API's /media route.

    `put` writes to a temporary file in the destination directory and then calls
    `os.replace`, which is an atomic rename on both POSIX and Windows when source and
    destination are on the same filesystem. A reader therefore never observes a
    half-written file — the same guarantee S3/R2 give per-object, which is why the
    publish job's correctness argument survives the backend swap unchanged.
    """

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are server-generated, but treat them as untrusted anyway: a key
        # containing '..' must not be able to escape STORAGE_ROOT.
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"key escapes storage root: {key!r}")
        return target

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def public_url(self, key: str) -> str:
        return f"/media/{key}"
