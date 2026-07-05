"""SSE endpoint tests: ownership gate + snapshot-after-subscribe + version
filtering + terminal close.

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
SNAPSHOT_VERSION = 3


@pytest.fixture(autouse=True)
def _reset_sse_app_status():
    """sse-starlette keeps a module-global exit Event bound to the first event
    loop it sees; TestClient spins a fresh loop per request, so reset it before
    each test. Purely a test-harness concern — a real uvicorn process has one
    long-lived loop."""
    sse_starlette.sse.AppStatus.should_exit_event = None
    yield


def _detail_row(workflow_status: str = "running", version: int = SNAPSHOT_VERSION) -> DocumentDetailRow:
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
        workflow_status=workflow_status,
        workflow_version=version,
        failed_step=None,
        failure_reason=None,
        partner_job_id=None,
        step_results={},
        tasks=(),
    )


class FakeEventStream:
    def __init__(self, events: list[dict]):
        self._events = events
        self.subscribed = False

    @asynccontextmanager
    async def subscribe(self, *, tenant_id, document_id):
        self.subscribed = True

        async def _gen():
            for event in self._events:
                yield event

        yield _gen()


def _client(reader, event_stream) -> TestClient:
    app.dependency_overrides[get_current_user_from_query_token] = lambda: AuthContext(tenant_id=TENANT, user=USER)
    app.dependency_overrides[get_document_reader] = lambda: reader
    app.dependency_overrides[get_event_stream] = lambda: event_stream
    return TestClient(app)


def _sse_events(body: str) -> list[tuple[str, dict]]:
    """Parse the SSE body into (event_type, data) pairs."""
    events: list[tuple[str, dict]] = []
    event_type = "message"
    for line in body.splitlines():
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((event_type, json.loads(line[len("data:"):].strip())))
            event_type = "message"
    return events


def test_unknown_document_is_404():
    try:
        client = _client(reader=lambda doc_id, tenant_id: None, event_stream=FakeEventStream([]))
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404


def test_snapshot_is_the_first_event_then_live_deltas():
    events = [
        {"step": "ocr", "step_status": "running", "workflow_status": "running", "version": 4},
        {"step": "ocr", "step_status": "succeeded", "workflow_status": "running", "version": 5},
    ]
    try:
        client = _client(reader=lambda doc_id, tenant_id: _detail_row(), event_stream=FakeEventStream(events))
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    parsed = _sse_events(r.text)
    assert parsed[0] == ("snapshot", {
        "workflow_status": "running", "version": SNAPSHOT_VERSION, "failed_step": None, "failure_reason": None,
    })
    assert parsed[1:] == [("step", events[0]), ("step", events[1])]


def test_events_at_or_below_snapshot_version_are_dropped():
    events = [
        {"step": "ocr", "step_status": "running", "workflow_status": "running", "version": SNAPSHOT_VERSION},  # == snapshot
        {"step": "metadata", "step_status": "running", "workflow_status": "running", "version": 2},  # older
        {"step": "metadata", "step_status": "succeeded", "workflow_status": "running", "version": 4},  # newer → kept
    ]
    try:
        client = _client(reader=lambda doc_id, tenant_id: _detail_row(), event_stream=FakeEventStream(events))
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    step_events = [data for kind, data in _sse_events(r.text) if kind == "step"]
    assert step_events == [events[2]]  # only the version-4 event survives the filter


def test_already_terminal_workflow_snapshot_is_the_whole_stream():
    """A client connecting on an already-finished workflow must not hang: the
    snapshot carries the terminal status and the stream closes right after
    (no live events will ever come)."""
    stream = FakeEventStream([{"step": "late", "workflow_status": "running", "version": 99}])
    try:
        client = _client(reader=lambda doc_id, tenant_id: _detail_row(workflow_status="succeeded"), event_stream=stream)
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    parsed = _sse_events(r.text)
    assert len(parsed) == 1
    kind, data = parsed[0]
    assert kind == "snapshot"
    assert data["workflow_status"] == "succeeded"


def test_closes_at_terminal_status_ignoring_later_events():
    events = [
        {"step": "external_call", "step_status": "running", "workflow_status": "running", "version": 4},
        {"step": "external_call", "step_status": "succeeded", "workflow_status": "succeeded", "version": 5},
        {"step": "should_not_appear", "step_status": "running", "workflow_status": "running", "version": 6},
    ]
    try:
        client = _client(reader=lambda doc_id, tenant_id: _detail_row(), event_stream=FakeEventStream(events))
        r = client.get(f"/documents/{DOC_ID}/events?token=x")
    finally:
        app.dependency_overrides.clear()

    step_events = [data for kind, data in _sse_events(r.text) if kind == "step"]
    assert step_events == events[:2]  # stopped right after the terminal (succeeded) event
    assert all(e["step"] != "should_not_appear" for e in step_events)
