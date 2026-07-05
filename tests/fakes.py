import json
import uuid

from src.domain.models.document import Document
from src.domain.models.tenant import Tenant
from src.domain.models.workflow import Workflow
from src.ports.document_data_source import DocumentDetailRow, DocumentRow


class FakeWorkflowRepository:
    def __init__(self):
        self.saved: dict[uuid.UUID, Workflow] = {}
        self._versions: dict[uuid.UUID, int] = {}

    def get(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Workflow | None:
        workflow = self.saved.get(workflow_id)
        if workflow is None or workflow.tenant_id != tenant_id:
            return None
        return workflow

    def get_by_id(self, workflow_id: uuid.UUID) -> Workflow | None:
        return self.saved.get(workflow_id)

    def get_by_partner_job_id(self, partner_job_id: str) -> Workflow | None:
        for workflow in self.saved.values():
            if any(task.partner_job_id == partner_job_id for task in workflow.tasks.values()):
                return workflow
        return None

    def save(self, workflow: Workflow) -> int:
        self.saved[workflow.id] = workflow
        version = self._versions.get(workflow.id, 0) + 1
        self._versions[workflow.id] = version
        return version


class FakeBlobStore:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self._next_key = 0

    def put(self, content: bytes, *, content_type: str) -> str:
        self._next_key += 1
        key = f"blob-{self._next_key}"
        self.blobs[key] = content
        return key

    def get(self, blob_key: str) -> bytes:
        return self.blobs[blob_key]

    def get_json(self, blob_key: str):
        return json.loads(self.blobs[blob_key])


class FakeEventPublisher:
    def __init__(self):
        self.published: list[dict] = []

    def publish(self, *, tenant_id, document_id, event) -> None:
        self.published.append({"tenant_id": tenant_id, "document_id": document_id, "event": event})


class FakeTenantRepository:
    def __init__(self):
        self.saved: dict[uuid.UUID, Tenant] = {}

    def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self.saved.get(tenant_id)

    def save(self, tenant: Tenant) -> None:
        self.saved[tenant.id] = tenant


class FakeDocumentRepository:
    def __init__(self):
        self.saved: dict[uuid.UUID, Document] = {}

    def get(self, document_id: uuid.UUID, *, tenant_id: uuid.UUID) -> Document | None:
        doc = self.saved.get(document_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        return doc

    def save(self, document: Document) -> None:
        self.saved[document.id] = document

    def list_by_tenant(self, tenant_id: uuid.UUID) -> list[Document]:
        return [d for d in self.saved.values() if d.tenant_id == tenant_id]


class FakeDocumentDataSource:
    def __init__(self) -> None:
        self._rows: list[DocumentRow] = []
        self._details: dict[uuid.UUID, DocumentDetailRow] = {}

    def list_by_tenant(self, tenant_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[DocumentRow]:
        rows = [r for r in self._rows if r.tenant_id == tenant_id]
        return rows[offset : offset + limit]

    def get_by_id(self, document_id: uuid.UUID, *, tenant_id: uuid.UUID) -> DocumentDetailRow | None:
        row = self._details.get(document_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    def add(self, row: DocumentRow) -> None:
        self._rows.append(row)

    def add_detail(self, row: DocumentDetailRow) -> None:
        self._details[row.document_id] = row


class FakeWorkflowDispatcher:
    def __init__(self):
        self.dispatched: list[dict] = []

    def dispatch(self, workflow_id: uuid.UUID, *, tenant_id: uuid.UUID) -> None:
        self.dispatched.append({"workflow_id": workflow_id, "tenant_id": tenant_id})


class FakePartnerCallbackDispatcher:
    def __init__(self):
        self.dispatched: list = []

    def dispatch(self, command) -> None:
        self.dispatched.append(command)


class FakeUnitOfWork:
    """No-op transaction boundary wrapping fake repositories.

    Tracks whether commit() was called so tests can assert on it.
    Context manager is a no-op — repos are ready from construction.
    """

    def __init__(self):
        self.tenants = FakeTenantRepository()
        self.documents = FakeDocumentRepository()
        self.workflows = FakeWorkflowRepository()
        self.committed = False
        self.scoped_tenant = None

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        pass

    def scope_to_tenant(self, tenant) -> None:
        self.scoped_tenant = tenant  # recorded so tests can assert on it; no RLS in-memory

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass


class FakeTaskInstanceRunner:
    """Scripted outcomes per step, consumed in order across attempts.
    Pass an Exception instance to simulate a failed attempt."""

    def __init__(self, behaviors: dict[str, list]):
        self._behaviors = {step: list(outcomes) for step, outcomes in behaviors.items()}
        self.calls: list[tuple[str, dict]] = []

    def run_step(self, step_name, *, tenant_id, document_id, inputs):
        self.calls.append((step_name, inputs))
        outcome = self._behaviors[step_name].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
