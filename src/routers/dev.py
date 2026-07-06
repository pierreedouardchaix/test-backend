"""Dev-only router.

Like the auth dev-token endpoint, this is gated behind DEV_MODE and carries no
business logic. It exists solely to make the signed partner webhook testable
from Swagger: sign an arbitrary JSON body with PARTNER_HMAC_SECRET and get back
the hex signature to paste into the X-Partner-Signature header.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.dependencies import get_settings
from src.partner_hmac import sign
from src.settings import Settings

router = APIRouter(prefix="/dev", tags=["dev"])


# The handler reads the RAW body (the signature must cover the exact bytes the
# caller will re-send to /webhooks/partner), so there is no declared body param
# — which means Swagger would render no request-body editor. Declare the body in
# the OpenAPI schema manually so Swagger shows an editable JSON box with an
# example; FastAPI still leaves the body for `await request.body()` to read.
@router.post(
    "/sign",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {
                        "job_id": "<partner_job_id from GET /documents/{id}>",
                        "status": "completed",
                        "result": {"indexed_at": "2026-01-01T00:00:00Z"},
                    },
                }
            },
        }
    },
)
async def sign_body(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.dev_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available in production")

    # Sign the exact bytes received — the caller must POST the same raw body
    # to /webhooks/partner for the signature to match.
    raw_body = await request.body()
    signature = sign(raw_body, secret=settings.partner_hmac_secret)
    return {"signature": signature}
