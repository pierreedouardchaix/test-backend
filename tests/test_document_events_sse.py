"""SSE endpoint tests: ownership gate + live streaming + terminal close.

The event stream is faked with a finite async iterator, so the response
completes and TestClient can read the full SSE body. The real Redis round-trip
is left to the (deferred) integration tests."""
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
import sse_starlette.sse
from fastapi.testclient import TestClient

from src.auth import AuthContext, get_current_user_from_query_token
from src.dependencies import get_document_reader, get_event_stream
from src.domain.models.user import User
from src.main import app
from src.ports.document_data_source import DocumentDetailRow

TENANT = uuid.uuid4()
USER = User(id=uuid.uuid4(), first_name="Alice", last_name="Acme")
DOC_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _reset_sse_app_status():
    """sse-starlette keeps a module-global exit Event bound to the first event
    loop it sees; TestClient spins a fresh loop per request, so reset it before
    each test. Purely a test-harness concern — a real uvicorn process has one
    long-lived loop."""
    sse_starlette.sse.AppStatus.should_exit_event = None
    yield


def _detail_row() -> DocumentDetailRow:
    return DocumentDetailRow(
        document_id=DOC_ID,
        tenant_id=TENANT,
        uploaded_by=USER.id,
        uploaded_by_first_name="Alice",
        uploaded_by_last_name="Acme",
        filename="doc.pdf",
        content_type="application/pdf",
        size_bytes=10,
        created_at=datetime.now(tz=timezone.utc),
        workflow_status="running",
        workflow_version=3,
        failed_step=None,
        failure_reason=None,
        partner_job_id=None,
        step_results={},
        tasks=(),
    )


class FakeEventStream:
    def __init__(self, events: list[dict]):
        self._events = events

    @asynccontextmanager
    async def subscribe(self, *, tenant_id, document_id):
        async def _gen():
            for event in self._events:
                yield event

        yield _gen()


def _client(reader, event_stream) -> TestClient:
    app.dependency_overrides[get_current_user_from_query_token] = lambda: AuthContext(tenant_id=TENANT, user=USER)
    app.dependency_overrides[get_document_reader] = lambda: reader
    app.dependency_overrides[get_event_stream] = lambda: event_stream
    return TestClient(app)


def _data_payloads(sse_body: str) -> list[dict]:
    return [json.loads(line[len("data:"):].strip()) for line in sse_body.splitlines() if line.startswith("data:")]


def test_unknown_document_is_404():
    try:
        client = _client(reader=lambda doc_id, tenant_id: None, event_stream=FakeEventStream([]))
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404


def test_streams_events_until_the_stream_ends():
    events = [
        {"step": "ocr", "step_status": "running", "workflow_status": "running"},
        {"step": "ocr", "step_status": "succeeded", "workflow_status": "running"},
    ]
    try:
        client = _client(reader=lambda doc_id, tenant_id: _detail_row(), event_stream=FakeEventStream(events))
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    assert _data_payloads(r.text) == events


class _ExplodingEventStream:
    """Fails the test if the endpoint subscribes — used to prove the
    already-terminal short-circuit never reaches the live subscription."""

    @asynccontextmanager
    async def subscribe(self, *, tenant_id, document_id):
        raise AssertionError("must not subscribe when the workflow is already terminal")
        yield  # pragma: no cover


def test_already_terminal_workflow_emits_terminal_once_and_closes():
    """Regression: a client connecting on an already-finished workflow would
    hang forever (no further event will be published, the terminal check never
    fires). It must instead receive the terminal status once and close."""
    row = _detail_row()
    terminal_row = DocumentDetailRow(**{**row.__dict__, "workflow_status": "succeeded"})
    try:
        client = _client(reader=lambda doc_id, tenant_id: terminal_row, event_stream=_ExplodingEventStream())
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    payloads = _data_payloads(r.text)
    assert payloads == [{"workflow_status": "succeeded", "failed_step": None, "failure_reason": None}]


def test_closes_at_terminal_status_ignoring_later_events():
    events = [
        {"step": "external_call", "step_status": "running", "workflow_status": "running"},
        {"step": "external_call", "step_status": "succeeded", "workflow_status": "succeeded"},
        {"step": "should_not_appear", "step_status": "running", "workflow_status": "running"},
    ]
    try:
        client = _client(reader=lambda doc_id, tenant_id: _detail_row(), event_stream=FakeEventStream(events))
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    payloads = _data_payloads(r.text)
    assert payloads == events[:2]  # stopped right after the terminal (succeeded) event
    assert all(p["step"] != "should_not_appear" for p in payloads)
