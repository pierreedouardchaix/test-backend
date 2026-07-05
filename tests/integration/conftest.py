"""Integration-test harness: a real Postgres (with our migrations, RLS policies
and the non-superuser `primmo_app` role) and a real Redis, via testcontainers.

Two Postgres engines are exposed:
- `superuser_sessionmaker` — the container superuser, for setup/seed/asserts
  (bypasses RLS, owns the tables).
- `app_sessionmaker` — the non-superuser `primmo_app` role the app connects as
  in production, the ONLY way RLS actually bites (superusers/owners bypass it).

Every test starts with the business tables truncated and tenants A/B seeded.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

import src.adapters.sql.rls  # noqa: F401 — registers the RLS after_begin listener on Session
from src.adapters.sql.engine import create_session_factory

TENANT_A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_A = uuid.UUID("aaaaaaaa-0000-0000-0000-0000000000a1")
TENANT_B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
USER_B = uuid.UUID("bbbbbbbb-0000-0000-0000-0000000000b2")


@pytest.fixture(scope="session")
def _postgres():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:17", driver="psycopg", username="primmo", password="primmo", dbname="primmo") as pg:
        yield pg


@pytest.fixture(scope="session")
def superuser_url(_postgres) -> str:
    """Superuser connection URL, with our migrations applied (schema + RLS +
    the primmo_app role)."""
    url = _postgres.get_connection_url()  # postgresql+psycopg://primmo:primmo@host:port/primmo
    from alembic import command
    from alembic.config import Config

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic/env.py reads DATABASE_URL
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    return url


@pytest.fixture(scope="session")
def app_url(superuser_url) -> str:
    # Same database, but as the non-superuser role the migration created.
    return superuser_url.replace("//primmo:primmo@", "//primmo_app:primmo_app@")


@pytest.fixture(scope="session")
def superuser_sessionmaker(superuser_url):
    return create_session_factory(create_engine(superuser_url, future=True))


@pytest.fixture(scope="session")
def app_sessionmaker(app_url):
    return create_session_factory(create_engine(app_url, future=True))


@pytest.fixture
def app_session(app_sessionmaker):
    """A `primmo_app` session that is guaranteed closed after the test — a
    leaked session would hold an open transaction (ACCESS SHARE lock) and make
    the next test's TRUNCATE hang."""
    session = app_sessionmaker()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _clean_and_seed(superuser_sessionmaker):
    """Truncate the tenant-scoped tables and (re)seed tenants A/B + a user each,
    as superuser (RLS doesn't apply; identity tables aren't RLS'd anyway)."""
    session = superuser_sessionmaker()
    try:
        # Fail fast on a leaked open transaction from a prior test instead of hanging.
        session.execute(text("SET lock_timeout = '10s'"))
        session.execute(text("TRUNCATE tasks, workflows, documents, users, tenants CASCADE"))
        now = datetime.now(tz=timezone.utc)
        for tid, uid, name, first in ((TENANT_A, USER_A, "Acme", "Alice"), (TENANT_B, USER_B, "Beta", "Bob")):
            session.execute(
                text("INSERT INTO tenants (id, name, created_at) VALUES (:id, :name, :ts)"),
                {"id": tid, "name": name, "ts": now},
            )
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, first_name, last_name, created_at) "
                    "VALUES (:id, :tid, :first, 'X', :ts)"
                ),
                {"id": uid, "tid": tid, "first": first, "ts": now},
            )
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture(scope="session")
def redis_url() -> str:
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as r:
        host = r.get_container_host_ip()
        port = r.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"
