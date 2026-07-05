"""FastAPI dependency factories for the application.

- get_settings: cached Settings loaded once at startup.
- get_session: per-request SQLAlchemy session (for read-only use cases).
- get_uow: per-request UnitOfWork (for write use cases).
"""
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy.orm import Session

from src.adapters.sql.engine import create_db_engine, create_session_factory
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
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


def get_uow() -> Generator[SqlAlchemyUnitOfWork, None, None]:
    """Unit of work for write use cases. Caller must call uow.commit()."""
    uow = SqlAlchemyUnitOfWork(_get_session_factory())
    with uow as u:
        yield u
