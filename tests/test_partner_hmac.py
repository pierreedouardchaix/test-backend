from src.partner_hmac import sign, verify


def test_verify_accepts_matching_signature():
    body = b'{"job_id": "abc", "status": "completed"}'
    signature = sign(body, secret="s3cr3t")
    assert verify(body, signature, secret="s3cr3t") is True


def test_verify_rejects_wrong_signature():
    body = b'{"job_id": "abc"}'
    assert verify(body, "deadbeef", secret="s3cr3t") is False


def test_verify_rejects_wrong_secret():
    body = b'{"job_id": "abc"}'
    signature = sign(body, secret="s3cr3t")
    assert verify(body, signature, secret="other-secret") is False


def test_signature_is_sensitive_to_exact_bytes():
    # A single byte of extra whitespace changes the signature — this is why the
    # webhook must sign the raw body, not a re-serialized model.
    signature = sign(b'{"a":1}', secret="s3cr3t")
    assert verify(b'{"a": 1}', signature, secret="s3cr3t") is False
