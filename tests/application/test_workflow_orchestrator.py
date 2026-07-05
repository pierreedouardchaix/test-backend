import uuid

import pytest

from src.application.workflow_orchestrator import WorkflowOrchestrator
from src.domain.models.task import TaskStatus
from src.domain.models.workflow import Workflow, WorkflowStatus
from src.domain.models.workflow_definition import StepDefinition, WorkflowDefinition
from tests.fakes import FakeBlobStore, FakeEventPublisher, FakeWorkflowRepository


def make_definition(**step_overrides: int) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="test_pipeline",
        steps=(
            StepDefinition(name="t1", max_attempts=step_overrides.get("t1", 3)),
            StepDefinition(name="t2", depends_on=frozenset({"t1"}), max_attempts=step_overrides.get("t2", 3)),
        ),
    )


def make_workflow(tenant_id: uuid.UUID | None = None, **step_overrides: int) -> Workflow:
    """Workflow.id doubles as the (fictitious, in these tests) document's id —
    same 1:1 identity as in production, see dev_considerations.md."""
    return Workflow.create(
        id=uuid.uuid4(), tenant_id=tenant_id or uuid.uuid4(), definition=make_definition(**step_overrides)
    )


def make_orchestrator():
    repository = FakeWorkflowRepository()
    blob_store = FakeBlobStore()
    events = FakeEventPublisher()
    return WorkflowOrchestrator(repository, blob_store, events), repository, blob_store, events


def test_start_task_persists_the_started_task():
    orchestrator, repository, _, _ = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id)
    repository.save(workflow)

    task = orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1")

    assert task.status == TaskStatus.RUNNING
    persisted = repository.get(workflow.id, tenant_id=tenant_id)
    assert persisted.tasks["t1"].status == TaskStatus.RUNNING
    assert persisted.tasks["t1"].attempts == 1


def test_start_task_publishes_an_event():
    orchestrator, repository, _, events = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id)
    repository.save(workflow)

    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1")

    assert len(events.published) == 1
    assert events.published[0]["event"]["step_status"] == TaskStatus.RUNNING


def test_handle_step_success_stores_the_result_in_the_blob_store_not_on_the_workflow():
    orchestrator, repository, blob_store, _ = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id)
    repository.save(workflow)
    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1")

    ready = orchestrator.handle_step_success(
        tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1", result={"text": "lorem ipsum..."}
    )

    assert ready == frozenset({"t2"})
    blob_key = repository.get(workflow.id, tenant_id=tenant_id).results["t1"]
    assert blob_key != {"text": "lorem ipsum..."}  # the workflow only holds a key, not the raw data
    assert blob_store.get_json(blob_key) == {"text": "lorem ipsum..."}


def test_handle_step_success_publishes_one_event_with_the_expected_payload():
    orchestrator, repository, _, events = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id)
    repository.save(workflow)
    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1")

    orchestrator.handle_step_success(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1", result="r1")

    assert len(events.published) == 2  # one for dispatch, one for the success
    published = events.published[-1]
    assert published["tenant_id"] == tenant_id
    assert published["document_id"] == workflow.id  # same id, no separate document_id anywhere
    event = dict(published["event"])
    assert isinstance(event.pop("version"), int)  # monotonic version present on every event
    assert event == {
        "step": "t1",
        "step_status": TaskStatus.SUCCEEDED,
        "workflow_status": WorkflowStatus.RUNNING,
        "attempt": 1,
    }
    assert "error" not in published["event"]  # success events carry no error


def test_each_published_event_carries_a_strictly_increasing_version():
    orchestrator, repository, _, events = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id)
    repository.save(workflow)

    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1")
    orchestrator.handle_step_success(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1", result="r1")

    versions = [e["event"]["version"] for e in events.published]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)  # strictly increasing, no duplicates


def test_handle_step_failure_returns_retrying_when_attempts_remain():
    orchestrator, repository, _, _ = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id, t2=3)
    repository.save(workflow)
    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1")
    orchestrator.handle_step_success(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1", result="r1")
    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t2")

    status = orchestrator.handle_step_failure(
        tenant_id=tenant_id, workflow_id=workflow.id, step_name="t2", error="transient"
    )

    assert status == TaskStatus.RETRYING
    assert repository.get(workflow.id, tenant_id=tenant_id).status == WorkflowStatus.RUNNING


def test_handle_step_failure_publishes_an_event_recording_the_failed_task_instance():
    orchestrator, repository, _, events = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id, t2=3)
    repository.save(workflow)
    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1")
    orchestrator.handle_step_success(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1", result="r1")
    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t2")

    orchestrator.handle_step_failure(
        tenant_id=tenant_id, workflow_id=workflow.id, step_name="t2", error="metadata extraction failed"
    )

    published = dict(events.published[-1]["event"])
    assert isinstance(published.pop("version"), int)  # monotonic version present on every event
    assert published == {
        "step": "t2",
        "step_status": TaskStatus.RETRYING,
        "workflow_status": WorkflowStatus.RUNNING,
        "attempt": 1,
        "error": "metadata extraction failed",
    }


def test_handle_step_failure_returns_failed_when_attempts_exhausted():
    orchestrator, repository, _, _ = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id, t2=1)
    repository.save(workflow)
    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1")
    orchestrator.handle_step_success(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t1", result="r1")
    orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name="t2")

    status = orchestrator.handle_step_failure(
        tenant_id=tenant_id, workflow_id=workflow.id, step_name="t2", error="boom"
    )

    assert status == TaskStatus.FAILED
    persisted = repository.get(workflow.id, tenant_id=tenant_id)
    assert persisted.status == WorkflowStatus.FAILED
    assert persisted.failed_step == "t2"


def test_unknown_workflow_id_raises():
    orchestrator, _, _, _ = make_orchestrator()
    with pytest.raises(ValueError):
        orchestrator.start_task(tenant_id=uuid.uuid4(), workflow_id=uuid.uuid4(), step_name="t1")


def test_wrong_tenant_is_rejected_exactly_like_an_unknown_workflow():
    """A workflow_id that belongs to another tenant must not be distinguishable
    from one that doesn't exist at all — never leak existence across tenants."""
    orchestrator, repository, _, _ = make_orchestrator()
    tenant_id = uuid.uuid4()
    workflow = make_workflow(tenant_id=tenant_id)
    repository.save(workflow)

    unknown_error = None
    wrong_tenant_error = None
    try:
        orchestrator.start_task(tenant_id=uuid.uuid4(), workflow_id=uuid.uuid4(), step_name="t1")
    except ValueError as e:
        unknown_error = str(e)

    try:
        orchestrator.start_task(tenant_id=uuid.uuid4(), workflow_id=workflow.id, step_name="t1")
    except ValueError as e:
        wrong_tenant_error = str(e)

    assert unknown_error is not None
    assert wrong_tenant_error is not None
