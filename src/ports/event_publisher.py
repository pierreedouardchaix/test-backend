import uuid
from typing import Any, Protocol


class EventPublisher(Protocol):
    """Publishes a document's status-change events for real-time consumers (SSE)."""

    def publish(self, *, tenant_id: uuid.UUID, document_id: uuid.UUID, event: dict[str, Any]) -> None:
        """Publish one event for a document. Fire-and-forget: no subscriber
        means the event is simply dropped, the DB row remains the source of truth."""
