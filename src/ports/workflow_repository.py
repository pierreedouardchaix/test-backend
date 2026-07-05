from typing import Protocol
import uuid

from src.domain.models.workflow import Workflow


class WorkflowRepository(Protocol):
    def get(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Workflow | None: ...

    def save(self, workflow: Workflow) -> None: ...
