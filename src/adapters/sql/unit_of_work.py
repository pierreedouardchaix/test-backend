import uuid
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from src.adapters.sql.document_repository import SqlAlchemyDocumentRepository
from src.adapters.sql.rls import scope_session_to_tenant
from src.adapters.sql.tenant_repository import SqlAlchemyTenantRepository
from src.adapters.sql.workflow_repository import SqlAlchemyWorkflowRepository


class SqlAlchemyUnitOfWork:
    """One SQLAlchemy session/transaction, exposing the repositories that share
    it. Use as a context manager; call commit() explicitly, otherwise __exit__
    rolls back.

    Not wired to any use case yet (by design — every user-facing mutation will
    go through a use case, which don't exist yet). Built now so the persistence
    layer is ready and integration-testable.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __enter__(self) -> Self:
        self._session = self._session_factory()
        self.tenants = SqlAlchemyTenantRepository(self._session)
        self.documents = SqlAlchemyDocumentRepository(self._session)
        self.workflows = SqlAlchemyWorkflowRepository(self._session)
        return self

    def scope_to_tenant(self, tenant: uuid.UUID | str) -> None:
        """Bind this UoW's writes/reads to a tenant for RLS (or TENANT_BYPASS
        for the cross-tenant callback path). Call at the start of the use case,
        before any query."""
        scope_session_to_tenant(self._session, tenant)

    def __exit__(self, exc_type, exc, tb) -> None:
        # rollback() is a no-op if commit() already ran; guards against leaving
        # an uncommitted transaction open on an early return or exception.
        self.rollback()
        self._session.close()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
