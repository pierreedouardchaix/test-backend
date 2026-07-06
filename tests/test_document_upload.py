"""Upload endpoint checks: the size bound (413) and the empty-file guard (422).

Wires the real `app` with fake write dependencies (UoW, blob store, dispatcher)
so the POST exercises the router's own validation, not the persistence stack."""
import io
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from src.auth import AuthContext, get_current_user
from src.dependencies import get_blob_store, get_uow, get_workflow_dispatcher
from src.domain.models.user import User
from src.main import app
from src.routers import documents as documents_router
from tests.fakes import FakeBlobStore, FakeUnitOfWork, FakeWorkflowDispatcher

TENANT = uuid.uuid4()
USER = User(id=uuid.uuid4(), first_name="Alice", last_name="Acme")


def _client() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: AuthContext(tenant_id=TENANT, user=USER)
    app.dependency_overrides[get_uow] = lambda: FakeUnitOfWork()
    app.dependency_overrides[get_blob_store] = lambda: FakeBlobStore()
    app.dependency_overrides[get_workflow_dispatcher] = lambda: FakeWorkflowDispatcher()
    return TestClient(app)


def test_upload_small_file_succeeds():
    try:
        r = _client().post("/documents", files={"file": ("doc.pdf", b"hello world", "application/pdf")})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "processing"
    uuid.UUID(body["document_id"])  # a real id was returned


def test_upload_empty_file_returns_422_not_500():
    try:
        r = _client().post("/documents", files={"file": ("empty.pdf", b"", "application/pdf")})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 422
    assert "empty" in r.json()["detail"].lower()


def test_upload_exceeding_the_size_cap_returns_413(monkeypatch):
    monkeypatch.setattr(documents_router, "_MAX_UPLOAD_BYTES", 8)  # tiny cap for the test
    try:
        r = _client().post("/documents", files={"file": ("big.pdf", b"x" * 64, "application/pdf")})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 413
    assert "too large" in r.json()["detail"].lower()


# `_read_capped` is the real guard (a client can under-declare or omit
# Content-Length), so exercise it directly and deterministically.
async def test_read_capped_returns_the_full_body_under_the_cap():
    upload = UploadFile(filename="x", file=io.BytesIO(b"hello world"))
    assert await documents_router._read_capped(upload) == b"hello world"


async def test_read_capped_aborts_with_413_once_the_body_exceeds_the_cap(monkeypatch):
    monkeypatch.setattr(documents_router, "_MAX_UPLOAD_BYTES", 8)
    upload = UploadFile(filename="x", file=io.BytesIO(b"y" * 64))
    with pytest.raises(HTTPException) as exc_info:
        await documents_router._read_capped(upload)
    assert exc_info.value.status_code == 413
