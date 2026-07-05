import uuid
from datetime import datetime, timezone
from typing import Any

import jwt

_ALGORITHM = "HS256"


def issue_token(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    secret: str,
    expiry_seconds: int,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + expiry_seconds,
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(token: str, *, secret: str) -> dict[str, Any]:
    """Decode and verify a token. Raises jwt.PyJWTError on any failure."""
    return jwt.decode(token, secret, algorithms=[_ALGORITHM])
