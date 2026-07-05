import json
import uuid

import pytest
from fastapi.testclient import TestClient

from src.application.ingest_document import PRIMMO_DEFINITION
from src.dependencies import get_blob_store, get_event_publisher, get_settings, get_uow
from src.domain.models.workflow import Workflow, WorkflowStatus
from src.main import app
from src.partner_hmac import sign
from src.settings import Settings
from tests.fakes import FakeBlobStore, FakeEventPublisher, FakeUnitOfWork

SECRET = "partner-secret"
TENANT = uuid.uuid4()
PARTNER_JOB_ID = "j_abc123def4567890"  # the partner's opaque job id, correlation key on the webhook

_settings = Settings(
    database_url="unused",
    jwt_secret="unused",
    jwt_expiry_seconds=3600,
    dev_mode=True,
    partner_hmac_secret=SECRET,
)


def _workflow_awaiting_callback(workflow_id: uuid.UUID) -> Workflow:
    wf = Workflow.create(id=workflow_id, tenant_id=TENANT, definition=PRIMMO_DEFINITION)
    for step in ("ocr", "metadata", "chunking"):
        wf.start_task(step)
        wf.on_task_succeeded(step, f"blob-{step}")
    wf.start_task("external_call")
    wf.mark_task_deferred("external_call", PARTNER_JOB_ID)
    return wf


def _client(uow: FakeUnitOfWork) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: _settings
    app.dependency_overrides[get_uow] = lambda: uow
    app.dependency_overrides[get_blob_store] = lambda: FakeBlobStore()
    app.dependency_overrides[get_event_publisher] = lambda: FakeEventPublisher()
    return TestClient(app)


def _post_signed(client: TestClient, body: dict):
    raw = json.dumps(body).encode()
    return client.post("/webhooks/partner", content=raw, headers={"X-Partner-Signature": sign(raw, secret=SECRET)})


def test_signed_completed_callback_makes_document_ready():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(job_id))
    try:
        r = _post_signed(_client(uow), {"job_id": PARTNER_JOB_ID, "status": "completed", "result": {"indexed": True}})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.json()["workflow_status"] == WorkflowStatus.SUCCEEDED.value
    assert uow.workflows.get_by_id(job_id).status == WorkflowStatus.SUCCEEDED


def test_invalid_signature_is_401():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(job_id))
    try:
        client = _client(uow)
        raw = json.dumps({"job_id": PARTNER_JOB_ID, "status": "completed"}).encode()
        r = client.post("/webhooks/partner", content=raw, headers={"X-Partner-Signature": "deadbeef"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 401
    # Untouched: bad signature never reaches the use case.
    assert uow.workflows.get_by_id(job_id).status == WorkflowStatus.RUNNING


def test_unknown_job_id_is_404():
    uow = FakeUnitOfWork()  # empty
    try:
        r = _post_signed(_client(uow), {"job_id": "j_does_not_exist", "status": "completed"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404


def test_replayed_callback_is_200_without_reprocessing():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(job_id))
    try:
        client = _client(uow)
        body = {"job_id": PARTNER_JOB_ID, "status": "completed", "result": {"n": 1}}
        first = _post_signed(client, body)
        second = _post_signed(client, body)
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["workflow_status"] == WorkflowStatus.SUCCEEDED.value


def test_failed_callback_marks_document_failed():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(job_id))
    try:
        r = _post_signed(_client(uow), {"job_id": PARTNER_JOB_ID, "status": "failed", "error": "partner boom"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    wf = uow.workflows.get_by_id(job_id)
    assert wf.status == WorkflowStatus.FAILED
    assert wf.failure_reason == "partner boom"


def test_oversized_body_is_413():
    uow = FakeUnitOfWork()
    try:
        client = _client(uow)
        big_body = b"x" * (64 * 1024 + 1)
        r = client.post(
            "/webhooks/partner",
            content=big_body,
            headers={"X-Partner-Signature": "irrelevant"},
        )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 413


def test_error_field_truncated_above_2000_chars_is_422():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(job_id))
    try:
        r = _post_signed(_client(uow), {"job_id": PARTNER_JOB_ID, "status": "failed", "error": "x" * 2001})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 422


def test_malformed_content_length_is_a_400_not_a_500():
    """A non-numeric Content-Length is a bad request from the client, not a
    server error — int() must not be allowed to raise unguarded. Tested at the
    helper level: httpx recomputes Content-Length, so a malformed value can't
    be injected through TestClient."""
    from fastapi import HTTPException

    from src.routers.webhooks import _declared_content_length

    assert _declared_content_length(None) is None
    assert _declared_content_length("512") == 512
    with pytest.raises(HTTPException) as exc:
        _declared_content_length("not-a-number")
    assert exc.value.status_code == 400
