"""Idempotent seed: inserts two tenants with one user each.

UUIDs are fixed so re-running the script is safe (merge semantics).

Usage:
    PYTHONPATH=. DATABASE_URL=... uv run python scripts/seed.py
"""
import uuid

from src.adapters.sql.engine import create_db_engine, create_session_factory
from src.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from src.domain.models.tenant import Tenant
from src.domain.models.user import User

TENANT_A_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_A_ID = uuid.UUID("00000000-0000-0000-0000-000000000011")

TENANT_B_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_B_ID = uuid.UUID("00000000-0000-0000-0000-000000000022")


def _make_tenant(tenant_id: uuid.UUID, name: str, user_id: uuid.UUID, first: str, last: str) -> Tenant:
    user = User(id=user_id, first_name=first, last_name=last)
    return Tenant(id=tenant_id, name=name, user=[user])


def seed(uow: SqlAlchemyUnitOfWork) -> None:
    with uow as u:
        u.tenants.save(_make_tenant(TENANT_A_ID, "Acme Corp", USER_A_ID, "Alice", "Acme"))
        u.tenants.save(_make_tenant(TENANT_B_ID, "Beta Ltd", USER_B_ID, "Bob", "Beta"))
        u.commit()
    print(f"Seeded tenant A: {TENANT_A_ID} / user A: {USER_A_ID}")
    print(f"Seeded tenant B: {TENANT_B_ID} / user B: {USER_B_ID}")


if __name__ == "__main__":
    import os
    url = os.environ.get("DATABASE_URL")
    engine = create_db_engine(url)
    factory = create_session_factory(engine)
    seed(SqlAlchemyUnitOfWork(factory))
