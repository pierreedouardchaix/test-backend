import json
import uuid
from typing import Any, Callable

from src.application.concurrency import run_with_retry
from src.application.unit_of_work import UnitOfWork
from src.application.workflow_orchestrator import WorkflowOrchestrator
from src.domain.models.task import TaskStatus
from src.ports.blob_store import BlobStore
from src.ports.event_publisher import EventPublisher
from src.ports.task_instance_runner import Deferred, TaskInstanceRunner


class PipelineStepExecutor:
    """Executes exactly one pipeline step end-to-end — persists that it
    started, runs it, persists its outcome — and returns the step names it
    newly unblocked.

    This is the one piece that is identical whether the caller is the
    in-process SynchronousPipelineDriver (a plain loop around this) or a
    Celery task (this, then `.delay()` each returned step name): the only
    thing that differs between them is how the *next* step gets scheduled,
    never how *this* one runs or persists.

    Each of the two writes (start / outcome) opens its own fresh UnitOfWork
    via `uow_factory` and is wrapped in `run_with_retry`: a concurrent writer
    racing on the same Workflow row — the classic fan-in, e.g. metadata and
    chunking finishing at the same time on two different workers — loses the
    optimistic-lock race, and `run_with_retry` reloads fresh state and
    retries. That reload is what makes exactly one of the two branches see
    both results and dispatch the step they fan into.
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        task_instance_runner: TaskInstanceRunner,
        blob_store: BlobStore,
        event_publisher: EventPublisher,
    ) -> None:
        self._uow_factory = uow_factory
        self._task_instance_runner = task_instance_runner
        self._blob_store = blob_store
        self._events = event_publisher

    def execute_step(self, *, tenant_id: uuid.UUID, workflow_id: uuid.UUID, step_name: str) -> frozenset[str]:
        input_blob_keys = run_with_retry(
            lambda: self._start(tenant_id=tenant_id, workflow_id=workflow_id, step_name=step_name)
        )
        inputs = self._resolve_inputs(input_blob_keys)

        try:
            result = self._task_instance_runner.run_step(
                step_name, tenant_id=tenant_id, document_id=workflow_id, inputs=inputs
            )
        except Exception as exc:
            status = run_with_retry(
                lambda: self._fail(tenant_id=tenant_id, workflow_id=workflow_id, step_name=step_name, error=str(exc))
            )
            return frozenset({step_name}) if status == TaskStatus.RETRYING else frozenset()

        if isinstance(result, Deferred):
            # Handed off to an external executor — step stays RUNNING until the
            # partner webhook reports its outcome. A third write records the
            # partner's correlation id so that webhook can be matched back here.
            run_with_retry(
                lambda: self._defer(
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    step_name=step_name,
                    partner_job_id=result.partner_job_id,
                )
            )
            return frozenset()
        return run_with_retry(
            lambda: self._succeed(tenant_id=tenant_id, workflow_id=workflow_id, step_name=step_name, result=result)
        )

    def _start(self, *, tenant_id: uuid.UUID, workflow_id: uuid.UUID, step_name: str) -> dict[str, str]:
        """Mark the step started (committed before it runs) and return the blob
        key of each dependency's output — `{dep: blob_key}` — for _resolve_inputs
        to materialize."""
        with self._uow_factory() as uow:
            orchestrator = WorkflowOrchestrator(uow.workflows, self._blob_store, self._events)
            orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow_id, step_name=step_name)
            workflow = uow.workflows.get(workflow_id, tenant_id=tenant_id)
            uow.commit()
            step = workflow.get_step(step_name)
            return {dep: workflow.results[dep] for dep in step.depends_on}

    def _defer(self, *, tenant_id: uuid.UUID, workflow_id: uuid.UUID, step_name: str, partner_job_id: str) -> None:
        with self._uow_factory() as uow:
            orchestrator = WorkflowOrchestrator(uow.workflows, self._blob_store, self._events)
            orchestrator.handle_step_deferred(
                tenant_id=tenant_id, workflow_id=workflow_id, step_name=step_name, partner_job_id=partner_job_id
            )
            uow.commit()

    def _fail(self, *, tenant_id: uuid.UUID, workflow_id: uuid.UUID, step_name: str, error: str) -> TaskStatus:
        with self._uow_factory() as uow:
            orchestrator = WorkflowOrchestrator(uow.workflows, self._blob_store, self._events)
            status = orchestrator.handle_step_failure(
                tenant_id=tenant_id, workflow_id=workflow_id, step_name=step_name, error=error
            )
            uow.commit()
            return status

    def _succeed(self, *, tenant_id: uuid.UUID, workflow_id: uuid.UUID, step_name: str, result: Any) -> frozenset[str]:
        with self._uow_factory() as uow:
            orchestrator = WorkflowOrchestrator(uow.workflows, self._blob_store, self._events)
            ready_steps = orchestrator.handle_step_success(
                tenant_id=tenant_id, workflow_id=workflow_id, step_name=step_name, result=result
            )
            uow.commit()
            return ready_steps

    def _resolve_inputs(self, input_blob_keys: dict[str, str]) -> dict[str, Any]:
        """Dependency outputs are blob keys — materialize them into real values
        before handing them to the TaskInstanceRunner."""
        return {dep: json.loads(self._blob_store.get(key)) for dep, key in input_blob_keys.items()}
