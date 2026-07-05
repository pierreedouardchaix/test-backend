import uuid
from dataclasses import dataclass
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


class DocumentDataSource(Protocol):
    def list_by_tenant(self, tenant_id: uuid.UUID) -> list[DocumentRow]: ...
