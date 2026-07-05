import uuid
from typing import Any, AsyncIterator, Protocol


class EventStream(Protocol):
    """Read side of the real-time transport — the consuming counterpart of
    EventPublisher. Yields a document's status-change events as they are
    published, for as long as the caller keeps iterating.

    Async because the SSE endpoint holds one long-lived subscription per
    connected client on the event loop (never a threadpool thread). The
    returned iterator must clean up its underlying subscription/connection
    when the caller stops iterating (client disconnect, terminal status)."""

    def subscribe(
        self, *, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield each event published for this document, in arrival order,
        until the caller stops consuming the iterator."""
        ...
