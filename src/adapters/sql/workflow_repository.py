import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.adapters.sql.mappers import definition_to_dict, task_to_orm, workflow_from_orm
from src.adapters.sql.models import TaskORM, WorkflowORM
from src.application.concurrency import ConcurrencyError
from src.domain.models.workflow import Workflow


class SqlAlchemyWorkflowRepository:
    """Persists the Workflow aggregate (workflow row + its task rows) with
    optimistic concurrency on the workflow row.

    The `version` column is a persistence concern, so it is not carried on the
    domain Workflow: the repository remembers the version it loaded per id, and
    a save issues `UPDATE ... WHERE id=? AND version=?`. A 0-row update means a
    concurrent writer won → ConcurrencyError, and the caller (run_with_retry)
    reloads and retries. That reload is what resolves the fan-in.

    The WorkflowDefinition is stored as data on the row (JSONB), so there is no
    definitions registry to inject — the aggregate is reconstituted whole.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._loaded_versions: dict[uuid.UUID, int] = {}

    def get(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Workflow | None:
        row = self._session.get(WorkflowORM, workflow_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return self._reconstitute(row)

    def get_by_id(self, workflow_id: uuid.UUID) -> Workflow | None:
        row = self._session.get(WorkflowORM, workflow_id)
        if row is None:
            return None
        return self._reconstitute(row)

    def get_by_partner_job_id(self, partner_job_id: str) -> Workflow | None:
        workflow_id = self._session.execute(
            select(TaskORM.workflow_id).where(TaskORM.partner_job_id == partner_job_id)
        ).scalar_one_or_none()
        if workflow_id is None:
            return None
        return self.get_by_id(workflow_id)

    def _reconstitute(self, row: WorkflowORM) -> Workflow:
        task_rows = list(
            self._session.execute(select(TaskORM).where(TaskORM.workflow_id == row.id)).scalars()
        )
        self._loaded_versions[row.id] = row.version
        return workflow_from_orm(row, task_rows)

    def save(self, workflow: Workflow) -> int:
        loaded_version = self._loaded_versions.get(workflow.id)
        if loaded_version is None:
            version = self._insert(workflow)
        else:
            version = self._update(workflow, loaded_version)
        for task in workflow.tasks.values():
            self._session.merge(task_to_orm(task, workflow.tenant_id))
        return version

    def _insert(self, workflow: Workflow) -> int:
        self._session.add(
            WorkflowORM(
                id=workflow.id,
                tenant_id=workflow.tenant_id,
                definition=definition_to_dict(workflow.definition),
                status=workflow.status.value,
                results=dict(workflow.results),
                failed_step=workflow.failed_step,
                failure_reason=workflow.failure_reason,
                version=1,
                created_at=workflow.created_at,
            )
        )
        self._session.flush()  # emit the INSERT before the task rows FK-reference it
        self._loaded_versions[workflow.id] = 1
        return 1

    def _update(self, workflow: Workflow, loaded_version: int) -> int:
        new_version = loaded_version + 1
        result = self._session.execute(
            update(WorkflowORM)
            .where(WorkflowORM.id == workflow.id, WorkflowORM.version == loaded_version)
            .values(
                status=workflow.status.value,
                results=dict(workflow.results),
                failed_step=workflow.failed_step,
                failure_reason=workflow.failure_reason,
                version=new_version,
            )
        )
        if result.rowcount == 0:
            raise ConcurrencyError(
                f"workflow {workflow.id} was modified concurrently (expected version {loaded_version})"
            )
        self._loaded_versions[workflow.id] = new_version
        return new_version
