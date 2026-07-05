import json
import uuid
from typing import Any, AsyncIterator

import redis.asyncio as aioredis

from src.adapters.redis.channels import document_events_channel


class RedisEventStream:
    """Async read side: subscribes to a document's Redis pub/sub channel and
    yields each published event, deserialized. Counterpart of the synchronous
    RedisEventPublisher — a pub/sub subscription monopolizes its connection, so
    the SSE endpoint holds one dedicated async connection per connected client.

    The subscription and connection are always released when iteration stops —
    client disconnect, terminal status, or an exception — via the finally
    block, so a closed browser tab never leaks a Redis connection."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def subscribe(
        self, *, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> AsyncIterator[dict[str, Any]]:
        channel = document_events_channel(tenant_id, document_id)
        client = aioredis.from_url(self._redis_url)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue  # skip the subscribe confirmation and any control frames
                yield json.loads(message["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await client.aclose()
