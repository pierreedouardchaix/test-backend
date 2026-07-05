import json
import uuid

from src.domain.models.workflow import Workflow


class FakeWorkflowRepository:
    def __init__(self):
        self.saved: dict[uuid.UUID, Workflow] = {}

    def get(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Workflow | None:
        workflow = self.saved.get(workflow_id)
        if workflow is None or workflow.tenant_id != tenant_id:
            return None
        return workflow

    def save(self, workflow: Workflow) -> None:
        self.saved[workflow.id] = workflow


class FakeBlobStore:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self._next_key = 0

    def put(self, content: bytes, *, content_type: str) -> str:
        self._next_key += 1
        key = f"blob-{self._next_key}"
        self.blobs[key] = content
        return key

    def get(self, blob_key: str) -> bytes:
        return self.blobs[blob_key]

    def get_json(self, blob_key: str):
        return json.loads(self.blobs[blob_key])


class FakeEventPublisher:
    def __init__(self):
        self.published: list[dict] = []

    def publish(self, *, tenant_id, document_id, event) -> None:
        self.published.append({"tenant_id": tenant_id, "document_id": document_id, "event": event})


class FakeTaskInstanceRunner:
    """Scripted outcomes per step, consumed in order across attempts.
    Pass an Exception instance to simulate a failed attempt."""

    def __init__(self, behaviors: dict[str, list]):
        self._behaviors = {step: list(outcomes) for step, outcomes in behaviors.items()}
        self.calls: list[tuple[str, dict]] = []

    def run_step(self, step_name, *, document_id, inputs):
        self.calls.append((step_name, inputs))
        outcome = self._behaviors[step_name].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
