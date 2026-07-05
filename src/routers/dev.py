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


@router.post("/sign")
async def sign_body(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.dev_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available in production")

    # Sign the exact bytes received — the caller must POST the same raw body
    # to /webhooks/partner for the signature to match.
    raw_body = await request.body()
    signature = sign(raw_body, secret=settings.partner_hmac_secret)
    return {"signature": signature}
