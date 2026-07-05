import uuid
from dataclasses import dataclass

from src.application.unit_of_work import UnitOfWork
from src.application.write_use_case import WriteUseCase
from src.domain.models.document import Document
from src.domain.models.workflow import Workflow
from src.domain.models.workflow_definition import StepDefinition, WorkflowDefinition
from src.logging_config import get_logger
from src.ports.blob_store import BlobStore
from src.ports.workflow_dispatcher import WorkflowDispatcher

_log = get_logger("ingest")

PRIMMO_DEFINITION = WorkflowDefinition(
    name="primmo_ingestion",
    steps=(
        StepDefinition(name="ocr"),
        StepDefinition(name="metadata", depends_on=frozenset({"ocr"})),
        StepDefinition(name="chunking", depends_on=frozenset({"ocr"})),
        StepDefinition(
            name="external_call",
            depends_on=frozenset({"ocr", "metadata", "chunking"}),
        ),
    ),
)


@dataclass(frozen=True)
class IngestDocumentCommand:
    tenant_id: uuid.UUID
    uploaded_by: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    file_content: bytes


@dataclass(frozen=True)
class IngestDocumentResult:
    document_id: uuid.UUID
    workflow_status: str


class IngestDocumentUseCase(WriteUseCase[IngestDocumentCommand, IngestDocumentResult]):
    def __init__(self, uow: UnitOfWork, blob_store: BlobStore, dispatcher: WorkflowDispatcher) -> None:
        super().__init__(uow)
        self._blob_store = blob_store
        self._dispatcher = dispatcher

    def _post_execution(self, command: IngestDocumentCommand, result: IngestDocumentResult) -> None:
        self._dispatcher.dispatch(result.document_id, tenant_id=command.tenant_id)

    def _execute(self, command: IngestDocumentCommand) -> IngestDocumentResult:
        # RLS scoping to command.tenant_id is done by WriteUseCase (single-tenant base).
        blob_key = self._blob_store.put(
            command.file_content, content_type=command.content_type
        )
        doc = Document.create(
            tenant_id=command.tenant_id,
            uploaded_by=command.uploaded_by,
            filename=command.filename,
            content_type=command.content_type,
            size_bytes=command.size_bytes,
            blob_key=blob_key,
        )
        workflow = Workflow.create(
            id=doc.id,
            tenant_id=command.tenant_id,
            definition=PRIMMO_DEFINITION,
        )
        self._uow.documents.save(doc)
        self._uow.workflows.save(workflow)
        # NB: avoid keys that collide with reserved LogRecord attributes (e.g.
        # `filename`, `module`, `name`) — the stdlib logging raises on those.
        _log.info(
            "document ingested",
            extra={"doc_id": str(doc.id), "tenant_id": str(command.tenant_id), "document_name": command.filename},
        )
        return IngestDocumentResult(
            document_id=doc.id,
            workflow_status=workflow.status.value,
        )
