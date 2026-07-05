import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from src.adapters.jwt_auth import decode_token
from src.adapters.sql.tenant_repository import SqlAlchemyTenantRepository
from src.dependencies import get_session, get_settings
from src.domain.models.user import User
from src.settings import Settings

_bearer = HTTPBearer()


@dataclass(frozen=True)
class AuthContext:
    tenant_id: uuid.UUID
    user: User


def _authenticate(token: str, session: Session, settings: Settings) -> AuthContext:
    """Decode a JWT and resolve it to an AuthContext, verifying the tenant and
    user still exist. Shared by the header-based auth (normal endpoints) and
    the query-param auth (SSE, where the browser's EventSource can't set an
    Authorization header)."""
    try:
        payload = decode_token(token, secret=settings.jwt_secret)
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


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session=Depends(get_session),
    settings=Depends(get_settings),
) -> AuthContext:
    return _authenticate(credentials.credentials, session, settings)


def get_current_user_from_query_token(
    token: str = Query(..., description="JWT — passed in the query string because the browser's EventSource cannot set an Authorization header"),
    session=Depends(get_session),
    settings=Depends(get_settings),
) -> AuthContext:
    """Auth for the SSE endpoint. The native EventSource API cannot attach an
    Authorization header, so the token travels in the query string instead.
    Tradeoff (token visible in access logs / URL) is documented in
    dev_considerations.md — a Secure;HttpOnly cookie is the production upgrade."""
    return _authenticate(token, session, settings)
