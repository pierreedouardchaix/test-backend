import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class DocumentRow:
    document_id: uuid.UUID
    tenant_id: uuid.UUID
    uploaded_by: uuid.UUID
    uploaded_by_first_name: str
    uploaded_by_last_name: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    # Workflow fields
    workflow_status: str
    failed_step: str | None
    failure_reason: str | None


@dataclass(frozen=True)
class TaskRow:
    step_name: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class DocumentDetailRow:
    document_id: uuid.UUID
    tenant_id: uuid.UUID
    uploaded_by: uuid.UUID
    uploaded_by_first_name: str
    uploaded_by_last_name: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    workflow_status: str
    failed_step: str | None
    failure_reason: str | None
    step_results: dict[str, str]  # step_name → blob_key
    tasks: tuple[TaskRow, ...]


class DocumentDataSource(Protocol):
    def list_by_tenant(self, tenant_id: uuid.UUID, *, limit: int, offset: int) -> list[DocumentRow]: ...
    def get_by_id(self, document_id: uuid.UUID, *, tenant_id: uuid.UUID) -> DocumentDetailRow | None: ...
