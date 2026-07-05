import uuid
from datetime import datetime, timezone

import pytest

from src.application.get_document import DocumentNotFound, GetDocumentQuery, GetDocumentUseCase
from src.ports.document_data_source import DocumentDetailRow, TaskRow
from tests.fakes import FakeDocumentDataSource

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
USER_ID = uuid.uuid4()
DOC_ID = uuid.uuid4()


def _detail(
    document_id: uuid.UUID = DOC_ID,
    tenant_id: uuid.UUID = TENANT_A,
    workflow_status: str = "running",
    tasks: tuple[TaskRow, ...] = (),
    failed_step: str | None = None,
    failure_reason: str | None = None,
    step_results: dict | None = None,
    partner_job_id: str | None = None,
) -> DocumentDetailRow:
    return DocumentDetailRow(
        document_id=document_id,
        tenant_id=tenant_id,
        uploaded_by=USER_ID,
        uploaded_by_first_name="Alice",
        uploaded_by_last_name="Smith",
        filename="invoice.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        created_at=datetime.now(tz=timezone.utc),
        workflow_status=workflow_status,
        workflow_version=1,
        failed_step=failed_step,
        failure_reason=failure_reason,
        partner_job_id=partner_job_id,
        step_results=step_results or {},
        tasks=tasks,
    )


def _task(step_name: str, status: str = "succeeded", attempts: int = 1, last_error: str | None = None) -> TaskRow:
    return TaskRow(
        step_name=step_name,
        status=status,
        attempts=attempts,
        max_attempts=3,
        last_error=last_error,
        started_at=datetime.now(tz=timezone.utc),
        finished_at=datetime.now(tz=timezone.utc),
    )


def test_returns_document_detail():
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail())

    result = GetDocumentUseCase(ds).execute(GetDocumentQuery(document_id=DOC_ID, tenant_id=TENANT_A))

    assert result.document_id == DOC_ID
    assert result.filename == "invoice.pdf"
    assert result.uploaded_by_first_name == "Alice"
    assert result.uploaded_by_last_name == "Smith"


def test_exposes_partner_job_id_for_the_webhook_tester():
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(partner_job_id="j_abc123def4567890"))

    result = GetDocumentUseCase(ds).execute(GetDocumentQuery(document_id=DOC_ID, tenant_id=TENANT_A))

    assert result.partner_job_id == "j_abc123def4567890"


def test_returns_workflow_status():
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(workflow_status="succeeded"))

    result = GetDocumentUseCase(ds).execute(GetDocumentQuery(document_id=DOC_ID, tenant_id=TENANT_A))

    assert result.workflow_status == "succeeded"


def test_returns_tasks_with_status():
    tasks = (_task("ocr", "succeeded"), _task("metadata", "running", attempts=2))
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(tasks=tasks))

    result = GetDocumentUseCase(ds).execute(GetDocumentQuery(document_id=DOC_ID, tenant_id=TENANT_A))

    assert len(result.tasks) == 2
    assert result.tasks[0].step_name == "ocr"
    assert result.tasks[0].status == "succeeded"
    assert result.tasks[1].step_name == "metadata"
    assert result.tasks[1].attempts == 2


def test_failed_task_exposes_last_error():
    tasks = (_task("ocr", "failed", attempts=3, last_error="timeout"),)
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(workflow_status="failed", failed_step="ocr", failure_reason="max attempts reached", tasks=tasks))

    result = GetDocumentUseCase(ds).execute(GetDocumentQuery(document_id=DOC_ID, tenant_id=TENANT_A))

    assert result.failed_step == "ocr"
    assert result.failure_reason == "max attempts reached"
    assert result.tasks[0].last_error == "timeout"


def test_raises_not_found_for_unknown_document():
    ds = FakeDocumentDataSource()

    with pytest.raises(DocumentNotFound):
        GetDocumentUseCase(ds).execute(GetDocumentQuery(document_id=uuid.uuid4(), tenant_id=TENANT_A))


def test_raises_not_found_for_other_tenant():
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(tenant_id=TENANT_A))

    with pytest.raises(DocumentNotFound):
        GetDocumentUseCase(ds).execute(GetDocumentQuery(document_id=DOC_ID, tenant_id=TENANT_B))


def test_document_with_no_tasks_yet():
    ds = FakeDocumentDataSource()
    ds.add_detail(_detail(workflow_status="running", tasks=()))

    result = GetDocumentUseCase(ds).execute(GetDocumentQuery(document_id=DOC_ID, tenant_id=TENANT_A))

    assert result.tasks == ()
