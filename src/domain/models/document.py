import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Self


@dataclass
class Document:
    """A document's own id doubles as its Workflow's id (1:1, fixed at
    creation — see dev_considerations.md) — no separate workflow_id to keep
    in sync."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    uploaded_by: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    blob_key: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.filename.strip():
            raise ValueError("Document filename must not be empty")
        if self.size_bytes <= 0:
            raise ValueError("Document size_bytes must be positive")

    @classmethod
    def create(
        cls,
        *,
        tenant_id: uuid.UUID,
        uploaded_by: uuid.UUID,
        filename: str,
        content_type: str,
        size_bytes: int,
        blob_key: str,
    ) -> Self:
        return cls(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            uploaded_by=uploaded_by,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            blob_key=blob_key,
        )
