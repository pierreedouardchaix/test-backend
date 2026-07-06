"""Document construction invariants raise the typed DomainValidationError
(which the API maps to 422) — not a bare ValueError that would surface as 500."""
import uuid

import pytest

from src.domain.errors import DomainValidationError
from src.domain.models.document import Document


def _create(**overrides):
    kwargs = dict(
        tenant_id=uuid.uuid4(),
        uploaded_by=uuid.uuid4(),
        filename="doc.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        blob_key="blob-1",
    )
    kwargs.update(overrides)
    return Document.create(**kwargs)


def test_create_with_a_valid_document_succeeds():
    doc = _create()
    assert doc.filename == "doc.pdf"
    assert doc.size_bytes == 1024


def test_empty_filename_raises_domain_validation_error():
    with pytest.raises(DomainValidationError):
        _create(filename="   ")


def test_non_positive_size_raises_domain_validation_error():
    with pytest.raises(DomainValidationError):
        _create(size_bytes=0)


def test_domain_validation_error_is_also_a_value_error():
    # Backward-compat: callers/tests that catch ValueError keep working.
    with pytest.raises(ValueError):
        _create(size_bytes=-1)
