"""End-to-end test with the real in-memory adapters (not test doubles) wired
into the real WorkflowOrchestrator, PipelineStepExecutor and
SynchronousPipelineDriver — proves the whole stack articulates correctly
together, not just each piece in isolation.

Uses a scripted FakeTaskInstanceRunner rather than the real ocr/metadata/chunking/
external_call functions: those have real random sleeps (1-15s) and a 1/3
failure rate, which would make this test slow and flaky. The real functions
are exercised manually / in a separate slow smoke test, not here.
"""

import uuid

from src.adapters.in_memory.blob_store import InMemoryBlobStore
from src.adapters.in_memory.event_publisher import InMemoryEventPublisher
from src.adapters.in_memory.unit_of_work import InMemoryUnitOfWork
from src.application.pipeline_step_executor import PipelineStepExecutor
from src.application.synchronous_pipeline_driver import SynchronousPipelineDriver
from src.domain.models.workflow import Workflow, WorkflowStatus
from src.domain.models.workflow_definition import StepDefinition, WorkflowDefinition
from tests.fakes import FakeTaskInstanceRunner


def make_primmo_shaped_definition() -> WorkflowDefinition:
    """Same shape as the target pipeline: ocr -> {metadata, chunking} -> external_call."""
    return WorkflowDefinition(
        name="primmo_ingestion",
        steps=(
            StepDefinition(name="ocr"),
            StepDefinition(name="metadata", depends_on=frozenset({"ocr"})),
            StepDefinition(name="chunking", depends_on=frozenset({"ocr"})),
            StepDefinition(name="external_call", depends_on=frozenset({"ocr", "metadata", "chunking"}), max_attempts=2),
        ),
    )


def test_full_pipeline_runs_to_success_with_real_in_memory_adapters():
    tenant_id = uuid.uuid4()
    workflow = Workflow.create(id=uuid.uuid4(), tenant_id=tenant_id, definition=make_primmo_shaped_definition())

    uow = InMemoryUnitOfWork()
    blob_store = InMemoryBlobStore()
    events = InMemoryEventPublisher()
    task_instance_runner = FakeTaskInstanceRunner({
        "ocr": ["lorem ipsum..."],
        "metadata": [{"doc_type": "fake_type"}],
        "chunking": [["chunk_1", "chunk_2"]],
        "external_call": ["j_abc123"],
    })
    executor = PipelineStepExecutor(
        uow_factory=lambda: uow, task_instance_runner=task_instance_runner, blob_store=blob_store, event_publisher=events
    )
    driver = SynchronousPipelineDriver(executor, uow.workflows)
    uow.workflows.save(workflow)

    driver.run(tenant_id=tenant_id, workflow_id=workflow.id)

    persisted = uow.workflows.get(workflow.id, tenant_id=tenant_id)
    assert persisted.status == WorkflowStatus.SUCCEEDED
    assert blob_store.get(persisted.results["ocr"]) is not None
    assert {step for step, _ in task_instance_runner.calls} == {"ocr", "metadata", "chunking", "external_call"}
    # every real transition got a real event through the real InMemoryEventPublisher
    assert len(events.published) >= 8  # 4 dispatches + 4 successes


def test_full_pipeline_ends_failed_when_a_step_exhausts_its_retries_with_real_in_memory_adapters():
    tenant_id = uuid.uuid4()
    definition = WorkflowDefinition(
        name="primmo_ingestion",
        steps=(
            StepDefinition(name="ocr", max_attempts=1),
            StepDefinition(name="metadata", depends_on=frozenset({"ocr"})),
            StepDefinition(name="chunking", depends_on=frozenset({"ocr"})),
            StepDefinition(name="external_call", depends_on=frozenset({"ocr", "metadata", "chunking"})),
        ),
    )
    workflow = Workflow.create(id=uuid.uuid4(), tenant_id=tenant_id, definition=definition)

    uow = InMemoryUnitOfWork()
    blob_store = InMemoryBlobStore()
    events = InMemoryEventPublisher()
    task_instance_runner = FakeTaskInstanceRunner({"ocr": [TimeoutError("OCR provider timeout")]})
    executor = PipelineStepExecutor(
        uow_factory=lambda: uow, task_instance_runner=task_instance_runner, blob_store=blob_store, event_publisher=events
    )
    driver = SynchronousPipelineDriver(executor, uow.workflows)
    uow.workflows.save(workflow)

    driver.run(tenant_id=tenant_id, workflow_id=workflow.id)

    persisted = uow.workflows.get(workflow.id, tenant_id=tenant_id)
    assert persisted.status == WorkflowStatus.FAILED
    assert persisted.failed_step == "ocr"
    assert {step for step, _ in task_instance_runner.calls} == {"ocr"}  # nothing downstream ever ran
