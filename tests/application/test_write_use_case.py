from dataclasses import dataclass

import pytest

from src.application.write_use_case import WriteUseCase
from tests.fakes import FakeUnitOfWork


@dataclass(frozen=True)
class DummyCommand:
    pass


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
