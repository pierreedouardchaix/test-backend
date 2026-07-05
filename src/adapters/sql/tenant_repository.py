import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.adapters.sql.mappers import tenant_from_orm, tenant_to_orm, user_to_orm
from src.adapters.sql.models import TenantORM, UserORM
from src.domain.models.tenant import Tenant


class SqlAlchemyTenantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        row = self._session.get(TenantORM, tenant_id)
        if row is None:
            return None
        user_rows = list(
            self._session.execute(select(UserORM).where(UserORM.tenant_id == tenant_id)).scalars()
        )
        return tenant_from_orm(row, user_rows)

    def save(self, tenant: Tenant) -> None:
        self._session.merge(tenant_to_orm(tenant))
        for user in tenant.user:
            self._session.merge(user_to_orm(user, tenant.id))
