from typing import Protocol
import uuid

from src.domain.models.workflow import Workflow


class WorkflowRepository(Protocol):
    def get(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Workflow | None: ...

    def get_by_id(self, workflow_id: uuid.UUID) -> Workflow | None:
        """Cross-tenant lookup by id alone — for ingress points that have no
        tenant context yet (the partner webhook), where the globally-unique
        workflow id doubles as the correlation key. The caller derives the
        tenant from the returned workflow before doing anything tenant-scoped."""
        ...

    def save(self, workflow: Workflow) -> None: ...
