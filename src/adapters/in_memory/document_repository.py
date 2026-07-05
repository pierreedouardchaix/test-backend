import uuid

from src.domain.models.document import Document


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._documents: dict[uuid.UUID, Document] = {}

    def get(self, document_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Document | None:
        document = self._documents.get(document_id)
        if document is None or document.tenant_id != tenant_id:
            return None
        return document

    def save(self, document: Document) -> None:
        self._documents[document.id] = document

    def list_by_tenant(self, tenant_id: uuid.UUID) -> list[Document]:
        return [document for document in self._documents.values() if document.tenant_id == tenant_id]
