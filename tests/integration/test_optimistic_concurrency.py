"""Suite 2 — optimistic concurrency on the Workflow aggregate. Only a real DB
models the version conflict: two sessions load the same version, one commits,
the other's UPDATE ... WHERE version=v touches 0 rows → ConcurrencyError. This is
the mechanism the whole fan-in design rests on, and the fakes can't reproduce it."""
import pytest

pytestmark = pytest.mark.integration


from src.adapters.sql.rls import scope_session_to_tenant
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.adapters.sql.workflow_repository import SqlAlchemyWorkflowRepository
from src.application.concurrency import ConcurrencyError
from src.application.ingest_document import PRIMMO_DEFINITION
from src.domain.models.document import Document
from src.domain.models.workflow import Workflow
from tests.integration._helpers import persist_document
from tests.integration.conftest import TENANT_A, USER_A


def _persist_fan_in_workflow(session_factory):
    """ocr done, metadata + chunking both dispatched (RUNNING) — the state just
    before the fan-in into external_call."""
    doc = Document.create(
        tenant_id=TENANT_A, uploaded_by=USER_A, filename="f", content_type="text/plain", size_bytes=1, blob_key="b"
    )
    workflow = Workflow.create(id=doc.id, tenant_id=TENANT_A, definition=PRIMMO_DEFINITION)
    workflow.start_task("ocr")
    workflow.on_task_succeeded("ocr", "blob-ocr")
    workflow.start_task("metadata")
    workflow.start_task("chunking")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.scope_to_tenant(TENANT_A)
        uow.documents.save(doc)
        uow.workflows.save(workflow)
        uow.commit()
    return doc.id


def test_a_stale_save_raises_concurrency_error(app_sessionmaker):
    workflow_id = persist_document(app_sessionmaker, tenant_id=TENANT_A, uploaded_by=USER_A)
    s1, s2 = app_sessionmaker(), app_sessionmaker()
    try:
        scope_session_to_tenant(s1, TENANT_A)
        scope_session_to_tenant(s2, TENANT_A)
        r1, r2 = SqlAlchemyWorkflowRepository(s1), SqlAlchemyWorkflowRepository(s2)
        wf1 = r1.get(workflow_id, tenant_id=TENANT_A)
        wf2 = r2.get(workflow_id, tenant_id=TENANT_A)  # both loaded at the same version

        wf1.start_task("ocr")
        r1.save(wf1)
        s1.commit()  # version bumps

        wf2.start_task("ocr")
        with pytest.raises(ConcurrencyError):
            r2.save(wf2)  # UPDATE ... WHERE version=<old> → 0 rows
    finally:
        s1.close()
        s2.rollback()
        s2.close()


def test_fan_in_conflict_resolves_on_reload_and_dispatches_the_join_step_exactly_once(app_sessionmaker):
    workflow_id = _persist_fan_in_workflow(app_sessionmaker)
    s1, s2 = app_sessionmaker(), app_sessionmaker()
    try:
        scope_session_to_tenant(s1, TENANT_A)
        scope_session_to_tenant(s2, TENANT_A)
        r1, r2 = SqlAlchemyWorkflowRepository(s1), SqlAlchemyWorkflowRepository(s2)
        wf1 = r1.get(workflow_id, tenant_id=TENANT_A)
        wf2 = r2.get(workflow_id, tenant_id=TENANT_A)

        # s1 finishes metadata first — chunking still running, so nothing new is ready.
        ready_after_metadata = wf1.on_task_succeeded("metadata", "blob-meta")
        r1.save(wf1)
        s1.commit()
        assert "external_call" not in ready_after_metadata

        # s2 finishes chunking on the now-stale version → conflict.
        wf2.on_task_succeeded("chunking", "blob-chunk")
        with pytest.raises(ConcurrencyError):
            r2.save(wf2)
        s2.rollback()

        # Retry (what run_with_retry does): reload fresh state — metadata is now
        # done — reapply, and only this committer, seeing BOTH siblings done,
        # unblocks the join step. Exactly one dispatch of external_call.
        wf2_fresh = r2.get(workflow_id, tenant_id=TENANT_A)
        ready_after_chunking = wf2_fresh.on_task_succeeded("chunking", "blob-chunk")
        r2.save(wf2_fresh)
        s2.commit()
        assert ready_after_chunking == frozenset({"external_call"})
    finally:
        s1.close()
        s2.close()
