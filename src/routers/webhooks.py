"""Partner webhook ingress.

No JWT here — the partner authenticates with an HMAC signature over the raw
body. The signature MUST be checked against the exact bytes received, so the
body is read and verified before any parsing (a re-serialized model would not
match). This layer only does transport: verify, parse, gate on existence, then
hand off to a Celery task and return 202 — no orchestration work runs inside
the partner's request (see dev_considerations.md).
"""
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from src.application.apply_partner_callback import PartnerCallbackCommand
from src.dependencies import (
    PartnerJobResolver,
    get_partner_callback_dispatcher,
    get_partner_job_resolver,
    get_settings,
)
from src.partner_hmac import verify
from src.ports.partner_callback_dispatcher import PartnerCallbackDispatcher
from src.settings import Settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Reject oversized bodies before HMAC computation (HMAC on a multi-MB body is a DoS vector).
# Also configure this at the web-server level (e.g. nginx `client_max_body_size 64k`,
# uvicorn `--limit-concurrency`) — the application-level check is a second line of defence, not the first.
_MAX_BODY_BYTES = 64 * 1024  # 64 KB is orders of magnitude above any legitimate callback payload


def _declared_content_length(header_value: str | None) -> int | None:
    """Parse the Content-Length header. A client-supplied header that is not a
    number is a malformed request (400), not a server error — so this is
    guarded rather than letting int() raise a 500."""
    if header_value is None:
        return None
    try:
        return int(header_value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header")


class PartnerWebhookPayload(BaseModel):
    job_id: str = Field(max_length=128)  # the partner's opaque job id (j_<hex>), per the README example
    status: str  # "completed" | "failed"
    result: Any | None = None
    error: str | None = Field(default=None, max_length=2000)


_TERMINAL_TASK_STATUSES = ("succeeded", "failed")


@router.post("/partner", status_code=status.HTTP_202_ACCEPTED)
async def partner_webhook(
    request: Request,
    x_partner_signature: str = Header(default=""),
    settings: Settings = Depends(get_settings),
    partner_job_status: PartnerJobResolver = Depends(get_partner_job_resolver),
    dispatcher: PartnerCallbackDispatcher = Depends(get_partner_callback_dispatcher),
):
    declared_length = _declared_content_length(request.headers.get("content-length"))
    if declared_length is not None and declared_length > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
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

    task_status = partner_job_status(payload.job_id)
    # Unknown job id → 404, so the partner retries (a very fast webhook can beat
    # our own persistence of the job id — that race resolves by retry; see
    # dev_considerations.md).
    if task_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown job_id")
    # The step already reached a terminal outcome → this callback (a partner
    # retry, or a contradictory second delivery) is a no-op. Acknowledge it
    # synchronously (200) without enqueuing — the task-level idempotence in the
    # use case is still the real guarantee against a concurrent duplicate that
    # slips past this best-effort check.
    if task_status in _TERMINAL_TASK_STATUSES:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "already_processed"})

    dispatcher.dispatch(
        PartnerCallbackCommand(
            partner_job_id=payload.job_id,
            step_name="external_call",
            succeeded=payload.status == "completed",
            result=payload.result,
            error=payload.error,
        )
    )
    return {"status": "accepted"}
