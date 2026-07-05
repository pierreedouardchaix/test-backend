"""Suite 6 — the read-side DataSource against real SQL: pagination and the
multi-table join shape of the detail query."""
import pytest

pytestmark = pytest.mark.integration

from src.adapters.sql.document_data_source import SqlAlchemyDocumentDataSource
from tests.integration._helpers import persist_document
from tests.integration.conftest import TENANT_A, TENANT_B, USER_A


def test_pagination_returns_disjoint_pages_covering_all_rows(app_sessionmaker, app_session):
    ids = {persist_document(app_sessionmaker, tenant_id=TENANT_A, uploaded_by=USER_A, filename=f"d{i}.pdf") for i in range(5)}
    data_source = SqlAlchemyDocumentDataSource(app_session)

    page1 = data_source.list_by_tenant(TENANT_A, limit=2, offset=0)
    page2 = data_source.list_by_tenant(TENANT_A, limit=2, offset=2)
    page3 = data_source.list_by_tenant(TENANT_A, limit=2, offset=4)

    assert [len(page1), len(page2), len(page3)] == [2, 2, 1]
    seen = [r.document_id for r in (*page1, *page2, *page3)]
    assert len(seen) == len(set(seen)) == 5  # disjoint pages...
    assert set(seen) == ids                  # ...covering exactly the inserted rows


def test_offset_past_the_end_is_empty(app_sessionmaker, app_session):
    persist_document(app_sessionmaker, tenant_id=TENANT_A, uploaded_by=USER_A)
    assert SqlAlchemyDocumentDataSource(app_session).list_by_tenant(TENANT_A, limit=10, offset=10) == []


def test_get_by_id_joins_document_workflow_and_user(app_sessionmaker, app_session):
    doc_id = persist_document(app_sessionmaker, tenant_id=TENANT_A, uploaded_by=USER_A, filename="invoice.pdf")

    row = SqlAlchemyDocumentDataSource(app_session).get_by_id(doc_id, tenant_id=TENANT_A)

    assert row is not None
    assert row.filename == "invoice.pdf"
    assert row.uploaded_by == USER_A
    assert row.uploaded_by_first_name == "Alice"  # joined from users (seeded in conftest)
    assert row.workflow_status == "running"
    assert row.workflow_version >= 1
    assert row.partner_job_id is None  # nothing deferred yet
    assert row.tasks == ()


def test_get_by_id_is_none_for_another_tenant(app_sessionmaker, app_session):
    doc_id = persist_document(app_sessionmaker, tenant_id=TENANT_A, uploaded_by=USER_A)
    assert SqlAlchemyDocumentDataSource(app_session).get_by_id(doc_id, tenant_id=TENANT_B) is None
