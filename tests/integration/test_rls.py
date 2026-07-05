"""Suite 1 — Row-Level Security, exercised as the non-superuser `primmo_app`
role (the only way RLS actually applies). These behaviours are impossible to
cover with the fake-based unit tests — they live entirely in Postgres."""
import pytest

pytestmark = pytest.mark.integration

import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.adapters.in_memory.blob_store import InMemoryBlobStore
from src.adapters.sql.document_data_source import SqlAlchemyDocumentDataSource
from src.adapters.sql.rls import TENANT_BYPASS, scope_session_to_tenant
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.application.ingest_document import IngestDocumentCommand, IngestDocumentUseCase
from tests.fakes import FakeWorkflowDispatcher
from tests.integration._helpers import persist_document
from tests.integration.conftest import TENANT_A, TENANT_B, USER_A, USER_B


def _count_documents(session_factory, doc_id, *, tenant=None) -> int:
    session = session_factory()
    try:
        if tenant is not None:
            scope_session_to_tenant(session, tenant)
        return session.execute(text("SELECT count(*) FROM documents WHERE id = :id"), {"id": doc_id}).scalar()
    finally:
        session.close()


def test_read_isolation_between_tenants(app_sessionmaker):
    doc_id = persist_document(app_sessionmaker, tenant_id=TENANT_A, uploaded_by=USER_A)
    assert _count_documents(app_sessionmaker, doc_id, tenant=TENANT_A) == 1  # owner sees it
    assert _count_documents(app_sessionmaker, doc_id, tenant=TENANT_B) == 0  # other tenant does not


def test_unset_guc_is_fail_closed(app_sessionmaker):
    doc_id = persist_document(app_sessionmaker, tenant_id=TENANT_A, uploaded_by=USER_A)
    # No scope set → current_setting(...) is NULL → the policy matches nothing.
    assert _count_documents(app_sessionmaker, doc_id, tenant=None) == 0


def test_bypass_sees_all_tenants(app_sessionmaker):
    doc_id = persist_document(app_sessionmaker, tenant_id=TENANT_A, uploaded_by=USER_A)
    assert _count_documents(app_sessionmaker, doc_id, tenant=TENANT_BYPASS) == 1


def test_write_check_rejects_inserting_a_row_for_another_tenant(app_sessionmaker):
    session = app_sessionmaker()
    try:
        scope_session_to_tenant(session, TENANT_A)  # acting as A
        with pytest.raises(DBAPIError):  # WITH CHECK violation: a row for B while scoped to A
            session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, uploaded_by, filename, content_type, size_bytes, blob_key, created_at) "
                    "VALUES (:id, :tid, :uid, 'x', 'text/plain', 1, 'b', now())"
                ),
                {"id": uuid.uuid4(), "tid": TENANT_B, "uid": USER_B},
            )
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_ingest_use_case_auto_scopes_and_rls_hides_it_from_other_tenants(app_sessionmaker):
    """End-to-end: WriteUseCase auto-scopes the write to the command's tenant,
    RLS persists only for that tenant, and the data source (scoped by its
    tenant_id arg) can't read it as another tenant."""
    result = IngestDocumentUseCase(
        SqlAlchemyUnitOfWork(app_sessionmaker), InMemoryBlobStore(), FakeWorkflowDispatcher()
    ).execute(
        IngestDocumentCommand(
            tenant_id=TENANT_A, uploaded_by=USER_A, filename="invoice.pdf",
            content_type="application/pdf", size_bytes=3, file_content=b"pdf",
        )
    )

    session = app_sessionmaker()
    try:
        data_source = SqlAlchemyDocumentDataSource(session)
        assert data_source.get_by_id(result.document_id, tenant_id=TENANT_A) is not None
        assert data_source.get_by_id(result.document_id, tenant_id=TENANT_B) is None  # RLS + query scope
    finally:
        session.close()
