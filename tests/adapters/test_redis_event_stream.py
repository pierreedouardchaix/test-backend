"""Focused test of RedisEventStream's own logic — skipping non-message control
frames and JSON-decoding the payload — with a fake async pub/sub injected in
place of the real redis.asyncio client. The real Redis round-trip is left to
the (deferred) integration tests, like the DB ones.

Uses asyncio.run rather than pulling in pytest-asyncio for a single test."""
import asyncio
import json
import uuid

import src.adapters.redis.event_stream as event_stream_module
from src.adapters.redis.event_stream import RedisEventStream


class _FakePubSub:
    def __init__(self, frames):
        self._frames = frames
        self.subscribed = None
        self.unsubscribed = None
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed = channel

    async def listen(self):
        for frame in self._frames:
            yield frame

    async def unsubscribe(self, channel):
        self.unsubscribed = channel

    async def aclose(self):
        self.closed = True


class _FakeClient:
    def __init__(self, pubsub):
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self):
        return self._pubsub

    async def aclose(self):
        self.closed = True


def test_skips_control_frames_and_yields_decoded_events(monkeypatch):
    pubsub = _FakePubSub(
        frames=[
            {"type": "subscribe", "data": 1},  # subscribe confirmation — must be skipped
            {"type": "message", "data": json.dumps({"step": "ocr", "step_status": "running"})},
            {"type": "message", "data": json.dumps({"step": "ocr", "step_status": "succeeded"})},
        ]
    )
    client = _FakeClient(pubsub)
    monkeypatch.setattr(event_stream_module.aioredis, "from_url", lambda url: client)

    async def collect():
        stream = RedisEventStream("redis://unused")
        async with stream.subscribe(tenant_id=uuid.uuid4(), document_id=uuid.uuid4()) as events:
            return [event async for event in events]

    events = asyncio.run(collect())

    assert events == [
        {"step": "ocr", "step_status": "running"},
        {"step": "ocr", "step_status": "succeeded"},
    ]
    # the subscription was established on context entry...
    assert pubsub.subscribed is not None
    # ...and released on exit
    assert pubsub.unsubscribed == pubsub.subscribed
    assert pubsub.closed is True
    assert client.closed is True
