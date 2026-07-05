import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Self

from src.domain.models.task import Task
from src.domain.models.workflow_definition import WorkflowDefinition


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Workflow:
    """A running instance of a WorkflowDefinition.

    Owns the only status that matters for a document's processing, and
    accumulates each step's result as it completes so downstream steps
    (and the results endpoint) can read them.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    definition: WorkflowDefinition
    status: WorkflowStatus = WorkflowStatus.RUNNING
    results: dict[str, Any] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    failed_step: str | None = None
    failure_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, *, id: uuid.UUID, tenant_id: uuid.UUID, definition: WorkflowDefinition) -> Self:
        """`id` is supplied by the caller — for the 1:1 Document/Workflow
        pairing, it's the Document's own id (see dev_considerations.md)."""
        return cls(id=id, tenant_id=tenant_id, definition=definition)

    def completed_steps(self) -> frozenset[str]:
        return frozenset(self.results)

    def ready_steps(self) -> frozenset[str]:
        """Steps whose dependencies are met and that have never been
        dispatched. A step that already has a Task (RUNNING/RETRYING/
        already terminal) is excluded even though it isn't in
        completed_steps() yet — otherwise, in a fan-in, one sibling
        finishing would re-list an already-dispatched-but-not-yet-finished
        sibling as "ready" and cause a duplicate dispatch. A step's own
        retries are re-scheduled explicitly by the caller (see
        PipelineStepExecutor), never rediscovered through this method."""
        if self.status != WorkflowStatus.RUNNING:
            return frozenset()
        return self.definition.ready_steps(self.completed_steps()) - frozenset(self.tasks)

    def record_step_result(self, step_name: str, result: Any) -> None:
        if self.status != WorkflowStatus.RUNNING:
            raise ValueError(f"Cannot record a step result on a workflow that is {self.status}")
        if step_name not in self.definition.step_names():
            raise ValueError(
                f"Unknown step {step_name!r} for workflow definition {self.definition.name!r}"
            )

        self.results[step_name] = result
        if self.completed_steps() == self.definition.step_names():
            self.status = WorkflowStatus.SUCCEEDED

    def mark_step_failed(self, step_name: str, reason: str) -> None:
        if self.status != WorkflowStatus.RUNNING:
            raise ValueError(f"Cannot fail a workflow that is already {self.status}")
        self.status = WorkflowStatus.FAILED
        self.failed_step = step_name
        self.failure_reason = reason

    def start_task(self, step_name: str) -> Task:
        """Get the Task for a step ready to run — create it on first start,
        reuse it across retries — and mark it started. Call right before
        actually running the step's function."""
        if self.status != WorkflowStatus.RUNNING:
            raise ValueError(f"Cannot dispatch a task on a workflow that is {self.status}")
        if step_name not in self.definition.step_names():
            raise ValueError(
                f"Unknown step {step_name!r} for workflow definition {self.definition.name!r}"
            )

        task = self.tasks.get(step_name)
        if task is None:
            step = self.definition.get_step(step_name)
            task = Task.create(workflow_id=self.id, step_name=step_name, max_attempts=step.max_attempts)
            self.tasks[step_name] = task

        task.start()
        return task

    def on_task_succeeded(self, step_name: str, result: Any) -> frozenset[str]:
        """Apply a task's successful outcome. Returns the step names newly
        unblocked (fan-out/fan-in resolved here)."""
        if self.status != WorkflowStatus.RUNNING:
            raise ValueError(f"Cannot apply a task outcome on a workflow that is {self.status}")
        self._require_task(step_name).succeed()
        self.record_step_result(step_name, result)
        return self.ready_steps()

    def on_task_failed(self, step_name: str, error: str) -> None:
        """Apply a task's failed outcome. If attempts remain, the task moves
        to RETRYING and the workflow is left untouched — the caller
        reschedules the same task. Only a terminal failure (attempts
        exhausted) reaches the workflow."""
        if self.status != WorkflowStatus.RUNNING:
            raise ValueError(f"Cannot apply a task outcome on a workflow that is {self.status}")
        can_retry = self._require_task(step_name).fail(error)
        if not can_retry:
            self.mark_step_failed(step_name, error)

    def mark_task_deferred(self, step_name: str, partner_job_id: str) -> None:
        """Attach the external correlation id to a step that has just handed off
        and is awaiting its callback. The task stays RUNNING; the workflow is
        unchanged — this only records how to correlate the incoming webhook."""
        if self.status != WorkflowStatus.RUNNING:
            raise ValueError(f"Cannot defer a task on a workflow that is {self.status}")
        self._require_task(step_name).mark_deferred(partner_job_id)

    def on_callback_failed(self, step_name: str, error: str) -> None:
        """Apply a terminal failure reported by an external callback (the
        partner webhook). Unlike on_task_failed this never retries — the
        external authority has already decided the outcome is final."""
        if self.status != WorkflowStatus.RUNNING:
            raise ValueError(f"Cannot apply a task outcome on a workflow that is {self.status}")
        self._require_task(step_name).fail_terminally(error)
        self.mark_step_failed(step_name, error)

    def _require_task(self, step_name: str) -> Task:
        task = self.tasks.get(step_name)
        if task is None:
            raise ValueError(f"No task has been dispatched yet for step {step_name!r}")
        return task
