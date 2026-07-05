"""Process-wide singletons shared by every entrypoint (uvicorn API, Celery
worker) — same image, same env vars, same settings/session-factory/blob-store
wiring, different command. Kept out of dependencies.py (FastAPI-specific
`Depends()` wrappers) so the Celery worker can reuse this bootstrap without
importing FastAPI at all.
"""
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

import uuid

from src.adapters.filesystem.blob_store import FileSystemBlobStore
from src.adapters.redis.event_publisher import RedisEventPublisher
from src.adapters.redis.event_stream import RedisEventStream
from src.adapters.sql.document_data_source import SqlAlchemyDocumentDataSource
from src.adapters.sql.engine import create_db_engine, create_session_factory
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.adapters.sql.workflow_repository import SqlAlchemyWorkflowRepository
from src.ports.blob_store import BlobStore
from src.ports.document_data_source import DocumentDetailRow
from src.ports.event_publisher import EventPublisher
from src.ports.event_stream import EventStream
from src.settings import Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    return create_session_factory(engine)


def new_unit_of_work() -> SqlAlchemyUnitOfWork:
    """A fresh, un-entered UnitOfWork bound to the shared session factory —
    each call gets its own session/transaction. WriteUseCase.execute() and
    PipelineStepExecutor's run_with_retry both rely on getting a brand new one
    per attempt."""
    return SqlAlchemyUnitOfWork(get_session_factory())


@lru_cache(maxsize=1)
def get_blob_store() -> BlobStore:
    return FileSystemBlobStore(get_settings().blob_storage_dir)


@lru_cache(maxsize=1)
def get_event_publisher() -> EventPublisher:
    """Real-time event bus, shared by the API (webhook completion events) and
    the Celery workers (per-step events) — both PUBLISH to the same Redis so
    the SSE endpoint sees every transition regardless of which process caused
    it."""
    return RedisEventPublisher.from_url(get_settings().redis_url)


@lru_cache(maxsize=1)
def get_event_stream() -> EventStream:
    """Read side of the bus — the SSE endpoint subscribes through this. API
    process only (the workers publish, they never consume)."""
    return RedisEventStream(get_settings().redis_url)


def read_document_detail(document_id: uuid.UUID, tenant_id: uuid.UUID) -> DocumentDetailRow | None:
    """Read a document's current detail through a short-lived session (open →
    read → close). Deliberately NOT the request-scoped `get_session`: an SSE
    response is long-lived, and a request-scoped session stays open until the
    response finishes — which for SSE means the whole streamed connection,
    pinning one DB connection per connected client. This opens and closes a
    session per read so the DB connection is released before the long stream."""
    session = get_session_factory()()
    try:
        return SqlAlchemyDocumentDataSource(session).get_by_id(document_id, tenant_id=tenant_id)
    finally:
        session.close()


def partner_job_exists(partner_job_id: str) -> bool:
    """Whether a task with this partner job id is on record — the webhook's
    synchronous 404 gate. Short-session read (open → read → close): the webhook
    only needs existence, not to hold a session while it enqueues."""
    session = get_session_factory()()
    try:
        return SqlAlchemyWorkflowRepository(session).get_by_partner_job_id(partner_job_id) is not None
    finally:
        session.close()
