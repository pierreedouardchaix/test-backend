from dataclasses import dataclass, field
from typing import Any

from src.application.unit_of_work import UnitOfWork
from src.application.workflow_orchestrator import WorkflowOrchestrator
from src.application.write_use_case import WriteUseCase
from src.domain.models.task import TaskStatus
from src.domain.models.workflow import WorkflowStatus
from src.ports.blob_store import BlobStore
from src.ports.event_publisher import EventPublisher

_TERMINAL_TASK_STATUSES = (TaskStatus.SUCCEEDED, TaskStatus.FAILED)


class WorkflowNotFound(Exception):
    """No task matches the callback's partner_job_id → the endpoint answers 404."""


class CallbackPremature(Exception):
    """The callback's target task exists but isn't RUNNING yet (e.g. our own
    attempt is still RETRYING after a transient failure on our side, while the
    partner has already processed the job). Transient — the caller should retry
    the callback later, once our side settles into RUNNING."""


@dataclass(frozen=True)
class PartnerCallbackCommand:
    partner_job_id: str  # the partner's opaque job id (j_<hex>), the correlation key
    step_name: str  # which step the callback completes — injected by the endpoint
    succeeded: bool
    result: Any | None = None  # the partner's payload, when succeeded
    error: str | None = None  # failure reason, when failed


@dataclass(frozen=True)
class PartnerCallbackResult:
    workflow_status: str
    already_processed: bool
    newly_ready: frozenset[str] = field(default_factory=frozenset)  # steps unblocked, for the caller to dispatch


class ApplyPartnerCallbackUseCase(WriteUseCase[PartnerCallbackCommand, PartnerCallbackResult]):
    """Applies a partner webhook to the step named in the command, through the
    same WorkflowOrchestrator every other trigger funnels through.

    Idempotent at the **task** level: the partner retries on non-2xx, so a
    callback landing after the step's task is already terminal is a silent
    no-op. The task-level check (not just workflow-terminal) is deliberate — it
    survives a DAG where the deferred step is no longer the last node. Correlation
    is by the partner's own job id (unique across tasks); the tenant and our
    workflow id are resolved from the stored workflow, never from the request body.
    """

    def __init__(self, uow: UnitOfWork, blob_store: BlobStore, event_publisher: EventPublisher) -> None:
        super().__init__(uow)
        self._blob_store = blob_store
        self._events = event_publisher

    def _execute(self, command: PartnerCallbackCommand) -> PartnerCallbackResult:
        workflow = self._uow.workflows.get_by_partner_job_id(command.partner_job_id)
        if workflow is None:
            raise WorkflowNotFound(command.partner_job_id)

        task = workflow.tasks.get(command.step_name)
        # Task-level idempotence: the step already reached a terminal outcome →
        # this callback (a partner retry, or a duplicate delivery) is a no-op.
        if task is not None and task.status in _TERMINAL_TASK_STATUSES:
            return PartnerCallbackResult(workflow_status=workflow.status.value, already_processed=True)
        # Workflow already terminal for another reason (e.g. a sibling step failed)
        # → nothing to apply. Belt-and-suspenders alongside the task check above.
        if workflow.status != WorkflowStatus.RUNNING:
            return PartnerCallbackResult(workflow_status=workflow.status.value, already_processed=True)
        # Task missing or not yet RUNNING (e.g. still RETRYING on our side) → the
        # callback got here before we're ready; ask the caller to retry.
        if task is None or task.status != TaskStatus.RUNNING:
            raise CallbackPremature(command.partner_job_id)

        orchestrator = WorkflowOrchestrator(self._uow.workflows, self._blob_store, self._events)
        if command.succeeded:
            newly_ready = orchestrator.handle_step_success(
                tenant_id=workflow.tenant_id,
                workflow_id=workflow.id,
                step_name=command.step_name,
                result=command.result,
            )
            # Status deducible from the path, no refetch: more steps unblocked →
            # still running; none → this completed the DAG → succeeded.
            status = WorkflowStatus.RUNNING if newly_ready else WorkflowStatus.SUCCEEDED
            return PartnerCallbackResult(
                workflow_status=status.value, already_processed=False, newly_ready=newly_ready
            )

        orchestrator.handle_terminal_failure(
            tenant_id=workflow.tenant_id,
            workflow_id=workflow.id,
            step_name=command.step_name,
            error=command.error or "partner reported failure",
        )
        return PartnerCallbackResult(workflow_status=WorkflowStatus.FAILED.value, already_processed=False)
