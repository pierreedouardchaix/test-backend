"""Auth router.

This is intentionally the only place in the codebase where an endpoint reaches
directly into a repository without going through an application service.
It exists solely to issue dev tokens and is gated behind DEV_MODE — no
business logic lives here. All other routers must go through use cases.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from src.adapters.jwt_auth import issue_token
from src.adapters.sql.tenant_repository import SqlAlchemyTenantRepository
from src.dependencies import get_session, get_settings
from src.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/dev-token")
def dev_token(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    settings: Settings = Depends(get_settings),
    session=Depends(get_session),
):
    if not settings.dev_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available in production")

    tenant = SqlAlchemyTenantRepository(session).get(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if not any(u.id == user_id for u in tenant.user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    token = issue_token(
        tenant_id=tenant_id,
        user_id=user_id,
        secret=settings.jwt_secret,
        expiry_seconds=settings.jwt_expiry_seconds,
    )
    return {"access_token": token, "token_type": "bearer"}
