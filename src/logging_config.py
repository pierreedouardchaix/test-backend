"""Structured (JSON) logging for the application's own events.

Keyed by `doc_id` (== document id == workflow id) so a single document's journey
across the API and the Celery workers can be grepped/filtered by that one id.

Logs go through the `primmo` logger namespace with its own JSON handler and
`propagate=False`, so they stay separate from uvicorn/Celery framework logs
(which keep their own format). Call `configure_logging()` once per process
(API startup, worker startup)."""
import json
import logging
import sys

# Attributes present on a bare LogRecord — everything else on the record is
# treated as structured context (passed via `extra={...}`) and merged into the line.
_STANDARD_ATTRS = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    logger = logging.getLogger("primmo")
    if logger.handlers:  # idempotent — configure once per process
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def get_logger(name: str) -> logging.LoggerAdapter | logging.Logger:
    """A logger under the `primmo` namespace. Log structured context with
    `logger.info("...", extra={"doc_id": ..., "step": ...})`."""
    return logging.getLogger(f"primmo.{name}")
