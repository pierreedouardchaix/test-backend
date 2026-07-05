import uuid
from typing import Any, Protocol

from src import agents
from src.ports.task_instance_runner import Deferred


class StepHandler(Protocol):
    def __call__(self, *, document_id: uuid.UUID, tenant_id: uuid.UUID, inputs: dict[str, Any]) -> Any: ...


class InMemoryTaskInstanceRunner:
    """Wraps the mock pipeline functions unchanged — signatures, sleeps, and
    failure rates are exactly as given. This is the one place that knows how
    to translate a step name and its dependencies' outputs into the specific
    positional arguments each function expects; the domain never does.

    external_call calls agents.external_call() (the simulated outgoing HTTP
    call) and returns a Deferred carrying the partner's job id: the task stays
    RUNNING until the signed partner webhook (correlated by that job id) reports
    its outcome via WorkflowOrchestrator."""

    def __init__(self) -> None:
        self._handlers: dict[str, StepHandler] = {
            "ocr": self._ocr,
            "metadata": self._metadata,
            "chunking": self._chunking,
            "external_call": self._external_call,
        }

    def run_step(
        self,
        step_name: str,
        *,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        inputs: dict[str, Any],
    ) -> Any | Deferred:
        handler = self._handlers.get(step_name)
        if handler is None:
            raise ValueError(f"No handler registered for step {step_name!r}")
        return handler(document_id=document_id, tenant_id=tenant_id, inputs=inputs)

    def _ocr(self, *, document_id, tenant_id, inputs) -> Any:
        return agents.ocr()

    def _metadata(self, *, document_id, tenant_id, inputs) -> Any:
        return agents.metadata(inputs["ocr"])

    def _chunking(self, *, document_id, tenant_id, inputs) -> Any:
        return agents.chunking(inputs["ocr"])

    def _external_call(self, *, document_id, tenant_id, inputs) -> Deferred:
        # Simulated outgoing call — may raise ConnectionError (retried like any step failure).
        # Returns the partner's opaque job id, which we carry on the Deferred so
        # the incoming webhook (keyed by that id) is correlated back to this task.
        partner_job_id = agents.external_call(
            doc_id=str(document_id),
            ocr_text=inputs["ocr"],
            meta=inputs["metadata"],
            chunks=inputs["chunking"],
        )
        return Deferred(partner_job_id)
