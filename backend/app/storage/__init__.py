"""Storage backend selection.

`STORAGE_BACKEND` picks the implementation. Nothing else in the codebase names a
concrete backend.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.storage.base import Storage
from app.storage.local import LocalDiskStorage
from app.storage.r2 import R2Storage

__all__ = ["Storage", "LocalDiskStorage", "R2Storage", "get_storage"]


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "local":
        return LocalDiskStorage(s.storage_root)
    if s.storage_backend == "r2":
        return R2Storage(
            account_id=s.r2_account_id,
            bucket=s.r2_bucket,
            access_key_id=s.r2_access_key_id,
            secret_access_key=s.r2_secret_access_key,
            public_base_url=s.r2_public_base_url,
        )
    raise ValueError(f"Unknown STORAGE_BACKEND {s.storage_backend!r}. Expected 'local' or 'r2'.")
