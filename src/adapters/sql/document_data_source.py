import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.adapters.sql.models import DocumentORM, TaskORM, UserORM, WorkflowORM
from src.ports.document_data_source import DocumentDetailRow, DocumentRow, TaskRow


class SqlAlchemyDocumentDataSource:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_tenant(self, tenant_id: uuid.UUID) -> list[DocumentRow]:
        rows = self._session.execute(
            select(DocumentORM, WorkflowORM, UserORM)
            .join(WorkflowORM, WorkflowORM.id == DocumentORM.id)
            .join(UserORM, UserORM.id == DocumentORM.uploaded_by)
            .where(DocumentORM.tenant_id == tenant_id)
            .order_by(DocumentORM.created_at)
        ).all()
        return [
            DocumentRow(
                document_id=doc.id,
                tenant_id=doc.tenant_id,
                uploaded_by=doc.uploaded_by,
                uploaded_by_first_name=user.first_name,
                uploaded_by_last_name=user.last_name,
                filename=doc.filename,
                content_type=doc.content_type,
                size_bytes=doc.size_bytes,
                created_at=doc.created_at,
                workflow_status=wf.status,
                failed_step=wf.failed_step,
                failure_reason=wf.failure_reason,
            )
            for doc, wf, user in rows
        ]

    def get_by_id(self, document_id: uuid.UUID, *, tenant_id: uuid.UUID) -> DocumentDetailRow | None:
        row = self._session.execute(
            select(DocumentORM, WorkflowORM, UserORM)
            .join(WorkflowORM, WorkflowORM.id == DocumentORM.id)
            .join(UserORM, UserORM.id == DocumentORM.uploaded_by)
            .where(DocumentORM.id == document_id, DocumentORM.tenant_id == tenant_id)
        ).one_or_none()
        if row is None:
            return None
        doc, wf, user = row

        task_rows = self._session.execute(
            select(TaskORM)
            .where(TaskORM.workflow_id == document_id)
            .order_by(TaskORM.created_at)
        ).scalars().all()

        return DocumentDetailRow(
            document_id=doc.id,
            tenant_id=doc.tenant_id,
            uploaded_by=doc.uploaded_by,
            uploaded_by_first_name=user.first_name,
            uploaded_by_last_name=user.last_name,
            filename=doc.filename,
            content_type=doc.content_type,
            size_bytes=doc.size_bytes,
            created_at=doc.created_at,
            workflow_status=wf.status,
            failed_step=wf.failed_step,
            failure_reason=wf.failure_reason,
            step_results=wf.results or {},
            tasks=tuple(
                TaskRow(
                    step_name=t.step_name,
                    status=t.status,
                    attempts=t.attempts,
                    max_attempts=t.max_attempts,
                    last_error=t.errors[-1]["error"] if t.errors else None,
                    started_at=t.started_at,
                    finished_at=t.finished_at,
                )
                for t in task_rows
            ),
        )
