"""FastAPI dependency factories for the application.

- get_settings / get_blob_store: process-wide singletons, shared with the
  Celery worker via src.bootstrap.
- get_session: per-request SQLAlchemy session (for read-only use cases).
- get_uow: per-request UnitOfWork (for write use cases).
"""
from collections.abc import Generator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from src.adapters.celery.workflow_dispatcher import CeleryWorkflowDispatcher
from src.adapters.in_memory.event_publisher import InMemoryEventPublisher
from src.adapters.sql.document_data_source import SqlAlchemyDocumentDataSource
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.bootstrap import get_blob_store, get_session_factory, get_settings, new_unit_of_work
from src.ports.document_data_source import DocumentDataSource
from src.ports.event_publisher import EventPublisher
from src.ports.workflow_dispatcher import WorkflowDispatcher

__all__ = ["get_blob_store", "get_settings"]


def get_session() -> Generator[Session, None, None]:
    """Short-lived read-only session — no transaction boundary needed."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def get_uow() -> SqlAlchemyUnitOfWork:
    """Provides an un-entered UnitOfWork. WriteUseCase.execute() owns the lifecycle."""
    return new_unit_of_work()


@lru_cache(maxsize=1)
def get_workflow_dispatcher() -> WorkflowDispatcher:
    return CeleryWorkflowDispatcher(get_session_factory())


@lru_cache(maxsize=1)
def get_event_publisher() -> EventPublisher:
    return InMemoryEventPublisher()


def get_document_data_source(session: Session = Depends(get_session)) -> DocumentDataSource:
    return SqlAlchemyDocumentDataSource(session)
