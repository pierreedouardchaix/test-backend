import uuid
from typing import Protocol

from src.domain.models.document import Document


class DocumentRepository(Protocol):
    def get(self, document_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Document | None: ...

    def save(self, document: Document) -> None: ...

    def list_by_tenant(self, tenant_id: uuid.UUID) -> list[Document]: ...
