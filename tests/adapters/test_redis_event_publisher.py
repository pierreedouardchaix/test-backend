import json
import uuid

from src.adapters.redis.channels import document_events_channel
from src.adapters.redis.event_publisher import RedisEventPublisher
from src.domain.models.task import TaskStatus
from src.domain.models.workflow import WorkflowStatus


class FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, channel, message):
        self.published.append((channel, message))


def test_publishes_json_to_the_document_channel():
    client = FakeRedis()
    publisher = RedisEventPublisher(client)
    tenant_id = uuid.uuid4()
    document_id = uuid.uuid4()

    publisher.publish(
        tenant_id=tenant_id,
        document_id=document_id,
        event={"step": "ocr", "step_status": "running", "attempt": 1},
    )

    assert len(client.published) == 1
    channel, message = client.published[0]
    assert channel == document_events_channel(tenant_id, document_id)
    assert json.loads(message) == {"step": "ocr", "step_status": "running", "attempt": 1}


def test_serializes_strenum_event_values_to_plain_strings():
    """The orchestrator puts TaskStatus/WorkflowStatus (StrEnum) in the event —
    they must round-trip through JSON as their plain string value so any
    consumer, in any language, reads a normal string."""
    client = FakeRedis()
    publisher = RedisEventPublisher(client)

    publisher.publish(
        tenant_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        event={"step_status": TaskStatus.SUCCEEDED, "workflow_status": WorkflowStatus.RUNNING},
    )

    _, message = client.published[0]
    assert json.loads(message) == {"step_status": "succeeded", "workflow_status": "running"}
