import uuid
from datetime import datetime

from typing import Self

from pydantic import BaseModel, ConfigDict

from src.ports.document_data_source import DocumentDetailRow, DocumentRow, TaskRow


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
    step_results: dict[str, str]  # step_name → blob_key (or resolved value once blob store wired)

    @classmethod
    def from_row(cls, row: DocumentDetailRow) -> Self:
        return cls(step_results=row.step_results)


class DocumentSummaryResponse(BaseModel):
    document_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    uploaded_by: uuid.UUID
    uploaded_by_name: str
    workflow_status: str
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
            workflow_status=row.workflow_status,
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
            workflow=WorkflowResponse(
                status=row.workflow_status,
                failed_step=row.failed_step,
                failure_reason=row.failure_reason,
                tasks=[TaskResponse.model_validate(t, from_attributes=True) for t in row.tasks],
            ),
        )
