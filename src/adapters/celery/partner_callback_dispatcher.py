from src.application.apply_partner_callback import PartnerCallbackCommand
from src.celery_app import apply_partner_callback


class CeleryPartnerCallbackDispatcher:
    """Enqueues the apply-partner-callback Celery task. Arguments are passed as
    broker-serializable primitives (str/bool/JSON); `result` is the partner's
    JSON payload, `error` a string."""

    def dispatch(self, command: PartnerCallbackCommand) -> None:
        apply_partner_callback.delay(
            partner_job_id=command.partner_job_id,
            step_name=command.step_name,
            succeeded=command.succeeded,
            result=command.result,
            error=command.error,
        )
