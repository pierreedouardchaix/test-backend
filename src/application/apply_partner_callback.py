from dataclasses import dataclass
from typing import Any

from src.application.unit_of_work import UnitOfWork
from src.application.workflow_orchestrator import WorkflowOrchestrator
from src.application.write_use_case import WriteUseCase
from src.domain.models.workflow import WorkflowStatus
from src.ports.blob_store import BlobStore
from src.ports.event_publisher import EventPublisher


class WorkflowNotFound(Exception):
    """No task matches the callback's partner_job_id → the endpoint answers 404."""


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


class ApplyPartnerCallbackUseCase(WriteUseCase[PartnerCallbackCommand, PartnerCallbackResult]):
    """Applies a partner webhook to the step named in the command, through the
    same WorkflowOrchestrator every other trigger funnels through.

    Idempotent by design: the partner retries on non-2xx, so a callback that
    lands after the step is already terminal is a silent no-op (the endpoint
    still answers 200). Correlation is by the partner's own job id (unique
    across tasks); the tenant and our workflow id are resolved from the stored
    workflow, never trusted from the request body.
    """

    def __init__(self, uow: UnitOfWork, blob_store: BlobStore, event_publisher: EventPublisher) -> None:
        super().__init__(uow)
        self._blob_store = blob_store
        self._events = event_publisher

    def _execute(self, command: PartnerCallbackCommand) -> PartnerCallbackResult:
        workflow = self._uow.workflows.get_by_partner_job_id(command.partner_job_id)
        if workflow is None:
            raise WorkflowNotFound(command.partner_job_id)

        # Already terminal → this callback was handled (or superseded) already.
        if workflow.status != WorkflowStatus.RUNNING:
            return PartnerCallbackResult(workflow_status=workflow.status.value, already_processed=True)

        orchestrator = WorkflowOrchestrator(self._uow.workflows, self._blob_store, self._events)
        tenant_id = workflow.tenant_id
        workflow_id = workflow.id
        if command.succeeded:
            orchestrator.handle_step_success(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                step_name=command.step_name,
                result=command.result,
            )
        else:
            orchestrator.handle_terminal_failure(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                step_name=command.step_name,
                error=command.error or "partner reported failure",
            )

        updated = self._uow.workflows.get_by_id(workflow_id)
        return PartnerCallbackResult(workflow_status=updated.status.value, already_processed=False)
