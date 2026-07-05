import uuid
from typing import Any, Protocol


class Deferred:
    """Returned by a step that hands off to an external executor and does not
    complete in-process.  The task stays RUNNING; its outcome will arrive later
    through WorkflowOrchestrator (e.g. the partner webhook).

    Callers can detect deferred steps at runtime with ``isinstance(result, Deferred)``
    without knowing anything about *which* external system was called."""


DEFERRED = Deferred()


class TaskInstanceRunner(Protocol):
    """Executes one pipeline step.

    A single attempt, synchronous from the caller's point of view: returns the
    step's result (or DEFERRED for callback steps), or raises on failure.
    Retries/backoff are the caller's concern, not this port's — the
    Celery-backed implementation will still honor this single-attempt contract
    per call.
    """

    def run_step(
        self,
        step_name: str,
        *,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> Any | Deferred:
        """Run `step_name` with the outputs of its dependencies as `inputs`
        (keyed by step name), plus the ids every step may need.

        Returns DEFERRED if the step dispatched work to an external system and
        is now waiting for a callback to report its outcome."""
