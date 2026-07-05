import json
import uuid

import pytest

from src.application.apply_partner_callback import (
    ApplyPartnerCallbackUseCase,
    CallbackPremature,
    PartnerCallbackCommand,
    WorkflowNotFound,
)
from src.application.ingest_document import PRIMMO_DEFINITION
from src.domain.models.task import TaskStatus
from src.domain.models.workflow import Workflow, WorkflowStatus
from tests.fakes import FakeBlobStore, FakeEventPublisher, FakeUnitOfWork

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
PARTNER_JOB_ID = "j_abc123def4567890"


def _workflow_awaiting_callback(
    tenant_id: uuid.UUID, workflow_id: uuid.UUID, partner_job_id: str = PARTNER_JOB_ID
) -> Workflow:
    """A workflow whose three upstream steps are done and whose external_call
    task has been dispatched (RUNNING) and deferred (carrying the partner job
    id) — exactly the state in which the partner webhook is expected to arrive."""
    wf = Workflow.create(id=workflow_id, tenant_id=tenant_id, definition=PRIMMO_DEFINITION)
    for step in ("ocr", "metadata", "chunking"):
        wf.start_task(step)
        wf.on_task_succeeded(step, f"blob-{step}")
    wf.start_task("external_call")
    wf.mark_task_deferred("external_call", partner_job_id)
    return wf


def _use_case(uow, blob=None, events=None):
    return ApplyPartnerCallbackUseCase(uow, blob or FakeBlobStore(), events or FakeEventPublisher())


def test_completed_callback_finishes_the_workflow():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(TENANT_A, job_id))
    blob = FakeBlobStore()

    result = _use_case(uow, blob=blob).execute(
        PartnerCallbackCommand(partner_job_id=PARTNER_JOB_ID, step_name="external_call", succeeded=True, result={"indexed": True})
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
        PartnerCallbackCommand(partner_job_id=PARTNER_JOB_ID, step_name="external_call", succeeded=False, error="partner boom")
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
        PartnerCallbackCommand(partner_job_id=PARTNER_JOB_ID, step_name="external_call", succeeded=True, result={"n": 1})
    )
    events_after_first = len(events.published)

    replay = _use_case(uow, blob=blob, events=events).execute(
        PartnerCallbackCommand(partner_job_id=PARTNER_JOB_ID, step_name="external_call", succeeded=True, result={"n": 2})
    )

    assert replay.already_processed is True
    assert replay.workflow_status == WorkflowStatus.SUCCEEDED.value
    # Nothing re-applied: no new event, result unchanged.
    assert len(events.published) == events_after_first


def test_completed_callback_on_the_terminal_step_unblocks_nothing():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(TENANT_A, job_id))

    result = _use_case(uow).execute(
        PartnerCallbackCommand(partner_job_id=PARTNER_JOB_ID, step_name="external_call", succeeded=True, result={})
    )

    # external_call is the last node — its success completes the DAG, nothing new is ready.
    assert result.newly_ready == frozenset()
    assert result.workflow_status == WorkflowStatus.SUCCEEDED.value


def test_callback_on_a_retrying_task_is_premature():
    """The partner processed the job, but our own external_call attempt failed
    transiently and is RETRYING (not RUNNING). The callback arrived too early to
    apply — the use case signals the caller to retry."""
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    workflow = _workflow_awaiting_callback(TENANT_A, job_id)
    workflow.on_task_failed("external_call", "our transient timeout")  # RUNNING -> RETRYING
    uow.workflows.save(workflow)

    with pytest.raises(CallbackPremature):
        _use_case(uow).execute(
            PartnerCallbackCommand(partner_job_id=PARTNER_JOB_ID, step_name="external_call", succeeded=True, result={})
        )


def test_unknown_job_id_raises():
    uow = FakeUnitOfWork()
    with pytest.raises(WorkflowNotFound):
        _use_case(uow).execute(PartnerCallbackCommand(partner_job_id="j_does_not_exist", step_name="external_call", succeeded=True, result={}))


def test_tenant_is_resolved_from_the_stored_workflow():
    job_id = uuid.uuid4()
    uow = FakeUnitOfWork()
    uow.workflows.save(_workflow_awaiting_callback(TENANT_B, job_id))
    events = FakeEventPublisher()

    _use_case(uow, events=events).execute(
        PartnerCallbackCommand(partner_job_id=PARTNER_JOB_ID, step_name="external_call", succeeded=True, result={})
    )

    # The published event carries tenant B — proving the tenant came from the
    # workflow, not from any request-supplied field.
    assert events.published[-1]["tenant_id"] == TENANT_B
