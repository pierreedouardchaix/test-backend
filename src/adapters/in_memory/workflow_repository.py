import uuid

from src.domain.models.workflow import Workflow


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self._workflows: dict[uuid.UUID, Workflow] = {}
        self._versions: dict[uuid.UUID, int] = {}

    def get(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Workflow | None:
        workflow = self._workflows.get(workflow_id)
        if workflow is None or workflow.tenant_id != tenant_id:
            return None
        return workflow

    def get_by_id(self, workflow_id: uuid.UUID) -> Workflow | None:
        return self._workflows.get(workflow_id)

    def get_by_partner_job_id(self, partner_job_id: str) -> Workflow | None:
        for workflow in self._workflows.values():
            if any(task.partner_job_id == partner_job_id for task in workflow.tasks.values()):
                return workflow
        return None

    def save(self, workflow: Workflow) -> int:
        self._workflows[workflow.id] = workflow
        version = self._versions.get(workflow.id, 0) + 1
        self._versions[workflow.id] = version
        return version
