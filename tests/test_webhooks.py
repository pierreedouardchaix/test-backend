"""Partner webhook endpoint — transport only: verify, parse, gate on existence,
enqueue and return 202. The actual application of the callback is a Celery task,
covered by test_apply_partner_callback.py."""
import json

import pytest
from fastapi.testclient import TestClient

from src.dependencies import get_partner_callback_dispatcher, get_partner_job_resolver, get_settings
from src.main import app
from src.partner_hmac import sign
from src.settings import Settings
from tests.fakes import FakePartnerCallbackDispatcher

SECRET = "partner-secret"
PARTNER_JOB_ID = "j_abc123def4567890"

_settings = Settings(
    database_url="unused",
    jwt_secret="unused",
    jwt_expiry_seconds=3600,
    dev_mode=True,
    partner_hmac_secret=SECRET,
)


def _client(dispatcher: FakePartnerCallbackDispatcher, *, task_status: str | None = "running") -> TestClient:
    app.dependency_overrides[get_settings] = lambda: _settings
    app.dependency_overrides[get_partner_job_resolver] = lambda: (lambda partner_job_id: task_status)
    app.dependency_overrides[get_partner_callback_dispatcher] = lambda: dispatcher
    return TestClient(app)


def _post_signed(client: TestClient, body: dict):
    raw = json.dumps(body).encode()
    return client.post("/webhooks/partner", content=raw, headers={"X-Partner-Signature": sign(raw, secret=SECRET)})


def test_signed_completed_callback_is_accepted_and_enqueued():
    dispatcher = FakePartnerCallbackDispatcher()
    try:
        r = _post_signed(_client(dispatcher), {"job_id": PARTNER_JOB_ID, "status": "completed", "result": {"indexed": True}})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 202
    assert r.json() == {"status": "accepted"}
    assert len(dispatcher.dispatched) == 1
    command = dispatcher.dispatched[0]
    assert command.partner_job_id == PARTNER_JOB_ID
    assert command.step_name == "external_call"
    assert command.succeeded is True
    assert command.result == {"indexed": True}


def test_failed_callback_is_accepted_and_enqueued():
    dispatcher = FakePartnerCallbackDispatcher()
    try:
        r = _post_signed(_client(dispatcher), {"job_id": PARTNER_JOB_ID, "status": "failed", "error": "partner boom"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 202
    command = dispatcher.dispatched[0]
    assert command.succeeded is False
    assert command.error == "partner boom"


def test_invalid_signature_is_401_and_not_enqueued():
    dispatcher = FakePartnerCallbackDispatcher()
    try:
        client = _client(dispatcher)
        raw = json.dumps({"job_id": PARTNER_JOB_ID, "status": "completed"}).encode()
        r = client.post("/webhooks/partner", content=raw, headers={"X-Partner-Signature": "deadbeef"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 401
    assert dispatcher.dispatched == []  # bad signature never reaches the dispatcher


def test_unknown_job_id_is_404_and_not_enqueued():
    dispatcher = FakePartnerCallbackDispatcher()
    try:
        r = _post_signed(_client(dispatcher, task_status=None), {"job_id": "j_unknown", "status": "completed"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404
    assert dispatcher.dispatched == []


def test_callback_on_an_already_terminal_task_is_200_already_processed_not_enqueued():
    """A duplicate/late webhook on a finalized job is acknowledged synchronously
    (200) without enqueuing — the caller sees it's a no-op instead of an opaque
    202. Tested for both a succeeded and a failed task."""
    for terminal in ("succeeded", "failed"):
        dispatcher = FakePartnerCallbackDispatcher()
        try:
            r = _post_signed(_client(dispatcher, task_status=terminal), {"job_id": PARTNER_JOB_ID, "status": "completed"})
        finally:
            app.dependency_overrides.clear()

        assert r.status_code == 200
        assert r.json() == {"status": "already_processed"}
        assert dispatcher.dispatched == []


def test_bad_status_is_422_and_not_enqueued():
    dispatcher = FakePartnerCallbackDispatcher()
    try:
        r = _post_signed(_client(dispatcher), {"job_id": PARTNER_JOB_ID, "status": "in_progress"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 422
    assert dispatcher.dispatched == []


def test_oversized_body_is_413():
    dispatcher = FakePartnerCallbackDispatcher()
    try:
        client = _client(dispatcher)
        big_body = b"x" * (64 * 1024 + 1)
        r = client.post("/webhooks/partner", content=big_body, headers={"X-Partner-Signature": "irrelevant"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 413


def test_error_field_truncated_above_2000_chars_is_422():
    dispatcher = FakePartnerCallbackDispatcher()
    try:
        r = _post_signed(_client(dispatcher), {"job_id": PARTNER_JOB_ID, "status": "failed", "error": "x" * 2001})
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
