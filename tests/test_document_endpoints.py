"""Endpoint-level check that the app-level DomainError handlers map to the right
status — here, DocumentNotFound (raised by the use case, no try/except in the
router) surfaces as a 404 through the handler registered in main.py."""
import uuid

from fastapi.testclient import TestClient

from src.auth import AuthContext, get_current_user
from src.dependencies import get_document_data_source
from src.domain.models.user import User
from src.main import app
from tests.fakes import FakeDocumentDataSource

TENANT = uuid.uuid4()
USER = User(id=uuid.uuid4(), first_name="Alice", last_name="Acme")


def test_get_unknown_document_is_404_via_the_domain_error_handler():
    data_source = FakeDocumentDataSource()  # empty → get_by_id returns None → DocumentNotFound
    app.dependency_overrides[get_current_user] = lambda: AuthContext(tenant_id=TENANT, user=USER)
    app.dependency_overrides[get_document_data_source] = lambda: data_source
    try:
        r = TestClient(app).get(f"/documents/{uuid.uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404
    assert "detail" in r.json()
