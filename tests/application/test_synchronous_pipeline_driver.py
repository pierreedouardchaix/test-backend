import uuid

from src.application.synchronous_pipeline_driver import SynchronousPipelineDriver
from src.application.workflow_orchestrator import WorkflowOrchestrator
from src.domain.models.task import TaskStatus
from src.domain.models.workflow import Workflow, WorkflowStatus
from src.domain.models.workflow_definition import StepDefinition, WorkflowDefinition
from src.ports.task_instance_runner import DEFERRED
from tests.fakes import (
    FakeBlobStore,
    FakeEventPublisher,
    FakeTaskInstanceRunner,
    FakeWorkflowRepository,
)


def make_definition(**step_overrides: int) -> WorkflowDefinition:
    """t1 is the root; t2 and t3 fan out from it; t4 fans back in on both —
    same shape as the target 4-node Primmo pipeline (ocr -> {metadata,
    chunking} -> external_call), with generic names since the concrete
    definition isn't finalized yet (see dev_considerations.md)."""
    return WorkflowDefinition(
        name="test_pipeline",
        steps=(
            StepDefinition(name="t1", max_attempts=step_overrides.get("t1", 3)),
            StepDefinition(name="t2", depends_on=frozenset({"t1"}), max_attempts=step_overrides.get("t2", 3)),
            StepDefinition(name="t3", depends_on=frozenset({"t1"}), max_attempts=step_overrides.get("t3", 3)),
            StepDefinition(
                name="t4", depends_on=frozenset({"t2", "t3"}), max_attempts=step_overrides.get("t4", 3)
            ),
        ),
    )


def make_driver(task_instance_runner):
    workflow_repository = FakeWorkflowRepository()
    blob_store = FakeBlobStore()
    events = FakeEventPublisher()
    orchestrator = WorkflowOrchestrator(workflow_repository, blob_store, events)
    driver = SynchronousPipelineDriver(orchestrator, task_instance_runner, blob_store)
    return driver, workflow_repository, blob_store, events


def test_drives_a_full_fan_out_fan_in_pipeline_to_success():
    tenant_id = uuid.uuid4()
    workflow = Workflow.create(id=uuid.uuid4(), tenant_id=tenant_id, definition=make_definition())
    task_instance_runner = FakeTaskInstanceRunner({
        "t1": ["r1"],
        "t2": ["r2"],
        "t3": ["r3"],
        "t4": ["r4"],
    })
    driver, repository, blob_store, _ = make_driver(task_instance_runner)
    repository.save(workflow)

    driver.run(tenant_id=tenant_id, workflow=workflow)

    assert workflow.status == WorkflowStatus.SUCCEEDED
    assert blob_store.get_json(workflow.results["t1"]) == "r1"
    assert blob_store.get_json(workflow.results["t4"]) == "r4"
    assert {step for step, _ in task_instance_runner.calls} == {"t1", "t2", "t3", "t4"}


def test_resolves_dependency_outputs_into_real_values_before_calling_the_task_instance_runner():
    tenant_id = uuid.uuid4()
    workflow = Workflow.create(id=uuid.uuid4(), tenant_id=tenant_id, definition=make_definition())
    task_instance_runner = FakeTaskInstanceRunner({
        "t1": [{"text": "lorem ipsum"}],
        "t2": ["r2"],
        "t3": ["r3"],
        "t4": ["r4"],
    })
    driver, repository, _, _ = make_driver(task_instance_runner)
    repository.save(workflow)

    driver.run(tenant_id=tenant_id, workflow=workflow)

    t2_inputs = next(inputs for step, inputs in task_instance_runner.calls if step == "t2")
    assert t2_inputs == {"t1": {"text": "lorem ipsum"}}  # the real value, not a blob key
    t4_inputs = next(inputs for step, inputs in task_instance_runner.calls if step == "t4")
    assert t4_inputs == {"t2": "r2", "t3": "r3"}


def test_retries_a_transient_failure_automatically_until_it_succeeds():
    tenant_id = uuid.uuid4()
    workflow = Workflow.create(id=uuid.uuid4(), tenant_id=tenant_id, definition=make_definition(t2=3))
    task_instance_runner = FakeTaskInstanceRunner({
        "t1": ["r1"],
        "t2": [TimeoutError("transient"), "r2"],  # fails once, then succeeds
        "t3": ["r3"],
        "t4": ["r4"],
    })
    driver, repository, _, _ = make_driver(task_instance_runner)
    repository.save(workflow)

    driver.run(tenant_id=tenant_id, workflow=workflow)

    assert workflow.status == WorkflowStatus.SUCCEEDED
    assert workflow.tasks["t2"].attempts == 2
    assert workflow.tasks["t2"].errors[0].error == "transient"


def test_stops_the_whole_workflow_on_a_terminal_failure():
    tenant_id = uuid.uuid4()
    workflow = Workflow.create(id=uuid.uuid4(), tenant_id=tenant_id, definition=make_definition(t2=1))
    task_instance_runner = FakeTaskInstanceRunner({
        "t1": ["r1"],
        "t2": [ValueError("boom")],  # only one attempt allowed -> terminal
        "t3": ["r3"],
        "t4": ["r4"],
    })
    driver, repository, _, _ = make_driver(task_instance_runner)
    repository.save(workflow)

    driver.run(tenant_id=tenant_id, workflow=workflow)

    assert workflow.status == WorkflowStatus.FAILED
    assert workflow.failed_step == "t2"
    assert "t4" not in {step for step, _ in task_instance_runner.calls}


def _callback_definition() -> WorkflowDefinition:
    """t1 -> t2, where t2 hands off to an external executor and waits."""
    return WorkflowDefinition(
        name="callback_pipeline",
        steps=(
            StepDefinition(name="t1"),
            StepDefinition(name="t2", depends_on=frozenset({"t1"})),
        ),
    )


def test_deferred_step_leaves_workflow_running_with_its_inputs_resolved():
    tenant_id = uuid.uuid4()
    workflow = Workflow.create(id=uuid.uuid4(), tenant_id=tenant_id, definition=_callback_definition())
    task_instance_runner = FakeTaskInstanceRunner({
        "t1": [{"text": "extracted"}],
        "t2": [DEFERRED],  # runner signals: handed off, wait for callback
    })
    driver, repository, _, _ = make_driver(task_instance_runner)
    repository.save(workflow)

    driver.run(tenant_id=tenant_id, workflow=workflow)

    # The runner received the resolved upstream inputs for t2.
    t2_inputs = next(inputs for step, inputs in task_instance_runner.calls if step == "t2")
    assert t2_inputs == {"t1": {"text": "extracted"}}

    # The workflow is left running — nothing is newly ready until the callback arrives.
    assert workflow.status == WorkflowStatus.RUNNING
    assert workflow.tasks["t2"].status == TaskStatus.RUNNING
    assert "t2" not in workflow.results
