import json
import uuid
from typing import Any

from src.domain.models.task import TaskStatus
from src.domain.models.workflow import Workflow, WorkflowStatus
from src.application.workflow_orchestrator import WorkflowOrchestrator
from src.ports.blob_store import BlobStore
from src.ports.task_instance_runner import Deferred, TaskInstanceRunner


class SynchronousPipelineDriver:
    """Drives a Workflow to completion in the current process — no queue, no
    backoff on retry (a failed attempt is retried immediately; the mock
    functions' own sleeps provide the only pacing). Stands in for Celery
    during this in-memory phase: every trigger it needs already goes through
    WorkflowOrchestrator (start_task / handle_step_success /
    handle_step_failure), so swapping this driver for real Celery tasks later
    changes nothing about that orchestration logic — only how steps get
    enqueued changes.

    Processes one ready step at a time, so it never exercises the true
    concurrent fan-in race a multi-worker deployment would hit — that stays
    covered by Workflow's own guards and, later, a Postgres concurrency test.
    """

    def __init__(
        self,
        orchestrator: WorkflowOrchestrator,
        task_instance_runner: TaskInstanceRunner,
        blob_store: BlobStore,
    ) -> None:
        self._orchestrator = orchestrator
        self._task_instance_runner = task_instance_runner
        self._blob_store = blob_store

    def run(self, *, tenant_id: uuid.UUID, workflow: Workflow) -> None:
        pending = set(workflow.ready_steps())
        while pending and workflow.status == WorkflowStatus.RUNNING:
            step_name = pending.pop()
            pending.update(self._run_step(tenant_id=tenant_id, workflow=workflow, step_name=step_name))

    def _run_step(self, *, tenant_id: uuid.UUID, workflow: Workflow, step_name: str) -> frozenset[str]:
        self._orchestrator.start_task(tenant_id=tenant_id, workflow_id=workflow.id, step_name=step_name)

        step = workflow.definition.get_step(step_name)
        inputs = self._resolve_inputs(workflow, step.depends_on)

        try:
            result = self._task_instance_runner.run_step(
                step_name, tenant_id=tenant_id, document_id=workflow.id, inputs=inputs
            )
        except Exception as exc:
            status = self._orchestrator.handle_step_failure(
                tenant_id=tenant_id, workflow_id=workflow.id, step_name=step_name, error=str(exc)
            )
            return frozenset({step_name}) if status == TaskStatus.RETRYING else frozenset()

        if isinstance(result, Deferred):
            # Handed off to an external executor — step stays RUNNING until the
            # partner webhook reports its outcome via WorkflowOrchestrator.
            return frozenset()
        return self._orchestrator.handle_step_success(
            tenant_id=tenant_id, workflow_id=workflow.id, step_name=step_name, result=result
        )

    def _resolve_inputs(self, workflow: Workflow, depends_on: frozenset[str]) -> dict[str, Any]:
        """Dependency outputs are blob keys on Workflow.results — materialize
        them into real values before handing them to the TaskInstanceRunner."""
        return {dep: json.loads(self._blob_store.get(workflow.results[dep])) for dep in depends_on}
