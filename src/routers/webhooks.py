"""Partner webhook ingress.

No JWT here — the partner authenticates with an HMAC signature over the raw
body. The signature MUST be checked against the exact bytes received, so the
body is read and verified before any parsing (a re-serialized model would not
match). Correlation and tenant resolution live in the use case; this layer only
does transport: verify, parse, map errors to status codes.
"""
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from src.application.apply_partner_callback import (
    ApplyPartnerCallbackUseCase,
    PartnerCallbackCommand,
    WorkflowNotFound,
)
from src.application.unit_of_work import UnitOfWork
from src.dependencies import get_blob_store, get_event_publisher, get_settings, get_uow
from src.partner_hmac import verify
from src.ports.blob_store import BlobStore
from src.ports.event_publisher import EventPublisher
from src.settings import Settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class PartnerWebhookPayload(BaseModel):
    job_id: UUID
    status: str  # "completed" | "failed"
    result: Any | None = None
    error: str | None = None


@router.post("/partner")
async def partner_webhook(
    request: Request,
    x_partner_signature: str = Header(default=""),
    settings: Settings = Depends(get_settings),
    uow: UnitOfWork = Depends(get_uow),
    blob_store: BlobStore = Depends(get_blob_store),
    event_publisher: EventPublisher = Depends(get_event_publisher),
):
    raw_body = await request.body()
    if not verify(raw_body, x_partner_signature, secret=settings.partner_hmac_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = PartnerWebhookPayload.model_validate(json.loads(raw_body))
    except (json.JSONDecodeError, ValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Malformed payload")

    if payload.status not in ("completed", "failed"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be 'completed' or 'failed'",
        )

    command = PartnerCallbackCommand(
        job_id=payload.job_id,
        succeeded=payload.status == "completed",
        result=payload.result,
        error=payload.error,
    )
    try:
        result = ApplyPartnerCallbackUseCase(uow, blob_store, event_publisher).execute(command)
    except WorkflowNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown job_id")

    return {"workflow_status": result.workflow_status}
