"""Partner webhook signature: HMAC-SHA256 over the *exact* request body bytes.

The signature must be computed on the raw bytes as received — re-serializing a
parsed Pydantic/JSON model would change whitespace/key order and break the
comparison. Callers therefore pass `raw_body` straight from `request.body()`.
"""
import hashlib
import hmac


def sign(raw_body: bytes, *, secret: str) -> str:
    """Hex-encoded HMAC-SHA256 of `raw_body`."""
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def verify(raw_body: bytes, signature: str, *, secret: str) -> bool:
    """Constant-time comparison of `signature` against the expected HMAC."""
    expected = sign(raw_body, secret=secret)
    return hmac.compare_digest(expected, signature)
