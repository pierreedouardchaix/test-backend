import uuid
from dataclasses import dataclass

import pytest

from src.application.ingest_document import (
    PRIMMO_DEFINITION,
    IngestDocumentCommand,
    IngestDocumentUseCase,
)
from tests.fakes import FakeBlobStore, FakeUnitOfWork, FakeWorkflowDispatcher

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()

COMMAND = IngestDocumentCommand(
    tenant_id=TENANT_ID,
    uploaded_by=USER_ID,
    filename="invoice.pdf",
    content_type="application/pdf",
    size_bytes=1024,
    file_content=b"%PDF-1.4 fake content",
)


def _make_use_case():
    uow = FakeUnitOfWork()
    blob_store = FakeBlobStore()
    dispatcher = FakeWorkflowDispatcher()
    use_case = IngestDocumentUseCase(uow, blob_store, dispatcher)
    return use_case, uow, blob_store, dispatcher


def test_returns_document_id_and_running_status():
    use_case, *_ = _make_use_case()
    result = use_case.execute(COMMAND)
    assert result.document_id is not None
    assert result.workflow_status == "running"


def test_document_persisted_with_correct_metadata():
    use_case, uow, *_ = _make_use_case()
    result = use_case.execute(COMMAND)

    doc = uow.documents.get(result.document_id, tenant_id=TENANT_ID)
    assert doc is not None
    assert doc.filename == "invoice.pdf"
    assert doc.content_type == "application/pdf"
    assert doc.tenant_id == TENANT_ID
    assert doc.uploaded_by == USER_ID


def test_workflow_persisted_with_primmo_definition():
    use_case, uow, *_ = _make_use_case()
    result = use_case.execute(COMMAND)

    workflow = uow.workflows.get(result.document_id, tenant_id=TENANT_ID)
    assert workflow is not None
    assert workflow.id == result.document_id
    assert workflow.definition == PRIMMO_DEFINITION
    assert workflow.tenant_id == TENANT_ID


def test_document_id_equals_workflow_id():
    use_case, uow, *_ = _make_use_case()
    result = use_case.execute(COMMAND)

    doc = uow.documents.get(result.document_id, tenant_id=TENANT_ID)
    workflow = uow.workflows.get(result.document_id, tenant_id=TENANT_ID)
    assert doc.id == workflow.id


def test_file_content_stored_in_blob_store():
    use_case, uow, blob_store, _ = _make_use_case()
    result = use_case.execute(COMMAND)

    doc = uow.documents.get(result.document_id, tenant_id=TENANT_ID)
    assert doc.blob_key in blob_store.blobs
    assert blob_store.blobs[doc.blob_key] == b"%PDF-1.4 fake content"


def test_document_blob_key_references_stored_content():
    use_case, uow, blob_store, _ = _make_use_case()
    result = use_case.execute(COMMAND)

    doc = uow.documents.get(result.document_id, tenant_id=TENANT_ID)
    assert blob_store.get(doc.blob_key) == COMMAND.file_content


def test_uow_committed():
    use_case, uow, *_ = _make_use_case()
    use_case.execute(COMMAND)
    assert uow.committed is True


def test_workflow_dispatched_after_commit():
    use_case, uow, _, dispatcher = _make_use_case()
    result = use_case.execute(COMMAND)

    assert len(dispatcher.dispatched) == 1
    assert dispatcher.dispatched[0]["workflow_id"] == result.document_id
    assert dispatcher.dispatched[0]["tenant_id"] == TENANT_ID


def test_dispatch_not_called_if_execution_fails():
    use_case, uow, blob_store, dispatcher = _make_use_case()
    # Force a failure by passing non-bytes file content
    bad_command = IngestDocumentCommand(
        tenant_id=TENANT_ID,
        uploaded_by=USER_ID,
        filename="bad.pdf",
        content_type="application/pdf",
        size_bytes=0,
        file_content=None,  # BlobStore.put will fail
    )
    with pytest.raises(Exception):
        use_case.execute(bad_command)

    assert dispatcher.dispatched == []


def test_tenant_isolation_document_not_visible_to_other_tenant():
    use_case, uow, *_ = _make_use_case()
    result = use_case.execute(COMMAND)

    other_tenant = uuid.uuid4()
    assert uow.documents.get(result.document_id, tenant_id=other_tenant) is None
    assert uow.workflows.get(result.document_id, tenant_id=other_tenant) is None
