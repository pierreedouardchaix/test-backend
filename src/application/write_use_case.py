import uuid
from abc import ABC, abstractmethod
from typing import Generic, Protocol, TypeVar

from src.application.unit_of_work import CROSS_TENANT, UnitOfWork

TResult = TypeVar("TResult")


class TenantScopedCommand(Protocol):
    """A command that names the tenant it acts on — the contract a single-tenant
    WriteUseCase relies on to scope RLS automatically."""

    tenant_id: uuid.UUID


TCommand = TypeVar("TCommand")
TScopedCommand = TypeVar("TScopedCommand", bound=TenantScopedCommand)


class _DbClosed:
    """Sentinel that replaces self._uow after commit to prevent any DB access
    in _post_execution (the session is already closed at that point)."""

    def __getattr__(self, name: str):
        raise RuntimeError(
            f"DB access via self._uow is not allowed in _post_execution "
            f"(attempted: .{name})"
        )


class _WriteUseCaseBase(ABC, Generic[TCommand, TResult]):
    """Shared write lifecycle: open the UoW, scope it for RLS, run, commit, then
    (post-commit, DB closed) run _post_execution. Scoping is done here, in the
    base, precisely so no concrete use case can forget it — that is what keeps
    RLS *automatically* defensive rather than a per-use-case discipline."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, command: TCommand) -> TResult:
        with self._uow:
            self._scope(command)
            result = self._execute(command)
            self._uow.commit()
        self._uow = _DbClosed()
        self._post_execution(command, result)
        return result

    @abstractmethod
    def _scope(self, command: TCommand) -> None:
        """Bind the UoW to a tenant for RLS before _execute runs."""

    @abstractmethod
    def _execute(self, command: TCommand) -> TResult: ...

    def _post_execution(self, command: TCommand, result: TResult) -> None:
        pass


class WriteUseCase(_WriteUseCaseBase[TScopedCommand, TResult]):
    """A **single-tenant** write: the command names its tenant, and the UoW is
    RLS-scoped to it automatically. This is the common case — subclasses only
    implement _execute and never touch scoping."""

    def _scope(self, command: TScopedCommand) -> None:
        self._uow.scope_to_tenant(command.tenant_id)


class CrossTenantWriteUseCase(_WriteUseCaseBase[TCommand, TResult]):
    """A write whose target tenant is not known from the command — it's resolved
    from the DB (e.g. the partner webhook correlates by an external job id with
    no tenant context). The UoW is scoped to the cross-tenant bypass; isolation
    is then enforced at the application level by acting only on the resolved
    row's own tenant."""

    def _scope(self, command: TCommand) -> None:
        self._uow.scope_to_tenant(CROSS_TENANT)
