import uuid

from src.application.pipeline_step_executor import PipelineStepExecutor
from src.domain.models.workflow import WorkflowStatus
from src.ports.workflow_repository import WorkflowRepository


class SynchronousPipelineDriver:
    """Drives a Workflow to completion in the current process by repeatedly
    calling PipelineStepExecutor.execute_step() and feeding the step names it
    unblocks back into the loop — no queue, no backoff (a failed attempt is
    retried immediately; the mock functions' own sleeps provide the only
    pacing). Stands in for Celery during the in-memory phase: PipelineStepExecutor
    is exactly what a Celery task calls per step, so swapping this driver for
    real Celery tasks changes nothing about execution or persistence — only
    how the next step gets scheduled changes (`.delay()` instead of a Python
    loop).

    Processes one ready step at a time, so it never exercises the true
    concurrent fan-in race a multi-worker deployment would hit — that stays
    covered by PipelineStepExecutor's run_with_retry and, later, a Postgres
    concurrency test.
    """

    def __init__(self, executor: PipelineStepExecutor, workflow_repository: WorkflowRepository) -> None:
        self._executor = executor
        self._workflow_repository = workflow_repository

    def run(self, *, tenant_id: uuid.UUID, workflow_id: uuid.UUID) -> None:
        workflow = self._workflow_repository.get(workflow_id, tenant_id=tenant_id)
        pending = set(workflow.ready_steps())
        while pending:
            workflow = self._workflow_repository.get(workflow_id, tenant_id=tenant_id)
            if workflow.status != WorkflowStatus.RUNNING:
                break
            step_name = pending.pop()
            pending.update(
                self._executor.execute_step(tenant_id=tenant_id, workflow_id=workflow_id, step_name=step_name)
            )
