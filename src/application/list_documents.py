import uuid
from dataclasses import dataclass

from src.ports.document_data_source import DocumentDataSource, DocumentRow


@dataclass(frozen=True)
class ListDocumentsQuery:
    tenant_id: uuid.UUID
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True)
class ListDocumentsResult:
    documents: list[DocumentRow]


class ListDocumentsUseCase:
    def __init__(self, data_source: DocumentDataSource) -> None:
        self._data_source = data_source

    def execute(self, query: ListDocumentsQuery) -> ListDocumentsResult:
        return ListDocumentsResult(
            documents=self._data_source.list_by_tenant(
                query.tenant_id, limit=query.limit, offset=query.offset
            )
        )
