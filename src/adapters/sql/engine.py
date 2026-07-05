import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.adapters.sql import rls  # noqa: F401 — registers the RLS after_begin listener on import

_LOCAL_DEFAULT = "postgresql+psycopg://primmo:primmo@localhost:5432/primmo"


def get_database_url(explicit: str | None = None) -> str:
    return explicit or os.environ.get("DATABASE_URL", _LOCAL_DEFAULT)


def create_db_engine(url: str | None = None) -> Engine:
    return create_engine(get_database_url(url), future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: domain objects are already detached copies (the
    # repositories translate ORM <-> domain), so there is nothing to refresh.
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
