import uuid
from typing import Any


class InMemoryEventPublisher:
    """Keeps published events in a list — no real subscribers exist yet.
    The SSE endpoint will need a pub/sub-capable implementation (Redis)
    instead; this one only proves the wiring."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, *, tenant_id: uuid.UUID, document_id: uuid.UUID, event: dict[str, Any]) -> None:
        self.published.append({"tenant_id": tenant_id, "document_id": document_id, "event": event})
