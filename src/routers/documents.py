from fastapi import APIRouter, Depends, UploadFile

from src.application.ingest_document import IngestDocumentCommand, IngestDocumentUseCase
from src.application.list_documents import ListDocumentsQuery, ListDocumentsUseCase
from src.application.unit_of_work import UnitOfWork
from src.auth import AuthContext, get_current_user
from src.dependencies import get_blob_store, get_document_data_source, get_uow, get_workflow_dispatcher
from src.ports.blob_store import BlobStore
from src.ports.document_data_source import DocumentDataSource
from src.ports.workflow_dispatcher import WorkflowDispatcher

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


@router.get("")
def list_documents(
    auth: AuthContext = Depends(get_current_user),
    data_source: DocumentDataSource = Depends(get_document_data_source),
):
    result = ListDocumentsUseCase(data_source).execute(
        ListDocumentsQuery(tenant_id=auth.tenant_id)
    )
    return {
        "documents": [
            {
                "document_id": str(doc.document_id),
                "filename": doc.filename,
                "content_type": doc.content_type,
                "size_bytes": doc.size_bytes,
                "created_at": doc.created_at.isoformat(),
                "uploaded_by": str(doc.uploaded_by),
                "uploaded_by_name": f"{doc.uploaded_by_first_name} {doc.uploaded_by_last_name}",
                "workflow_status": doc.workflow_status,
                "failed_step": doc.failed_step,
                "failure_reason": doc.failure_reason,
            }
            for doc in result.documents
        ]
    }
