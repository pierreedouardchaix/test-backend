import uuid

from src.domain.models.workflow import Workflow


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self._workflows: dict[uuid.UUID, Workflow] = {}

    def get(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Workflow | None:
        workflow = self._workflows.get(workflow_id)
        if workflow is None or workflow.tenant_id != tenant_id:
            return None
        return workflow

    def get_by_id(self, workflow_id: uuid.UUID) -> Workflow | None:
        return self._workflows.get(workflow_id)

    def save(self, workflow: Workflow) -> None:
        self._workflows[workflow.id] = workflow
