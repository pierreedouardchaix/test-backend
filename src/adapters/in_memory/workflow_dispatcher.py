import uuid


class NoOpWorkflowDispatcher:
    """Placeholder until Celery is wired (step 6)."""

    def dispatch(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> None:
        pass
