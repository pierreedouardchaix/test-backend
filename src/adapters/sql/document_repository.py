import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.adapters.sql.mappers import document_from_orm, document_to_orm
from src.adapters.sql.models import DocumentORM
from src.domain.models.document import Document


class SqlAlchemyDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, document_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Document | None:
        row = self._session.get(DocumentORM, document_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return document_from_orm(row)

    def save(self, document: Document) -> None:
        self._session.merge(document_to_orm(document))

    def list_by_tenant(self, tenant_id: uuid.UUID) -> list[Document]:
        rows = self._session.execute(
            select(DocumentORM).where(DocumentORM.tenant_id == tenant_id).order_by(DocumentORM.created_at)
        ).scalars()
        return [document_from_orm(r) for r in rows]
