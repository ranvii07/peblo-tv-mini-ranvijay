"""Storage protocol.

Every byte the system persists outside Postgres — artwork originals and published
catalogue files — goes through this interface. Call sites never import a concrete
backend; they take a `Storage` and are handed one by `get_storage()`. Swapping local
disk for Cloudflare R2 is therefore a one-class change plus an env var, with zero
edits at any call site (see README, written answer #2).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Write `data` at `key`. The write is durable and atomic on return.

        Atomic means a concurrent reader sees either the previous object or the
        complete new one — never a partially written object. The publish job depends
        on this guarantee.
        """
        ...

    def get(self, key: str) -> bytes:
        """Return the object's bytes. Raises FileNotFoundError if absent."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None:
        """Remove the object. Deleting a missing key is not an error."""
        ...

    def public_url(self, key: str) -> str:
        """URL a browser can fetch this object from."""
        ...
