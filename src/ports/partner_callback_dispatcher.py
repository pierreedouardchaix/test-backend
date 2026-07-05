from typing import Protocol

from src.application.apply_partner_callback import PartnerCallbackCommand


class PartnerCallbackDispatcher(Protocol):
    """Hands a validated partner callback off to be applied asynchronously, so
    no orchestration work happens inside the partner's HTTP request. The webhook
    endpoint verifies + parses synchronously, then dispatches and returns 202."""

    def dispatch(self, command: PartnerCallbackCommand) -> None: ...
