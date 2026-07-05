import uuid
from datetime import datetime

from typing import Any, Self

from pydantic import BaseModel, ConfigDict

from src.ports.document_data_source import DocumentDetailRow, DocumentRow, TaskRow

# The README speaks of a document being `ready` once processing completes. That
# is a document-facing vocabulary; internally the aggregate is a Workflow with a
# running/succeeded/failed lifecycle. Map at the API boundary so clients see the
# README's terms (per-task statuses stay task-level and are not remapped).
_DOCUMENT_STATUS = {"running": "processing", "succeeded": "ready", "failed": "failed"}
DOCUMENT_TERMINAL_STATUSES = ("ready", "failed")


def document_status(workflow_status: str) -> str:
    return _DOCUMENT_STATUS.get(workflow_status, workflow_status)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_name: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class WorkflowResponse(BaseModel):
    status: str
    failed_step: str | None
    failure_reason: str | None
    tasks: list[TaskResponse]


class DocumentResultsResponse(BaseModel):
    # The actual extracted data per step (OCR text, metadata, chunks…), resolved
    # from the blob store — not the blob keys, which are an internal storage
    # detail. Only returned once the document is ready.
    results: dict[str, Any]  # step_name → resolved value


class DocumentSummaryResponse(BaseModel):
    document_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    uploaded_by: uuid.UUID
    uploaded_by_name: str
    status: str  # document-facing: processing / ready / failed
    failed_step: str | None
    failure_reason: str | None

    @classmethod
    def from_row(cls, row: DocumentRow) -> Self:
        return cls(
            document_id=row.document_id,
            filename=row.filename,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
            uploaded_by=row.uploaded_by,
            uploaded_by_name=f"{row.uploaded_by_first_name} {row.uploaded_by_last_name}",
            status=document_status(row.workflow_status),
            failed_step=row.failed_step,
            failure_reason=row.failure_reason,
        )


class DocumentDetailResponse(BaseModel):
    document_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    uploaded_by: uuid.UUID
    uploaded_by_name: str
    partner_job_id: str | None  # copy this into a /webhooks/partner payload to test the callback
    workflow: WorkflowResponse

    @classmethod
    def from_row(cls, row: DocumentDetailRow) -> Self:
        return cls(
            document_id=row.document_id,
            filename=row.filename,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
            uploaded_by=row.uploaded_by,
            uploaded_by_name=f"{row.uploaded_by_first_name} {row.uploaded_by_last_name}",
            partner_job_id=row.partner_job_id,
            workflow=WorkflowResponse(
                status=document_status(row.workflow_status),  # processing / ready / failed
                failed_step=row.failed_step,
                failure_reason=row.failure_reason,
                tasks=[TaskResponse.model_validate(t, from_attributes=True) for t in row.tasks],
            ),
        )
