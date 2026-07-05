import uuid

from sqlalchemy.orm import Session, sessionmaker

from src.adapters.sql.workflow_repository import SqlAlchemyWorkflowRepository
from src.celery_app import run_pipeline_step


class CeleryWorkflowDispatcher:
    """Enqueues one Celery task per root step of a freshly created workflow.

    Called right after IngestDocumentUseCase commits (session already closed,
    see _DbClosed), so it cannot reuse that UnitOfWork — it opens its own
    short-lived read session instead, the same pattern read-only use cases use
    (a plain session, no transaction boundary needed for a read).
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def dispatch(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> None:
        session = self._session_factory()
        try:
            workflow = SqlAlchemyWorkflowRepository(session).get(workflow_id, tenant_id=tenant_id)
            ready_steps = workflow.ready_steps()
        finally:
            session.close()

        for step_name in ready_steps:
            run_pipeline_step.delay(tenant_id=str(tenant_id), workflow_id=str(workflow_id), step_name=step_name)
