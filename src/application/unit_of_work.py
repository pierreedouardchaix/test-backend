import uuid
from typing import Protocol, Self

from src.ports.document_repository import DocumentRepository
from src.ports.tenant_repository import TenantRepository
from src.ports.workflow_repository import WorkflowRepository

# scope_to_tenant marker for tenant-less ingress (partner webhook resolves a
# workflow across tenants). Lives here, on the port, not in the SQL adapter, so
# use cases can express "cross-tenant" without importing persistence.
CROSS_TENANT = "*"


class UnitOfWork(Protocol):
    """A transaction boundary that exposes the repositories sharing it.

    Used as a context manager: on exit without an explicit commit(), the
    transaction is rolled back. Retry-on-conflict lives outside (see
    run_with_retry) so this stays a plain boundary.
    """

    tenants: TenantRepository
    documents: DocumentRepository
    workflows: WorkflowRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc, tb) -> None: ...

    def scope_to_tenant(self, tenant: uuid.UUID | str) -> None:
        """Bind this UoW to a tenant for row-level security (or a bypass
        sentinel for cross-tenant ingress). Call before any query."""
        ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
