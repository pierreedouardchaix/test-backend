"""Shared builders for the integration suite — persist real Document/Workflow
aggregates through the real repositories, RLS-scoped like the app does."""
import uuid

from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.application.ingest_document import PRIMMO_DEFINITION
from src.domain.models.document import Document
from src.domain.models.workflow import Workflow


def persist_document(session_factory, *, tenant_id, uploaded_by, filename="doc.pdf", blob_key="blob-x") -> uuid.UUID:
    """Insert a Document + its Workflow for a tenant, RLS-scoped to that tenant.
    Returns the (shared) id."""
    doc = Document.create(
        tenant_id=tenant_id, uploaded_by=uploaded_by, filename=filename,
        content_type="application/pdf", size_bytes=10, blob_key=blob_key,
    )
    workflow = Workflow.create(id=doc.id, tenant_id=tenant_id, definition=PRIMMO_DEFINITION)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.scope_to_tenant(tenant_id)
        uow.documents.save(doc)
        uow.workflows.save(workflow)
        uow.commit()
    return doc.id


def load_workflow(session_factory, workflow_id, tenant_id) -> Workflow:
    from src.adapters.sql.workflow_repository import SqlAlchemyWorkflowRepository
    from src.adapters.sql.rls import scope_session_to_tenant

    session = session_factory()
    try:
        scope_session_to_tenant(session, tenant_id)
        return SqlAlchemyWorkflowRepository(session).get(workflow_id, tenant_id=tenant_id)
    finally:
        session.close()
