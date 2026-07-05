import json
import uuid

import pytest

from src.application.apply_partner_callback import (
    ApplyPartnerCallbackUseCase,
    PartnerCallbackCommand,
    WorkflowNotFound,
)
from src.application.ingest_document import PRIMMO_DEFINITION
from src.domain.models.task import TaskStatus
from src.domain.models.workflow import Workflow, WorkflowStatus
from tests.fakes import FakeBlobStore, FakeEventPublisher, FakeUnitOfWork

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


def _workflow_awaiting_callback(tenant_id: uuid.UUID, job_id: uuid.UUID) -> Workflow:
    """A workflow whose four upstream steps are done and whose external_call
    task has been dispatched (RUNNING) — exactly the state in which the partner
    webhook is expected to arrive."""
    wf = Workflow.create(id=job_id, tenant_id=tenant_id, definition=PRIMMO_DEFINITION)
    for step in ("ocr", "metadata", "chunking", "agent"):
        wf.start_task(step)
        wf.on_task_succeeded(step, f"blob-{step}")
    wf.start_task("external_call")
    return wf


def _use_case(uow, blob=None, events=None):
    return ApplyPartnerCallbackUseCase(uow, blob or FakeBlobStore(), events or FakeEventPublisher())


def test_completed_callback_finishes_the_workflow():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(TENANT_A, job_id))
    blob = FakeBlobStore()

    result = _use_case(uow, blob=blob).execute(
        PartnerCallbackCommand(job_id=job_id, step_name="external_call", succeeded=True, result={"indexed": True})
    )

    assert result.already_processed is False
    assert result.workflow_status == WorkflowStatus.SUCCEEDED.value
    assert uow.committed is True

    wf = uow.workflows.get_by_id(job_id)
    assert wf.status == WorkflowStatus.SUCCEEDED
    assert wf.tasks["external_call"].status == TaskStatus.SUCCEEDED
    # The partner payload is stored via the blob store, only its key on results.
    stored = blob.get_json(wf.results["external_call"])
    assert stored == {"indexed": True}


def test_failed_callback_fails_the_workflow_without_retry():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(TENANT_A, job_id))

    result = _use_case(uow).execute(
        PartnerCallbackCommand(job_id=job_id, step_name="external_call", succeeded=False, error="partner boom")
    )

    assert result.workflow_status == WorkflowStatus.FAILED.value
    wf = uow.workflows.get_by_id(job_id)
    assert wf.status == WorkflowStatus.FAILED
    assert wf.failed_step == "external_call"
    assert wf.failure_reason == "partner boom"
    # Terminal on the first callback — no RETRYING despite attempts remaining.
    assert wf.tasks["external_call"].status == TaskStatus.FAILED


def test_replayed_callback_is_a_silent_noop():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(TENANT_A, job_id))
    blob = FakeBlobStore()
    events = FakeEventPublisher()

    # Each webhook delivery is a fresh use case over the same store (WriteUseCase
    # instances are single-use — the UoW is swapped out after execute()).
    _use_case(uow, blob=blob, events=events).execute(
        PartnerCallbackCommand(job_id=job_id, step_name="external_call", succeeded=True, result={"n": 1})
    )
    events_after_first = len(events.published)

    replay = _use_case(uow, blob=blob, events=events).execute(
        PartnerCallbackCommand(job_id=job_id, step_name="external_call", succeeded=True, result={"n": 2})
    )

    assert replay.already_processed is True
    assert replay.workflow_status == WorkflowStatus.SUCCEEDED.value
    # Nothing re-applied: no new event, result unchanged.
    assert len(events.published) == events_after_first


def test_unknown_job_id_raises():
    uow = FakeUnitOfWork()
    with pytest.raises(WorkflowNotFound):
        _use_case(uow).execute(PartnerCallbackCommand(job_id=uuid.uuid4(), step_name="external_call", succeeded=True, result={}))


def test_tenant_is_resolved_from_the_stored_workflow():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(TENANT_B, job_id))
    events = FakeEventPublisher()

    _use_case(uow, events=events).execute(
        PartnerCallbackCommand(job_id=job_id, step_name="external_call", succeeded=True, result={})
    )

    # The published event carries tenant B — proving the tenant came from the
    # workflow, not from any request-supplied field.
    assert events.published[-1]["tenant_id"] == TENANT_B
