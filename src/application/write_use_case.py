from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from src.application.unit_of_work import UnitOfWork

TCommand = TypeVar("TCommand")
TResult = TypeVar("TResult")


class _DbClosed:
    """Sentinel that replaces self._uow after commit to prevent any DB access
    in _post_execution (the session is already closed at that point)."""

    def __getattr__(self, name: str):
        raise RuntimeError(
            f"DB access via self._uow is not allowed in _post_execution "
            f"(attempted: .{name})"
        )


class WriteUseCase(ABC, Generic[TCommand, TResult]):
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, command: TCommand) -> TResult:
        with self._uow:
            result = self._execute(command)
            self._uow.commit()
        self._uow = _DbClosed()
        self._post_execution(command, result)
        return result

    @abstractmethod
    def _execute(self, command: TCommand) -> TResult: ...

    def _post_execution(self, command: TCommand, result: TResult) -> None:
        pass
