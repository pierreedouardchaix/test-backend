"""enable row-level security on the tenant-scoped business tables

Revision ID: b2d0rls00002
Revises: a1c0partner01
Create Date: 2026-07-06 09:00:00.000000

Each business table is scoped by tenant in the DB itself: a row is visible/
writable only when the session GUC `app.current_tenant` equals its `tenant_id`,
or is the bypass sentinel `*` (tenant-less ingress). An unset GUC
(`current_setting(..., true)` → NULL) matches nothing — fail-closed.

FORCE is required because the app connects as the table owner (primmo), and
owners bypass ordinary RLS. Identity tables (tenants, users) are intentionally
left un-RLS'd: the auth lookup reads them *before* a tenant is established.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b2d0rls00002'
down_revision: Union[str, Sequence[str], None] = 'a1c0partner01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("documents", "workflows", "tasks")

_POLICY = """
CREATE POLICY tenant_isolation ON {table}
  USING (
    current_setting('app.current_tenant', true) = '*'
    OR tenant_id::text = current_setting('app.current_tenant', true)
  )
  WITH CHECK (
    current_setting('app.current_tenant', true) = '*'
    OR tenant_id::text = current_setting('app.current_tenant', true)
  )
"""


def upgrade() -> None:
    # RLS is bypassed by superusers and by the table owner (unless FORCEd). The
    # migrate/seed connection is the superuser `primmo`; the app/worker must
    # therefore connect as a NON-superuser, non-owner role so policies actually
    # apply. Password is dev-only, matching docker-compose (rotate in prod).
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'primmo_app') THEN
            CREATE ROLE primmo_app LOGIN PASSWORD 'primmo_app' NOSUPERUSER NOBYPASSRLS;
          END IF;
        END $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO primmo_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO primmo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO primmo_app")

    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(_POLICY.format(table=table))


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM primmo_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM primmo_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM primmo_app")
