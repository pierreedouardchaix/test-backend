"""Suite 3 — the domain ↔ ORM ↔ Postgres round-trip: every field a real save
then reload must preserve (statuses, errors JSONB, partner_job_id, version,
tz-aware timestamps), plus the cross-tenant lookup and the unique index."""
import pytest

pytestmark = pytest.mark.integration

from sqlalchemy.exc import IntegrityError

from src.adapters.sql.rls import TENANT_BYPASS, scope_session_to_tenant
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.adapters.sql.workflow_repository import SqlAlchemyWorkflowRepository
from src.application.ingest_document import PRIMMO_DEFINITION
from src.domain.models.document import Document
from src.domain.models.task import TaskStatus
from src.domain.models.workflow import Workflow, WorkflowStatus
from tests.integration.conftest import TENANT_A, USER_A


def _build_realistic_workflow(partner_job_id="j_roundtrip01"):
    """ocr succeeded; metadata failed once (retry) then succeeded; external_call
    deferred with a partner job id — a spread of task states."""
    doc = Document.create(
        tenant_id=TENANT_A, uploaded_by=USER_A, filename="f", content_type="text/plain", size_bytes=1, blob_key="b"
    )
    wf = Workflow.create(id=doc.id, tenant_id=TENANT_A, definition=PRIMMO_DEFINITION)
    wf.start_task("ocr")
    wf.on_task_succeeded("ocr", "blob-ocr")
    wf.start_task("metadata")
    wf.on_task_failed("metadata", "transient blip")  # → RETRYING (records an error)
    wf.start_task("metadata")
    wf.on_task_succeeded("metadata", "blob-meta")
    wf.start_task("chunking")
    wf.on_task_succeeded("chunking", "blob-chunk")
    wf.start_task("external_call")
    wf.mark_task_deferred("external_call", partner_job_id)
    return doc, wf


def _persist(session_factory, doc, wf):
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.scope_to_tenant(TENANT_A)
        uow.documents.save(doc)
        uow.workflows.save(wf)
        uow.commit()


def test_workflow_and_tasks_round_trip_through_postgres(app_sessionmaker):
    doc, wf = _build_realistic_workflow()
    _persist(app_sessionmaker, doc, wf)

    session = app_sessionmaker()
    try:
        scope_session_to_tenant(session, TENANT_A)
        reloaded = SqlAlchemyWorkflowRepository(session).get(wf.id, tenant_id=TENANT_A)
    finally:
        session.close()

    assert reloaded.status == WorkflowStatus.RUNNING
    assert reloaded.results == {"ocr": "blob-ocr", "metadata": "blob-meta", "chunking": "blob-chunk"}

    meta = reloaded.tasks["metadata"]
    assert meta.status == TaskStatus.SUCCEEDED
    assert meta.attempts == 2
    assert [e.error for e in meta.errors] == ["transient blip"]
    assert meta.errors[0].occurred_at.tzinfo is not None  # tz-aware timestamp survived

    ext = reloaded.tasks["external_call"]
    assert ext.status == TaskStatus.RUNNING
    assert ext.partner_job_id == "j_roundtrip01"


def test_get_by_partner_job_id_resolves_cross_tenant(app_sessionmaker):
    doc, wf = _build_realistic_workflow(partner_job_id="j_lookup42")
    _persist(app_sessionmaker, doc, wf)

    session = app_sessionmaker()
    try:
        scope_session_to_tenant(session, TENANT_BYPASS)  # webhook path has no tenant context
        found = SqlAlchemyWorkflowRepository(session).get_by_partner_job_id("j_lookup42")
    finally:
        session.close()

    assert found is not None and found.id == wf.id


def test_partner_job_id_is_unique_across_tasks(app_sessionmaker):
    doc1, wf1 = _build_realistic_workflow(partner_job_id="j_dup")
    _persist(app_sessionmaker, doc1, wf1)

    doc2, wf2 = _build_realistic_workflow(partner_job_id="j_dup")  # same partner job id → collides
    with pytest.raises(IntegrityError):
        _persist(app_sessionmaker, doc2, wf2)
