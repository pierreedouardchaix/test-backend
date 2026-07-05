import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.adapters.sql.models import DocumentORM, UserORM, WorkflowORM
from src.ports.document_data_source import DocumentRow


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
