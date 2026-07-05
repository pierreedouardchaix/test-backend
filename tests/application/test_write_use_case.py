import uuid
from dataclasses import dataclass, field

import pytest

from src.application.unit_of_work import CROSS_TENANT
from src.application.write_use_case import CrossTenantWriteUseCase, WriteUseCase
from tests.fakes import FakeUnitOfWork


@dataclass(frozen=True)
class DummyCommand:
    # WriteUseCase is single-tenant: it scopes the UoW to command.tenant_id for RLS.
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True)
class DummyResult:
    pass


class _OkUseCase(WriteUseCase[DummyCommand, DummyResult]):
    def _execute(self, command):
        return DummyResult()


class _BadUseCase(WriteUseCase[DummyCommand, DummyResult]):
    def _execute(self, command):
        return DummyResult()

    def _post_execution(self, command, result):
        _ = self._uow.documents  # forbidden


def test_commit_is_called():
    uow = FakeUnitOfWork()
    _OkUseCase(uow).execute(DummyCommand())
    assert uow.committed is True


def test_db_access_in_post_execution_raises():
    uow = FakeUnitOfWork()
    with pytest.raises(RuntimeError, match="not allowed in _post_execution"):
        _BadUseCase(uow).execute(DummyCommand())


def test_single_tenant_write_scopes_the_uow_to_the_command_tenant():
    """RLS is applied by the base, automatically — a subclass can't forget it."""
    uow = FakeUnitOfWork()
    tenant = uuid.uuid4()
    _OkUseCase(uow).execute(DummyCommand(tenant_id=tenant))
    assert uow.scoped_tenant == tenant


class _CrossTenantUseCase(CrossTenantWriteUseCase[DummyCommand, DummyResult]):
    def _execute(self, command):
        return DummyResult()


def test_cross_tenant_write_scopes_the_uow_to_the_bypass():
    uow = FakeUnitOfWork()
    _CrossTenantUseCase(uow).execute(DummyCommand())
    assert uow.scoped_tenant == CROSS_TENANT


@dataclass(frozen=True)
class _NoTenantCommand:
    pass


class _MisusedUseCase(WriteUseCase[_NoTenantCommand, DummyResult]):
    def _execute(self, command):
        return DummyResult()


def test_single_tenant_write_without_tenant_id_fails_fast_with_a_clear_error():
    """Runtime belt for when no type checker runs: a WriteUseCase whose command
    lacks tenant_id raises a clear TypeError, not a buried AttributeError."""
    with pytest.raises(TypeError, match="tenant_id"):
        _MisusedUseCase(FakeUnitOfWork()).execute(_NoTenantCommand())
