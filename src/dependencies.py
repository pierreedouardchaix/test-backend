"""FastAPI dependency factories for the application.

- get_settings / get_blob_store: process-wide singletons, shared with the
  Celery worker via src.bootstrap.
- get_session: per-request SQLAlchemy session (for read-only use cases).
- get_uow: per-request UnitOfWork (for write use cases).
"""
import uuid
from collections.abc import Callable, Generator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from src.adapters.celery.partner_callback_dispatcher import CeleryPartnerCallbackDispatcher
from src.adapters.celery.workflow_dispatcher import CeleryWorkflowDispatcher
from src.adapters.sql.document_data_source import SqlAlchemyDocumentDataSource
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.bootstrap import (
    get_blob_store,
    get_event_publisher,
    get_event_stream,
    get_session_factory,
    get_settings,
    new_unit_of_work,
    partner_job_exists,
    read_document_detail,
)
from src.ports.document_data_source import DocumentDataSource, DocumentDetailRow
from src.ports.partner_callback_dispatcher import PartnerCallbackDispatcher
from src.ports.workflow_dispatcher import WorkflowDispatcher

__all__ = ["get_blob_store", "get_event_publisher", "get_event_stream", "get_settings"]

DocumentReader = Callable[[uuid.UUID, uuid.UUID], DocumentDetailRow | None]
PartnerJobResolver = Callable[[str], bool]


def get_document_reader() -> DocumentReader:
    """Short-session document reader for the SSE endpoint's ownership check —
    see bootstrap.read_document_detail for why it isn't the request-scoped
    session (a request session would stay pinned for the whole SSE stream)."""
    return read_document_detail


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
def get_partner_callback_dispatcher() -> PartnerCallbackDispatcher:
    return CeleryPartnerCallbackDispatcher()


def get_partner_job_resolver() -> PartnerJobResolver:
    """Short-session existence check for the webhook's 404 gate — see
    bootstrap.partner_job_exists."""
    return partner_job_exists


def get_document_data_source(session: Session = Depends(get_session)) -> DocumentDataSource:
    return SqlAlchemyDocumentDataSource(session)
