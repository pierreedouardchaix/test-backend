import uuid


class InMemoryBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put(self, content: bytes, *, content_type: str) -> str:
        blob_key = str(uuid.uuid4())
        self._blobs[blob_key] = content
        return blob_key

    def get(self, blob_key: str) -> bytes:
        return self._blobs[blob_key]
