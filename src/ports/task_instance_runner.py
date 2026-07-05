import uuid
from typing import Any, Protocol


class TaskInstanceRunner(Protocol):
    """Executes one pipeline step.

    A single attempt, synchronous from the caller's point of view: returns the
    step's result, or raises on failure. Retries/backoff are the caller's
    concern, not this port's — the Celery-backed implementation will still
    honor this single-attempt contract per call.
    """

    def run_step(self, step_name: str, *, document_id: uuid.UUID, inputs: dict[str, Any]) -> Any:
        """Run `step_name` with the outputs of its dependencies as `inputs`
        (keyed by step name), plus the document_id every step may need."""
