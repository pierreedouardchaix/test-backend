"""Endpoint-level checks: the DomainError handlers (404) and the /results
status semantics (failed vs still-running vs succeeded)."""
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.auth import AuthContext, get_current_user
from src.dependencies import get_document_data_source
from src.domain.models.user import User
from src.main import app
from src.ports.document_data_source import DocumentDetailRow
from tests.fakes import FakeDocumentDataSource

TENANT = uuid.uuid4()
USER = User(id=uuid.uuid4(), first_name="Alice", last_name="Acme")


def _detail(document_id, *, workflow_status, failed_step=None, failure_reason=None, step_results=None):
    return DocumentDetailRow(
        document_id=document_id, tenant_id=TENANT, uploaded_by=USER.id,
        uploaded_by_first_name="Alice", uploaded_by_last_name="Acme",
        filename="doc.pdf", content_type="application/pdf", size_bytes=10,
        created_at=datetime.now(tz=timezone.utc),
        workflow_status=workflow_status, workflow_version=1,
        failed_step=failed_step, failure_reason=failure_reason,
        partner_job_id=None, step_results=step_results or {}, tasks=(),
    )


def _client(data_source) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: AuthContext(tenant_id=TENANT, user=USER)
    app.dependency_overrides[get_document_data_source] = lambda: data_source
    return TestClient(app)


def test_get_unknown_document_is_404_via_the_domain_error_handler():
    data_source = FakeDocumentDataSource()  # empty → get_by_id returns None → DocumentNotFound
    try:
        r = _client(data_source).get(f"/documents/{uuid.uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404
    assert "detail" in r.json()


def test_results_of_a_failed_document_reports_the_failure_not_not_yet_available():
    doc_id = uuid.uuid4()
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(doc_id, workflow_status="failed", failed_step="chunking", failure_reason="chunking failed"))
    try:
        r = _client(ds).get(f"/documents/{doc_id}/results")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "failed" in detail and "chunking" in detail  # terminal failure, not "not yet available"


def test_results_of_a_running_document_is_still_processing():
    doc_id = uuid.uuid4()
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(doc_id, workflow_status="running"))
    try:
        r = _client(ds).get(f"/documents/{doc_id}/results")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 409
    assert "still processing" in r.json()["detail"]


def test_results_of_a_succeeded_document_returns_the_step_results():
    doc_id = uuid.uuid4()
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(doc_id, workflow_status="succeeded", step_results={"ocr": "blob-1"}))
    try:
        r = _client(ds).get(f"/documents/{doc_id}/results")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.json()["step_results"] == {"ocr": "blob-1"}
