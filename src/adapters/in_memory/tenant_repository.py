import uuid

from src.domain.models.tenant import Tenant


class InMemoryTenantRepository:
    def __init__(self) -> None:
        self._tenants: dict[uuid.UUID, Tenant] = {}

    def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def save(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant
