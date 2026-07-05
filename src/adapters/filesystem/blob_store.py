import uuid
from pathlib import Path


class FileSystemBlobStore:
    """Stores blobs as files under a shared directory — the in-memory store
    is process-local, which breaks the moment ocr and metadata run in
    different Celery worker processes. A volume mounted on every app/worker
    container makes puts from one process readable from another.

    Placeholder for the production choice (object storage — S3/MinIO): same
    two-method contract, so swapping later touches only this file and the
    dependency wiring.
    """

    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes, *, content_type: str) -> str:
        blob_key = str(uuid.uuid4())
        (self._root / blob_key).write_bytes(content)
        return blob_key

    def get(self, blob_key: str) -> bytes:
        return (self._root / blob_key).read_bytes()
