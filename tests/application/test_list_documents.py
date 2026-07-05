import uuid
from datetime import datetime, timezone

from src.application.list_documents import ListDocumentsQuery, ListDocumentsUseCase
from src.ports.document_data_source import DocumentRow
from tests.fakes import FakeDocumentDataSource

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
USER_1 = uuid.uuid4()
USER_2 = uuid.uuid4()


def _row(
    tenant_id: uuid.UUID,
    uploaded_by: uuid.UUID,
    filename: str,
    first_name: str = "Alice",
    last_name: str = "Test",
    **kwargs,
) -> DocumentRow:
    return DocumentRow(
        document_id=uuid.uuid4(),
        tenant_id=tenant_id,
        uploaded_by=uploaded_by,
        uploaded_by_first_name=first_name,
        uploaded_by_last_name=last_name,
        filename=filename,
        content_type="application/pdf",
        size_bytes=1024,
        created_at=datetime.now(tz=timezone.utc),
        workflow_status=kwargs.get("workflow_status", "running"),
        failed_step=kwargs.get("failed_step", None),
        failure_reason=kwargs.get("failure_reason", None),
    )


def test_returns_documents_for_tenant():
    ds = FakeDocumentDataSource()
    ds.add(_row(TENANT_A, USER_1, "a.pdf"))
    ds.add(_row(TENANT_A, USER_1, "b.pdf"))

    result = ListDocumentsUseCase(ds).execute(ListDocumentsQuery(tenant_id=TENANT_A))

    assert len(result.documents) == 2
    assert {d.filename for d in result.documents} == {"a.pdf", "b.pdf"}


def test_user_can_see_documents_uploaded_by_other_users_in_same_tenant():
    ds = FakeDocumentDataSource()
    ds.add(_row(TENANT_A, USER_1, "user1_doc.pdf", first_name="Alice", last_name="Smith"))
    ds.add(_row(TENANT_A, USER_2, "user2_doc.pdf", first_name="Bob", last_name="Jones"))

    result = ListDocumentsUseCase(ds).execute(ListDocumentsQuery(tenant_id=TENANT_A))

    uploaded_bys = {d.uploaded_by for d in result.documents}
    assert USER_1 in uploaded_bys
    assert USER_2 in uploaded_bys


def test_uploaded_by_includes_full_name():
    ds = FakeDocumentDataSource()
    ds.add(_row(TENANT_A, USER_1, "doc.pdf", first_name="Alice", last_name="Smith"))

    result = ListDocumentsUseCase(ds).execute(ListDocumentsQuery(tenant_id=TENANT_A))

    doc = result.documents[0]
    assert doc.uploaded_by_first_name == "Alice"
    assert doc.uploaded_by_last_name == "Smith"


def test_tenant_isolation_other_tenant_documents_not_visible():
    ds = FakeDocumentDataSource()
    ds.add(_row(TENANT_A, USER_1, "tenant_a.pdf"))
    ds.add(_row(TENANT_B, USER_2, "tenant_b.pdf"))

    result = ListDocumentsUseCase(ds).execute(ListDocumentsQuery(tenant_id=TENANT_A))

    assert len(result.documents) == 1
    assert result.documents[0].filename == "tenant_a.pdf"


def test_returns_empty_list_when_no_documents():
    ds = FakeDocumentDataSource()
    result = ListDocumentsUseCase(ds).execute(ListDocumentsQuery(tenant_id=TENANT_A))
    assert result.documents == []


def test_document_row_contains_workflow_status():
    ds = FakeDocumentDataSource()
    ds.add(_row(TENANT_A, USER_1, "doc.pdf", workflow_status="succeeded"))

    result = ListDocumentsUseCase(ds).execute(ListDocumentsQuery(tenant_id=TENANT_A))

    assert result.documents[0].workflow_status == "succeeded"


def test_document_row_contains_failure_info_when_failed():
    ds = FakeDocumentDataSource()
    ds.add(_row(
        TENANT_A, USER_1, "broken.pdf",
        workflow_status="failed",
        failed_step="ocr",
        failure_reason="timeout after 3 attempts",
    ))

    result = ListDocumentsUseCase(ds).execute(ListDocumentsQuery(tenant_id=TENANT_A))

    doc = result.documents[0]
    assert doc.workflow_status == "failed"
    assert doc.failed_step == "ocr"
    assert doc.failure_reason == "timeout after 3 attempts"
