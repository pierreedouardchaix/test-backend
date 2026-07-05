from typing import Protocol


class BlobStore(Protocol):
    """Stores opaque byte content — uploaded files, step outputs — behind a key."""

    def put(self, content: bytes, *, content_type: str) -> str:
        """Store content, returning an opaque key to retrieve it later."""

    def get(self, blob_key: str) -> bytes:
        """Retrieve previously stored content."""
