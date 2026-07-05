"""Suite 4 — the real-time transport over a real Redis: a sync RedisEventPublisher
PUBLISH is received by the async RedisEventStream subscriber (the cross-process
path the SSE endpoint relies on)."""
import pytest

pytestmark = pytest.mark.integration

import asyncio
import uuid

from src.adapters.redis.event_publisher import RedisEventPublisher
from src.adapters.redis.event_stream import RedisEventStream


async def test_published_event_is_received_by_a_subscriber(redis_url):
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    publisher = RedisEventPublisher.from_url(redis_url)
    stream = RedisEventStream(redis_url)

    async with stream.subscribe(tenant_id=tenant_id, document_id=document_id) as events:
        # Subscription established → publishing now must reach us.
        publisher.publish(
            tenant_id=tenant_id, document_id=document_id,
            event={"step": "ocr", "status": "processing", "version": 1},
        )
        received = await asyncio.wait_for(events.__anext__(), timeout=5)

    assert received == {"step": "ocr", "status": "processing", "version": 1}


async def test_subscriber_only_receives_its_own_documents_channel(redis_url):
    tenant_id = uuid.uuid4()
    mine, other = uuid.uuid4(), uuid.uuid4()
    publisher = RedisEventPublisher.from_url(redis_url)
    stream = RedisEventStream(redis_url)

    async with stream.subscribe(tenant_id=tenant_id, document_id=mine) as events:
        publisher.publish(tenant_id=tenant_id, document_id=other, event={"v": "other"})   # different channel
        publisher.publish(tenant_id=tenant_id, document_id=mine, event={"v": "mine"})      # our channel
        received = await asyncio.wait_for(events.__anext__(), timeout=5)

    assert received == {"v": "mine"}  # the other document's event never arrives here
