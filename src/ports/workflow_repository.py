from typing import Protocol
import uuid

from src.domain.models.workflow import Workflow


class WorkflowRepository(Protocol):
    def get(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Workflow | None: ...

    def get_by_id(self, workflow_id: uuid.UUID) -> Workflow | None:
        """Cross-tenant lookup by id alone — for ingress points that have no
        tenant context yet, where the globally-unique workflow id doubles as the
        correlation key. The caller derives the tenant from the returned
        workflow before doing anything tenant-scoped."""
        ...

    def get_by_partner_job_id(self, partner_job_id: str) -> Workflow | None:
        """Cross-tenant lookup by the partner's job id — the correlation key
        carried on the incoming webhook (which knows nothing of our ids). The
        partner job id is unique across tasks, so this resolves to at most one
        workflow. The caller derives the tenant from the returned workflow."""
        ...

    def save(self, workflow: Workflow) -> int:
        """Persist the workflow and return its new version — a per-workflow
        monotonic counter, bumped in the same transaction as the mutation. The
        orchestrator carries it on each published event so a client can order
        events and drop stale/duplicate ones (SSE has no ordering guarantee)."""
        ...
