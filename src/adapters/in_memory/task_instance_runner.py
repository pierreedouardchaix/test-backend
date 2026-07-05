import uuid
from typing import Any, Callable

from src import agents


class InMemoryTaskInstanceRunner:
    """Wraps the mock pipeline functions unchanged — signatures, sleeps, and
    failure rates are exactly as given. This is the one place that knows how
    to translate a step name and its dependencies' outputs into the specific
    positional arguments each function expects; the domain never does."""

    _HANDLERS: dict[str, Callable[[uuid.UUID, dict[str, Any]], Any]] = {
        "ocr": lambda document_id, inputs: agents.ocr(),
        "metadata": lambda document_id, inputs: agents.metadata(inputs["ocr"]),
        "chunking": lambda document_id, inputs: agents.chunking(inputs["ocr"]),
        "external_call": lambda document_id, inputs: agents.external_call(
            doc_id=str(document_id),
            ocr_text=inputs["ocr"],
            meta=inputs["metadata"],
            chunks=inputs["chunking"],
        ),
    }

    def run_step(self, step_name: str, *, document_id: uuid.UUID, inputs: dict[str, Any]) -> Any:
        handler = self._HANDLERS.get(step_name)
        if handler is None:
            raise ValueError(f"No handler registered for step {step_name!r}")
        return handler(document_id, inputs)
