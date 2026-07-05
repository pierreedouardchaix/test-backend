from src.adapters.filesystem.blob_store import FileSystemBlobStore


def test_put_then_get_roundtrips_the_content(tmp_path):
    store = FileSystemBlobStore(str(tmp_path / "blobs"))

    key = store.put(b"lorem ipsum", content_type="text/plain")

    assert store.get(key) == b"lorem ipsum"


def test_two_store_instances_share_content_through_the_same_directory(tmp_path):
    """Simulates two separate processes (Celery workers) pointed at the same
    volume: a put() from one instance must be readable from another."""
    directory = str(tmp_path / "blobs")
    writer = FileSystemBlobStore(directory)
    reader = FileSystemBlobStore(directory)

    key = writer.put(b"cross-process payload", content_type="application/json")

    assert reader.get(key) == b"cross-process payload"


def test_creates_the_directory_if_missing(tmp_path):
    directory = tmp_path / "does" / "not" / "exist"

    FileSystemBlobStore(str(directory))

    assert directory.is_dir()
