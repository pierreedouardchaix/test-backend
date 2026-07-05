"""Process-wide singletons shared by every entrypoint (uvicorn API, Celery
worker) — same image, same env vars, same settings/session-factory/blob-store
wiring, different command. Kept out of dependencies.py (FastAPI-specific
`Depends()` wrappers) so the Celery worker can reuse this bootstrap without
importing FastAPI at all.
"""
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from src.adapters.filesystem.blob_store import FileSystemBlobStore
from src.adapters.sql.engine import create_db_engine, create_session_factory
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.ports.blob_store import BlobStore
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
