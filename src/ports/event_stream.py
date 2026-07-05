import uuid
from contextlib import AbstractAsyncContextManager
from typing import Any, AsyncIterator, Protocol


class EventStream(Protocol):
    """Read side of the real-time transport — the consuming counterpart of
    EventPublisher. Streams a document's status-change events as they are
    published, for as long as the caller keeps iterating.

    Async because the SSE endpoint holds one long-lived subscription per
    connected client on the event loop (never a threadpool thread).

    `subscribe` is an async context manager: the subscription is established on
    entry (so the caller can read its DB snapshot afterwards with no gap), and
    the underlying subscription/connection is released on exit (client
    disconnect, terminal status, or exception)."""

    def subscribe(
        self, *, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]:
        """Enter to establish the subscription; the yielded iterator produces
        each event published for this document, in arrival order."""
        ...
