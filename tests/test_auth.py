"""Auth + tenant isolation tests using FastAPI TestClient with overridden deps."""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.adapters.jwt_auth import issue_token
from src.auth import AuthContext
from src.dependencies import get_session, get_settings
from src.domain.models.tenant import Tenant
from src.domain.models.user import User
from src.main import app
from src.settings import Settings

TENANT_ID = uuid.UUID("00000000-aaaa-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-aaaa-0000-0000-000000000011")
SECRET = "test-secret"

_settings = Settings(
    database_url="unused",
    jwt_secret=SECRET,
    jwt_expiry_seconds=3600,
    dev_mode=True,
)
_user = User(id=USER_ID, first_name="Alice", last_name="Test")
_tenant = Tenant(id=TENANT_ID, name="Acme", user=[_user])


def _mock_session():
    """Returns a session mock whose TenantRepository returns _tenant."""
    session = MagicMock()
    # SqlAlchemyTenantRepository calls session.get() then session.execute()
    from src.adapters.sql.models import TenantORM, UserORM
    tenant_orm = TenantORM(
        id=TENANT_ID, name="Acme", created_at=datetime.now(timezone.utc)
    )
    user_orm = UserORM(
        id=USER_ID, tenant_id=TENANT_ID, first_name="Alice", last_name="Test",
        created_at=datetime.now(timezone.utc),
    )
    session.get.return_value = tenant_orm
    scalars_mock = MagicMock()
    scalars_mock.__iter__ = MagicMock(return_value=iter([user_orm]))
    execute_mock = MagicMock()
    execute_mock.scalars.return_value = scalars_mock
    session.execute.return_value = execute_mock
    return session


def _valid_token() -> str:
    return issue_token(
        tenant_id=TENANT_ID, user_id=USER_ID, secret=SECRET, expiry_seconds=3600
    )


@pytest.fixture()
def client():
    session = _mock_session()
    app.dependency_overrides[get_settings] = lambda: _settings
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_me_without_token_returns_401(client):
    r = client.get("/me")
    assert r.status_code == 401


def test_me_with_valid_token_returns_200(client):
    r = client.get("/me", headers={"Authorization": f"Bearer {_valid_token()}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == str(USER_ID)
    assert body["tenant_id"] == str(TENANT_ID)
    assert body["first_name"] == "Alice"


def test_me_with_wrong_secret_returns_401(client):
    bad_token = issue_token(
        tenant_id=TENANT_ID, user_id=USER_ID, secret="wrong-secret", expiry_seconds=3600
    )
    r = client.get("/me", headers={"Authorization": f"Bearer {bad_token}"})
    assert r.status_code == 401


def test_me_with_expired_token_returns_401(client):
    expired = issue_token(
        tenant_id=TENANT_ID, user_id=USER_ID, secret=SECRET, expiry_seconds=-1
    )
    r = client.get("/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_tenant_isolation_user_from_other_tenant_gets_401(client):
    """Token for a real user but wrong tenant_id → tenant not found → 401."""
    other_tenant_id = uuid.uuid4()
    cross_token = issue_token(
        tenant_id=other_tenant_id, user_id=USER_ID, secret=SECRET, expiry_seconds=3600
    )
    # Make the session return None for any other tenant_id lookup
    session = _mock_session()
    session.get.return_value = None
    app.dependency_overrides[get_session] = lambda: session
    r = client.get("/me", headers={"Authorization": f"Bearer {cross_token}"})
    assert r.status_code == 401


def test_dev_token_endpoint_disabled_in_prod():
    prod_settings = Settings(
        database_url="unused", jwt_secret=SECRET, jwt_expiry_seconds=3600, dev_mode=False
    )
    app.dependency_overrides[get_settings] = lambda: prod_settings
    app.dependency_overrides[get_session] = lambda: _mock_session()
    with TestClient(app) as c:
        r = c.get(f"/auth/dev-token?tenant_id={TENANT_ID}&user_id={USER_ID}")
    app.dependency_overrides.clear()
    assert r.status_code == 403


def test_dev_token_endpoint_returns_token_in_dev_mode(client):
    r = client.get(f"/auth/dev-token?tenant_id={TENANT_ID}&user_id={USER_ID}")
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
