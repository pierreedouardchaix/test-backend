from typing import Protocol

import uuid

from src.domain.models.tenant import Tenant


class TenantRepository(Protocol):
    """Loads and persists a Tenant together with the Users it owns.

    There is no separate UserRepository: `Tenant.user` is a composition
    (Tenant is the aggregate root), so a User is only ever reached through
    its Tenant.
    """
    def get(self, tenant_id: uuid.UUID) -> Tenant | None: ...

    def save(self, tenant: Tenant) -> None: ...
