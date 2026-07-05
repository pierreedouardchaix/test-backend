from typing import Self

from src.adapters.in_memory.document_repository import InMemoryDocumentRepository
from src.adapters.in_memory.tenant_repository import InMemoryTenantRepository
from src.adapters.in_memory.workflow_repository import InMemoryWorkflowRepository


class InMemoryUnitOfWork:
    """No-op transaction boundary wrapping the real in-memory repositories —
    same role as SqlAlchemyUnitOfWork but backed by process memory instead of
    Postgres. Useful wherever a UnitOfWork is required without a database:
    integration tests exercising the real (non-test-double) adapters, or a
    local dev mode without Postgres.

    Repositories are built once at construction and shared across every
    __enter__ — there is no real transaction to roll back, so all reads see
    all prior writes regardless of commit()."""

    def __init__(self) -> None:
        self.tenants = InMemoryTenantRepository()
        self.documents = InMemoryDocumentRepository()
        self.workflows = InMemoryWorkflowRepository()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass
