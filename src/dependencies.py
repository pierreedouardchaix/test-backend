"""FastAPI dependency factories for the application.

- get_settings: cached Settings loaded once at startup.
- get_session: per-request SQLAlchemy session (for read-only use cases).
- get_uow: per-request UnitOfWork (for write use cases).
- get_blob_store: singleton BlobStore, on a filesystem directory shared by
  the API and Celery workers (a volume in docker-compose).
"""
from collections.abc import Generator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from src.adapters.filesystem.blob_store import FileSystemBlobStore
from src.adapters.in_memory.event_publisher import InMemoryEventPublisher
from src.adapters.in_memory.workflow_dispatcher import NoOpWorkflowDispatcher
from src.adapters.sql.document_data_source import SqlAlchemyDocumentDataSource
from src.adapters.sql.engine import create_db_engine, create_session_factory
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.ports.blob_store import BlobStore
from src.ports.document_data_source import DocumentDataSource
from src.ports.event_publisher import EventPublisher
from src.ports.workflow_dispatcher import WorkflowDispatcher
from src.settings import Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def _get_session_factory():
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    return create_session_factory(engine)


def get_session() -> Generator[Session, None, None]:
    """Short-lived read-only session — no transaction boundary needed."""
    session = _get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def get_uow() -> SqlAlchemyUnitOfWork:
    """Provides an un-entered UnitOfWork. WriteUseCase.execute() owns the lifecycle."""
    return SqlAlchemyUnitOfWork(_get_session_factory())


@lru_cache(maxsize=1)
def get_blob_store() -> BlobStore:
    return FileSystemBlobStore(get_settings().blob_storage_dir)


@lru_cache(maxsize=1)
def get_workflow_dispatcher() -> WorkflowDispatcher:
    return NoOpWorkflowDispatcher()


@lru_cache(maxsize=1)
def get_event_publisher() -> EventPublisher:
    return InMemoryEventPublisher()


def get_document_data_source(session: Session = Depends(get_session)) -> DocumentDataSource:
    return SqlAlchemyDocumentDataSource(session)
