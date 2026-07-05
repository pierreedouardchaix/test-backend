import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import redis.asyncio as aioredis

from src.adapters.redis.channels import document_events_channel


class RedisEventStream:
    """Async read side: subscribes to a document's Redis pub/sub channel and
    yields each published event, deserialized. Counterpart of the synchronous
    RedisEventPublisher — a pub/sub subscription monopolizes its connection, so
    the SSE endpoint holds one dedicated async connection per connected client.

    Exposed as an async context manager, not a bare iterator, on purpose: the
    subscription is established on `__aenter__`, *before* the caller reads its
    DB snapshot. That ordering closes the connect-time gap — any event
    published while the snapshot is being read is already buffered by the live
    subscription (delivered as a possibly-idempotent duplicate) rather than
    lost. The `finally` guarantees the subscription and connection are released
    on client disconnect, terminal status, or exception — a closed browser tab
    never leaks a Redis connection.

    (Reconnection after a longer outage can still miss events — the DB snapshot
    re-reads current state on every reconnect, and Redis Streams would be the
    gap-free upgrade; see dev_considerations.md.)"""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    @asynccontextmanager
    async def subscribe(
        self, *, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        channel = document_events_channel(tenant_id, document_id)
        client = aioredis.from_url(self._redis_url)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        try:
            yield self._events(pubsub)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await client.aclose()

    async def _events(self, pubsub: Any) -> AsyncIterator[dict[str, Any]]:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue  # skip the subscribe confirmation and any control frames
            yield json.loads(message["data"])
