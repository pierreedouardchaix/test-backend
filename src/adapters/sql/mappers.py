"""Translation between the pure domain dataclasses and the SQLAlchemy ORM
rows. Keeping it in one place means the repositories stay thin and the domain
never learns about persistence.

`version` (optimistic lock) and `tenant_id` on child rows are persistence-only
concerns — they live on the ORM side and are handled by the repositories, not
carried on the domain objects.
"""

import uuid
from datetime import datetime

from src.adapters.sql.models import DocumentORM, TaskORM, TenantORM, UserORM, WorkflowORM
from src.domain.models.document import Document
from src.domain.models.task import Task, TaskAttemptError, TaskStatus
from src.domain.models.tenant import Tenant
from src.domain.models.user import User
from src.domain.models.workflow import Workflow, WorkflowStatus
from src.domain.models.workflow_definition import StepDefinition, WorkflowDefinition


# --- User / Tenant ----------------------------------------------------------

def user_to_orm(user: User, tenant_id: uuid.UUID) -> UserORM:
    return UserORM(
        id=user.id,
        tenant_id=tenant_id,
        first_name=user.first_name,
        last_name=user.last_name,
        created_at=user.created_at,
    )


def user_from_orm(row: UserORM) -> User:
    return User(
        id=row.id,
        first_name=row.first_name,
        last_name=row.last_name,
        created_at=row.created_at,
    )


def tenant_to_orm(tenant: Tenant) -> TenantORM:
    return TenantORM(id=tenant.id, name=tenant.name, created_at=tenant.created_at)


def tenant_from_orm(row: TenantORM, user_rows: list[UserORM]) -> Tenant:
    return Tenant(
        id=row.id,
        name=row.name,
        user=[user_from_orm(u) for u in user_rows],
        created_at=row.created_at,
    )


# --- Document ---------------------------------------------------------------

def document_to_orm(document: Document) -> DocumentORM:
    return DocumentORM(
        id=document.id,
        tenant_id=document.tenant_id,
        uploaded_by=document.uploaded_by,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        blob_key=document.blob_key,
        created_at=document.created_at,
    )


def document_from_orm(row: DocumentORM) -> Document:
    return Document(
        id=row.id,
        tenant_id=row.tenant_id,
        uploaded_by=row.uploaded_by,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        blob_key=row.blob_key,
        created_at=row.created_at,
    )


# --- Task -------------------------------------------------------------------

def task_to_orm(task: Task, tenant_id: uuid.UUID) -> TaskORM:
    return TaskORM(
        id=task.id,
        workflow_id=task.workflow_id,
        tenant_id=tenant_id,
        step_name=task.step_name,
        status=task.status.value,
        attempts=task.attempts,
        max_attempts=task.max_attempts,
        errors=[
            {"attempt": e.attempt, "error": e.error, "occurred_at": e.occurred_at.isoformat()}
            for e in task.errors
        ],
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
    )


def task_from_orm(row: TaskORM) -> Task:
    return Task(
        id=row.id,
        workflow_id=row.workflow_id,
        step_name=row.step_name,
        max_attempts=row.max_attempts,
        status=TaskStatus(row.status),
        attempts=row.attempts,
        errors=[
            TaskAttemptError(
                attempt=e["attempt"],
                error=e["error"],
                occurred_at=datetime.fromisoformat(e["occurred_at"]),
            )
            for e in row.errors
        ],
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )


# --- Workflow ---------------------------------------------------------------

def definition_to_dict(definition: WorkflowDefinition) -> dict:
    return {
        "name": definition.name,
        "steps": [
            {"name": s.name, "depends_on": sorted(s.depends_on), "max_attempts": s.max_attempts}
            for s in definition.steps
        ],
    }


def definition_from_dict(data: dict) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=data["name"],
        steps=tuple(
            StepDefinition(
                name=s["name"],
                depends_on=frozenset(s["depends_on"]),
                max_attempts=s["max_attempts"],
            )
            for s in data["steps"]
        ),
    )


def workflow_from_orm(row: WorkflowORM, task_rows: list[TaskORM]) -> Workflow:
    return Workflow(
        id=row.id,
        tenant_id=row.tenant_id,
        definition=definition_from_dict(row.definition),
        status=WorkflowStatus(row.status),
        results=dict(row.results),
        tasks={r.step_name: task_from_orm(r) for r in task_rows},
        failed_step=row.failed_step,
        failure_reason=row.failure_reason,
        created_at=row.created_at,
    )
