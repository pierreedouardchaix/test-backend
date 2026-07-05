import json
import uuid
from typing import Any

from src.domain.models.task import TaskStatus
from src.ports.blob_store import BlobStore
from src.ports.event_publisher import EventPublisher
from src.ports.workflow_repository import WorkflowRepository


class WorkflowOrchestrator:
    """The single place that fetches a Workflow, applies a step's outcome,
    and persists the result.

    Every trigger — the upload background task, a Celery step task, the
    partner webhook — funnels through this, so no adapter has to duplicate
    "load, apply, save, publish". It does not dispatch newly-ready steps
    itself: whether that means calling a TaskInstanceRunner synchronously or enqueuing
    a Celery task is an execution-strategy decision that belongs to the
    caller, not to this orchestration logic.

    Tasks are not persisted separately — they live on Workflow.tasks, so
    saving the workflow saves them too, in one write.

    A step's raw result can be sizeable in practice (OCR text, chunks...), so
    it never gets embedded in Workflow.results directly: it's JSON-serialized
    and written to the BlobStore, and only the resulting blob key — small,
    cheap to carry through Postgres/Celery/SSE — is recorded on the Workflow.
    Reading a document's results back means resolving each blob key through
    the BlobStore, not reading Workflow.results verbatim.
    """

    def __init__(
        self,
        workflow_repository: WorkflowRepository,
        blob_store: BlobStore,
        event_publisher: EventPublisher,
    ) -> None:
        self._workflow_repository = workflow_repository
        self._blob_store = blob_store
        self._events = event_publisher

    def start_task(self, *, tenant_id: uuid.UUID, workflow_id: uuid.UUID, step_name: str):
        """Get a step's Task ready to run (create/reuse + mark started) and
        persist that before the caller actually executes the step — so a
        crash right after this is never lost: the Task's RUNNING state and
        attempt count are safely on record before any (possibly slow)
        execution starts. Does not run the step itself."""
        workflow = self._require_workflow(workflow_id, tenant_id=tenant_id)

        task = workflow.start_task(step_name)

        version = self._workflow_repository.save(workflow)
        self._publish(tenant_id=tenant_id, workflow=workflow, step_name=step_name, version=version)
        return task

    def handle_step_success(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        step_name: str,
        result: Any,
    ) -> frozenset[str]:
        """Returns the step names newly unblocked by this success."""
        workflow = self._require_workflow(workflow_id, tenant_id=tenant_id)

        blob_key = self._blob_store.put(json.dumps(result).encode(), content_type="application/json")
        ready_steps = workflow.on_task_succeeded(step_name, blob_key)

        version = self._workflow_repository.save(workflow)
        self._publish(tenant_id=tenant_id, workflow=workflow, step_name=step_name, version=version)
        return ready_steps

    def handle_step_failure(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        step_name: str,
        error: str,
    ) -> TaskStatus:
        """Returns the task's status after applying the failure: RETRYING
        means the caller should reschedule the same task, FAILED means the
        workflow just became terminal."""
        workflow = self._require_workflow(workflow_id, tenant_id=tenant_id)

        workflow.on_task_failed(step_name, error)

        version = self._workflow_repository.save(workflow)
        self._publish(tenant_id=tenant_id, workflow=workflow, step_name=step_name, version=version)
        return workflow.tasks[step_name].status

    def handle_step_deferred(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        step_name: str,
        partner_job_id: str,
    ) -> None:
        """Record that a step handed off to an external system and is awaiting
        its callback, keyed by `partner_job_id`. No event is published: the task
        stays RUNNING, nothing observable changes for the client — only the
        correlation id is persisted so the incoming webhook can be matched."""
        workflow = self._require_workflow(workflow_id, tenant_id=tenant_id)
        workflow.mark_task_deferred(step_name, partner_job_id)
        self._workflow_repository.save(workflow)

    def handle_terminal_failure(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        step_name: str,
        error: str,
    ) -> None:
        """Apply a failure that is final by decree of an external authority
        (the partner webhook) — no retry, the workflow becomes terminal."""
        workflow = self._require_workflow(workflow_id, tenant_id=tenant_id)

        workflow.on_callback_failed(step_name, error)

        version = self._workflow_repository.save(workflow)
        self._publish(tenant_id=tenant_id, workflow=workflow, step_name=step_name, version=version)

    def _require_workflow(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID):
        workflow = self._workflow_repository.get(workflow_id, tenant_id=tenant_id)
        if workflow is None:
            raise ValueError(f"Unknown workflow {workflow_id} for tenant {tenant_id}")
        return workflow

    def _publish(self, *, tenant_id: uuid.UUID, workflow, step_name: str, version: int) -> None:
        task = workflow.tasks[step_name]
        event = {
            "step": step_name,
            "step_status": task.status,
            "workflow_status": workflow.status,
            "attempt": task.attempts,
            # Per-workflow monotonic version (bumped in the same tx as the mutation):
            # lets a client order events and drop stale/duplicate ones.
            "version": version,
        }
        # A retrying/failed status is the outcome of a task instance that just
        # failed — carry its error so the failed attempt is recorded, not just
        # the fact that the task is now waiting to retry.
        if task.status in (TaskStatus.RETRYING, TaskStatus.FAILED):
            event["error"] = task.last_error
        self._events.publish(tenant_id=tenant_id, document_id=workflow.id, event=event)
