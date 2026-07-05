import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sse_starlette.sse import EventSourceResponse

from src.application.get_document import DocumentNotFound, GetDocumentQuery, GetDocumentUseCase
from src.application.ingest_document import IngestDocumentCommand, IngestDocumentUseCase
from src.application.list_documents import ListDocumentsQuery, ListDocumentsUseCase
from src.application.unit_of_work import UnitOfWork
from src.auth import AuthContext, get_current_user, get_current_user_from_query_token
from src.dependencies import (
    DocumentReader,
    get_blob_store,
    get_document_data_source,
    get_document_reader,
    get_event_stream,
    get_uow,
    get_workflow_dispatcher,
)
from src.ports.blob_store import BlobStore
from src.ports.document_data_source import DocumentDataSource
from src.ports.event_stream import EventStream
from src.ports.workflow_dispatcher import WorkflowDispatcher
from src.routers.schemas import DocumentDetailResponse, DocumentResultsResponse, DocumentSummaryResponse

router = APIRouter(prefix="/documents", tags=["documents"])

_TERMINAL_STATUSES = ("succeeded", "failed")


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile,
    auth: AuthContext = Depends(get_current_user),
    uow: UnitOfWork = Depends(get_uow),
    blob_store: BlobStore = Depends(get_blob_store),
    dispatcher: WorkflowDispatcher = Depends(get_workflow_dispatcher),
):
    content = await file.read()
    result = IngestDocumentUseCase(uow, blob_store, dispatcher).execute(
        IngestDocumentCommand(
            tenant_id=auth.tenant_id,
            uploaded_by=auth.user.id,
            filename=file.filename or "upload",
            content_type=file.content_type or "application/octet-stream",
            size_bytes=len(content),
            file_content=content,
        )
    )
    return {"document_id": str(result.document_id), "workflow_status": result.workflow_status}


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    data_source: DocumentDataSource = Depends(get_document_data_source),
):
    try:
        row = GetDocumentUseCase(data_source).execute(
            GetDocumentQuery(document_id=document_id, tenant_id=auth.tenant_id)
        )
    except DocumentNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return DocumentDetailResponse.from_row(row)


@router.get("/{document_id}/results", response_model=DocumentResultsResponse)
def get_document_results(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    data_source: DocumentDataSource = Depends(get_document_data_source),
):
    try:
        row = GetDocumentUseCase(data_source).execute(
            GetDocumentQuery(document_id=document_id, tenant_id=auth.tenant_id)
        )
    except DocumentNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if row.workflow_status != "succeeded":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Results not yet available")
    return DocumentResultsResponse.from_row(row)


@router.get("/{document_id}/events")
async def stream_document_events(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user_from_query_token),
    read_document: DocumentReader = Depends(get_document_reader),
    event_stream: EventStream = Depends(get_event_stream),
):
    """Server-Sent Events stream of a document's step-status changes.

    Auth is via `?token=` (the browser's EventSource can't set an Authorization
    header). The ownership check below is the tenant-isolation gate: a client
    can only stream a document its own tenant owns (404 otherwise, without
    leaking existence).

    Ordering that closes the connect-time gap: subscribe FIRST, then read the
    DB snapshot. Any event published while the snapshot is read is buffered by
    the live subscription (a harmless duplicate), never lost. The snapshot goes
    out as the first `event: snapshot` (current status + monotonic version), so
    a client is consistent on connect without a separate GET. If already
    terminal, the snapshot is the whole stream — no hang waiting on a silent
    channel. Live `event: step` deltas follow; any event at or below the
    snapshot version is dropped (already reflected). Close on terminal status.
    """
    if read_document(document_id, auth.tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    async def event_generator():
        async with event_stream.subscribe(tenant_id=auth.tenant_id, document_id=document_id) as events:
            snapshot = read_document(document_id, auth.tenant_id)
            if snapshot is None:
                return  # vanished between the two reads (documents aren't deleted) — nothing to stream
            snapshot_version = snapshot.workflow_version
            yield {"event": "snapshot", "data": json.dumps({
                "workflow_status": snapshot.workflow_status,
                "version": snapshot_version,
                "failed_step": snapshot.failed_step,
                "failure_reason": snapshot.failure_reason,
            })}
            if snapshot.workflow_status in _TERMINAL_STATUSES:
                return

            async for event in events:
                if event.get("version", 0) <= snapshot_version:
                    continue  # already reflected in the snapshot (older or duplicate)
                yield {"event": "step", "data": json.dumps(event)}
                if event.get("workflow_status") in _TERMINAL_STATUSES:
                    return

    return EventSourceResponse(event_generator())


@router.get("", response_model=list[DocumentSummaryResponse])
def list_documents(
    auth: AuthContext = Depends(get_current_user),
    data_source: DocumentDataSource = Depends(get_document_data_source),
    limit: int = Query(50, ge=1, le=100, description="Max documents to return (1–100)."),
    offset: int = Query(0, ge=0, description="Number of documents to skip."),
):
    result = ListDocumentsUseCase(data_source).execute(
        ListDocumentsQuery(tenant_id=auth.tenant_id, limit=limit, offset=offset)
    )
    return [DocumentSummaryResponse.from_row(doc) for doc in result.documents]
