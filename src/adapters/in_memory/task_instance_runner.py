import uuid
from typing import Any

from src import agents
from src.ports.task_instance_runner import DEFERRED, Deferred


class InMemoryTaskInstanceRunner:
    """Wraps the mock pipeline functions unchanged — signatures, sleeps, and
    failure rates are exactly as given. This is the one place that knows how
    to translate a step name and its dependencies' outputs into the specific
    positional arguments each function expects; the domain never does.

    external_call calls agents.external_call() (the simulated outgoing HTTP
    call) and returns DEFERRED: the task stays RUNNING until the signed partner
    webhook reports its outcome via WorkflowOrchestrator."""

    def __init__(self) -> None:
        self._handlers = {
            "ocr": self._ocr,
            "metadata": self._metadata,
            "chunking": self._chunking,
            "agent": self._agent,
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

    def _agent(self, *, document_id, tenant_id, inputs) -> Any:
        # Placeholder for the "sortie agent" step (see dev_considerations.md,
        # step 5): a step we added ourselves, not one of the README's fixed
        # mocks (src/agents.py), so no injected sleep/failure — deterministic
        # enrichment ahead of the partner call.
        return {
            "doc_type": inputs["metadata"]["doc_type"],
            "chunk_count": len(inputs["chunking"]),
        }

    def _external_call(self, *, document_id, tenant_id, inputs) -> Deferred:
        # Simulated outgoing call — may raise ConnectionError (retried like any step failure).
        # Returns an opaque job_id we don't store; correlation is via document_id.
        agents.external_call(
            doc_id=str(document_id),
            ocr_text=inputs["ocr"],
            meta=inputs["metadata"],
            chunks=inputs["chunking"],
        )
        return DEFERRED
