import json
import uuid
from typing import Any

import redis

from src.adapters.redis.channels import document_events_channel


class RedisEventPublisher:
    """Write side of the real-time transport: PUBLISHes each status-change
    event to the document's Redis channel, where the SSE endpoint (a separate
    process — the API, while the publish comes from a Celery worker or the
    webhook handler) picks it up. This is what replaces the process-local
    InMemoryEventPublisher whose events never left the worker.

    Synchronous client on purpose: publish() is called from synchronous code
    (a Celery task via the orchestrator, or the webhook handler). The SSE
    consumer side uses a separate async client (see RedisEventStream).

    Fire-and-forget, matching the port's contract: a PUBLISH with no current
    subscriber is dropped by Redis; the DB row remains the source of truth,
    and a (re)connecting client gets the current state from a DB snapshot.
    Event values are StrEnum (TaskStatus/WorkflowStatus) — str subclasses, so
    json.dumps serializes them to their plain string value.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, redis_url: str) -> "RedisEventPublisher":
        return cls(redis.Redis.from_url(redis_url))

    def publish(self, *, tenant_id: uuid.UUID, document_id: uuid.UUID, event: dict[str, Any]) -> None:
        self._client.publish(document_events_channel(tenant_id, document_id), json.dumps(event))
