import uuid
from typing import Protocol


class WorkflowDispatcher(Protocol):
    def dispatch(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> None: ...
