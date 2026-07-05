import uuid
from dataclasses import dataclass

from src.domain.errors import DomainError
from src.ports.document_data_source import DocumentDataSource, DocumentDetailRow


@dataclass(frozen=True)
class GetDocumentQuery:
    document_id: uuid.UUID
    tenant_id: uuid.UUID


class DocumentNotFound(DomainError):
    pass


class GetDocumentUseCase:
    def __init__(self, data_source: DocumentDataSource) -> None:
        self._data_source = data_source

    def execute(self, query: GetDocumentQuery) -> DocumentDetailRow:
        row = self._data_source.get_by_id(query.document_id, tenant_id=query.tenant_id)
        if row is None:
            raise DocumentNotFound(query.document_id)
        return row
