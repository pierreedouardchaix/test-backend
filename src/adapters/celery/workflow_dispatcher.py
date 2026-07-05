import uuid

from sqlalchemy.orm import Session, sessionmaker

from src.adapters.sql.rls import scope_session_to_tenant
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
        # Reading ready_steps() then enqueuing without a lock is safe *only
        # because of the calling context*, not intrinsically: this runs exactly
        # once, right after the workflow is created, before any worker has
        # touched it — so there is no concurrent writer to race and no risk of
        # double-dispatching a root. The general anti-double-dispatch guarantee
        # for the rest of the DAG lives in the optimistic-lock replay inside
        # PipelineStepExecutor, not here.
        session = self._session_factory()
        try:
            scope_session_to_tenant(session, tenant_id)  # RLS
            workflow = SqlAlchemyWorkflowRepository(session).get(workflow_id, tenant_id=tenant_id)
            ready_steps = workflow.ready_steps()
        finally:
            session.close()

        for step_name in ready_steps:
            run_pipeline_step.delay(tenant_id=str(tenant_id), workflow_id=str(workflow_id), step_name=step_name)
