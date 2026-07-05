import uuid
from datetime import datetime, timezone

import pytest
import jwt

from src.adapters.jwt_auth import decode_token, issue_token

SECRET = "test-secret-key"
TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_round_trip():
    token = issue_token(tenant_id=TENANT_ID, user_id=USER_ID, secret=SECRET, expiry_seconds=3600)
    payload = decode_token(token, secret=SECRET)
    assert payload["sub"] == str(USER_ID)
    assert payload["tenant_id"] == str(TENANT_ID)


def test_expired_token_raises():
    token = issue_token(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        secret=SECRET,
        expiry_seconds=-1,
        now=_now(),
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token, secret=SECRET)


def test_wrong_secret_raises():
    token = issue_token(tenant_id=TENANT_ID, user_id=USER_ID, secret=SECRET, expiry_seconds=3600)
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token, secret="wrong-secret")


def test_tampered_payload_raises():
    token = issue_token(tenant_id=TENANT_ID, user_id=USER_ID, secret=SECRET, expiry_seconds=3600)
    header, payload, sig = token.split(".")
    import base64, json
    padded = payload + "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(padded))
    data["tenant_id"] = str(uuid.uuid4())
    new_payload = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    tampered = f"{header}.{new_payload}.{sig}"
    with pytest.raises(jwt.PyJWTError):
        decode_token(tampered, secret=SECRET)
