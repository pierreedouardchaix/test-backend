import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from src.application.get_document import DocumentNotFound, GetDocumentQuery, GetDocumentUseCase
from src.application.ingest_document import IngestDocumentCommand, IngestDocumentUseCase
from src.application.list_documents import ListDocumentsQuery, ListDocumentsUseCase
from src.application.unit_of_work import UnitOfWork
from src.auth import AuthContext, get_current_user
from src.dependencies import get_blob_store, get_document_data_source, get_uow, get_workflow_dispatcher
from src.ports.blob_store import BlobStore
from src.ports.document_data_source import DocumentDataSource
from src.ports.workflow_dispatcher import WorkflowDispatcher
from src.routers.schemas import DocumentDetailResponse, DocumentSummaryResponse

router = APIRouter(prefix="/documents", tags=["documents"])


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


@router.get("", response_model=list[DocumentSummaryResponse])
def list_documents(
    auth: AuthContext = Depends(get_current_user),
    data_source: DocumentDataSource = Depends(get_document_data_source),
):
    result = ListDocumentsUseCase(data_source).execute(
        ListDocumentsQuery(tenant_id=auth.tenant_id)
    )
    return [DocumentSummaryResponse.from_row(doc) for doc in result.documents]
