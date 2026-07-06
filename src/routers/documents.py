import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from sse_starlette.sse import EventSourceResponse

from src.application.get_document import GetDocumentQuery, GetDocumentUseCase
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
from src.routers.schemas import (
    DOCUMENT_TERMINAL_STATUSES,
    DocumentDetailResponse,
    DocumentResultsResponse,
    DocumentSummaryResponse,
    document_status,
)

router = APIRouter(prefix="/documents", tags=["documents"])

# Bound the in-memory upload. A pipeline legitimately handles large scans, but
# an unbounded `await file.read()` lets a single request pin arbitrary RAM in
# the API process — the same DoS posture the webhook guards against. Mirror it
# here: cap the body as a second line of defence, and enforce the real limit at
# the reverse proxy (e.g. nginx `client_max_body_size`) — the app check is the
# backstop, not the gate.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
_UPLOAD_CHUNK_BYTES = 1024 * 1024  # read in 1 MB chunks so we never buffer past the cap


async def _read_capped(file: UploadFile) -> bytes:
    """Read the upload in chunks, aborting with 413 as soon as it exceeds the
    cap — so an over-large (or Content-Length-lying) body never accumulates
    more than `_MAX_UPLOAD_BYTES` in memory."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Uploaded file too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile,
    request: Request,
    auth: AuthContext = Depends(get_current_user),
    uow: UnitOfWork = Depends(get_uow),
    blob_store: BlobStore = Depends(get_blob_store),
    dispatcher: WorkflowDispatcher = Depends(get_workflow_dispatcher),
):
    # Fast-fail on an honest Content-Length before reading a byte; the capped
    # read below is the real guard (a client can under-declare or omit it).
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_length = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")
        if declared_length > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Uploaded file too large")

    content = await _read_capped(file)
    if not content:
        # A 0-byte upload can't be processed. Reject it here as unprocessable
        # input — 422, not 204: the request failed, it didn't succeed with no
        # content. (The domain's size guard is the same 422 as a safety net.)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded file is empty")
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
    return {"document_id": str(result.document_id), "status": document_status(result.workflow_status)}


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    data_source: DocumentDataSource = Depends(get_document_data_source),
):
    # DocumentNotFound propagates to the app-level handler (404).
    row = GetDocumentUseCase(data_source).execute(
        GetDocumentQuery(document_id=document_id, tenant_id=auth.tenant_id)
    )
    return DocumentDetailResponse.from_row(row)


@router.get("/{document_id}/results", response_model=DocumentResultsResponse)
def get_document_results(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    data_source: DocumentDataSource = Depends(get_document_data_source),
    blob_store: BlobStore = Depends(get_blob_store),
):
    row = GetDocumentUseCase(data_source).execute(
        GetDocumentQuery(document_id=document_id, tenant_id=auth.tenant_id)
    )
    if row.workflow_status == "failed":
        # Terminal: results will never come. Distinct from "not ready yet" so a
        # polling client stops instead of retrying forever.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Processing failed at step '{row.failed_step}': {row.failure_reason}",
        )
    if row.workflow_status != "succeeded":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document is not ready yet (still processing)")
    # step_results holds blob keys (internal); resolve each to the actual value.
    results = {step: json.loads(blob_store.get(blob_key)) for step, blob_key in row.step_results.items()}
    return DocumentResultsResponse(results=results)


def _terminal_workflow_event(workflow_status: str, version: int | None, failed_step, failure_reason) -> dict:
    """A distinct `event: workflow` marking the workflow reaching a terminal
    state (ready/failed). Emitted right before the stream closes so a client can
    key its "done" handling off a single event type instead of having to read
    `workflow_status` on whichever step event happened to trigger the
    transition. Same payload shape as the snapshot."""
    return {"event": "workflow", "data": json.dumps({
        "workflow_status": workflow_status,
        "version": version,
        "failed_step": failed_step,
        "failure_reason": failure_reason,
    })}


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
    snapshot version is dropped (already reflected). Each forwarded event
    carries the document-facing `workflow_status` (processing/ready/failed)
    alongside the task-level `step_status`. When the workflow reaches a terminal
    state, a distinct `event: workflow` is emitted and the stream closes — so a
    client has one explicit "done" signal, not a status buried in a step event.
    """
    if read_document(document_id, auth.tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    async def event_generator():
        async with event_stream.subscribe(tenant_id=auth.tenant_id, document_id=document_id) as events:
            snapshot = read_document(document_id, auth.tenant_id)
            if snapshot is None:
                return  # vanished between the two reads (documents aren't deleted) — nothing to stream
            snapshot_version = snapshot.workflow_version
            snapshot_status = document_status(snapshot.workflow_status)  # document-facing: processing/ready/failed
            yield {"event": "snapshot", "data": json.dumps({
                "workflow_status": snapshot_status,
                "version": snapshot_version,
                "failed_step": snapshot.failed_step,
                "failure_reason": snapshot.failure_reason,
            })}
            if snapshot_status in DOCUMENT_TERMINAL_STATUSES:
                # Already terminal on connect: emit the explicit workflow event too
                # (same signal whether discovered via snapshot or a live delta).
                yield _terminal_workflow_event(
                    snapshot_status, snapshot_version, snapshot.failed_step, snapshot.failure_reason
                )
                return

            async for event in events:
                if event.get("version", 0) <= snapshot_version:
                    continue  # already reflected in the snapshot (older or duplicate)
                # Map the internal workflow status to the document-facing one under
                # `workflow_status`; the per-step `step_status` is task-level and
                # stays as is. Build a new dict (don't mutate the incoming event).
                doc_status = document_status(event.get("workflow_status", ""))
                out = {k: v for k, v in event.items() if k != "workflow_status"}
                out["workflow_status"] = doc_status
                yield {"event": "step", "data": json.dumps(out)}
                if doc_status in DOCUMENT_TERMINAL_STATUSES:
                    # The transition to terminal rode in on this step event; surface
                    # it as a first-class workflow event, then close. On failure the
                    # failing step and its error are this event's step/error.
                    failed = doc_status == "failed"
                    yield _terminal_workflow_event(
                        doc_status,
                        event.get("version"),
                        event.get("step") if failed else None,
                        event.get("error") if failed else None,
                    )
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
