import uuid

# Shared between the publisher (worker/webhook, sync) and the subscriber (SSE
# endpoint, async) so both sides agree on the exact channel string. Keyed by
# (tenant, document): document_id alone is globally unique and would suffice to
# route, but including tenant_id is defence in depth — a subscriber cannot even
# name another tenant's channel without knowing that tenant's id (the real
# isolation gate stays the DB ownership check before subscribing).


def document_events_channel(tenant_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"events:{tenant_id}:{document_id}"
