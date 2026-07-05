import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.adapters.jwt_auth import decode_token
from src.adapters.sql.tenant_repository import SqlAlchemyTenantRepository
from src.dependencies import get_session, get_settings
from src.domain.models.user import User

_bearer = HTTPBearer()


@dataclass(frozen=True)
class AuthContext:
    tenant_id: uuid.UUID
    user: User


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session=Depends(get_session),
    settings=Depends(get_settings),
) -> AuthContext:
    try:
        payload = decode_token(credentials.credentials, secret=settings.jwt_secret)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    tenant_id = uuid.UUID(payload["tenant_id"])
    user_id = uuid.UUID(payload["sub"])

    tenant = SqlAlchemyTenantRepository(session).get(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tenant not found")

    user = next((u for u in tenant.user if u.id == user_id), None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return AuthContext(tenant_id=tenant_id, user=user)
