import uuid

from fastapi import Depends, FastAPI, HTTPException, status

from src.adapters.jwt_auth import issue_token
from src.adapters.sql.tenant_repository import SqlAlchemyTenantRepository
from src.auth import AuthContext, get_current_user
from src.dependencies import get_session, get_settings
from src.settings import Settings

app = FastAPI()


@app.get("/healthcheck")
def healthcheck():
    return {"status": "ok"}


@app.get("/auth/dev-token")
def dev_token(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    settings: Settings = Depends(get_settings),
    session=Depends(get_session),
):
    """Issues a short-lived token. Only available when DEV_MODE=true."""
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


@app.get("/me")
def me(auth: AuthContext = Depends(get_current_user)):
    return {
        "user_id": str(auth.user.id),
        "first_name": auth.user.first_name,
        "last_name": auth.user.last_name,
        "tenant_id": str(auth.tenant_id),
    }
