"""Row-level security wiring.

Every business table (documents, workflows, tasks) is RLS-scoped by tenant in
the DB (see the alembic migration): a row is visible/writable only when the
session GUC `app.current_tenant` matches its `tenant_id`, or is the bypass
sentinel `*` (for the tenant-less ingress paths — partner webhook resolution).

The GUC is applied transaction-locally (`SET LOCAL`, reset on commit/rollback)
via an `after_begin` listener, from a value stashed on `session.info`. Doing it
per-transaction rather than per-connection avoids a stale GUC leaking across the
pool: a pooled connection reused by another tenant's session re-applies the new
tenant on its next transaction. Sessions that never call `scope_session_to_tenant`
(e.g. the auth lookup on the non-RLS identity tables) leave the GUC unset, which
the policy treats as "see nothing" — fail-closed."""
import uuid

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.application.unit_of_work import CROSS_TENANT

_TENANT_KEY = "rls_tenant"
TENANT_BYPASS = CROSS_TENANT  # cross-tenant ingress (partner webhook has no tenant context)


_SET_TENANT = text("SELECT set_config('app.current_tenant', :t, true)")


def scope_session_to_tenant(session: Session, tenant: uuid.UUID | str) -> None:
    """Bind this session to a tenant (or TENANT_BYPASS) for RLS. Applies the GUC
    to the current transaction immediately, and records it on session.info so it
    is re-applied to any later transaction on the same session (after_begin) —
    SET LOCAL is reset at each commit/rollback, so a long-lived session must
    re-apply it per transaction."""
    value = str(tenant)
    session.info[_TENANT_KEY] = value
    session.execute(_SET_TENANT, {"t": value})


@event.listens_for(Session, "after_begin")
def _apply_tenant_guc(session: Session, transaction, connection) -> None:
    tenant = session.info.get(_TENANT_KEY)
    if tenant is not None:
        # is_local=true → scoped to this transaction, auto-reset on commit/rollback.
        connection.execute(_SET_TENANT, {"t": tenant})
