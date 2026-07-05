from fastapi.testclient import TestClient

from src.dependencies import get_settings
from src.main import app
from src.partner_hmac import sign
from src.settings import Settings

SECRET = "partner-secret"

_dev_settings = Settings(
    database_url="unused",
    jwt_secret="unused",
    jwt_expiry_seconds=3600,
    dev_mode=True,
    partner_hmac_secret=SECRET,
)
_prod_settings = Settings(
    database_url="unused",
    jwt_secret="unused",
    jwt_expiry_seconds=3600,
    dev_mode=False,
    partner_hmac_secret=SECRET,
)


def test_dev_sign_returns_signature_of_exact_body():
    app.dependency_overrides[get_settings] = lambda: _dev_settings
    body = b'{"job_id":"abc","status":"completed"}'
    try:
        with TestClient(app) as c:
            r = c.post("/dev/sign", content=body)
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.json()["signature"] == sign(body, secret=SECRET)


def test_dev_sign_disabled_in_prod():
    app.dependency_overrides[get_settings] = lambda: _prod_settings
    try:
        with TestClient(app) as c:
            r = c.post("/dev/sign", content=b"{}")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 403
