"""Celery worker entrypoint: `celery -A src.celery_app worker`.

One task per pipeline step. A step task executes exactly one step via
PipelineStepExecutor (the same code path the in-process
SynchronousPipelineDriver drives in tests/demo) and enqueues a task for each
step name it newly unblocks — no central loop, no result backend: the DAG
walks itself forward one `.delay()` at a time.

The worker runs the orchestration directly against Postgres/Redis, not via a
callback to the API (see dev_considerations.md) — same image as the API,
different command, same settings/session-factory bootstrap (src.bootstrap).
"""
import os
import uuid
from typing import Any

from celery import Celery

from src.adapters.in_memory.task_instance_runner import InMemoryTaskInstanceRunner
from src.application.apply_partner_callback import (
    ApplyPartnerCallbackUseCase,
    CallbackPremature,
    PartnerCallbackCommand,
)
from src.application.concurrency import run_with_retry
from src.application.pipeline_step_executor import PipelineStepExecutor
from src.bootstrap import get_blob_store, get_event_publisher, new_unit_of_work
from src.logging_config import configure_logging, get_logger

configure_logging()
_log = get_logger("celery")

# Reads REDIS_URL directly (not via bootstrap.get_settings()): constructing
# the Celery app happens at import time, so it must not require every other
# setting (DATABASE_URL, JWT_SECRET...) to already be set — those are only
# needed once a task actually runs, via get_blob_store()/new_unit_of_work()
# below. Importing this module (e.g. transitively through dependencies.py in
# tests) must stay side-effect-free regarding env vars.
celery_app = Celery("primmo", broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


@celery_app.task(name="pipeline.run_step")
def run_pipeline_step(tenant_id: str, workflow_id: str, step_name: str) -> None:
    # Publishes each transition to Redis (get_event_publisher) so the SSE
    # endpoint in the API process streams it live — the worker and the API are
    # separate processes, Redis is the bus between them.
    executor = PipelineStepExecutor(
        uow_factory=new_unit_of_work,
        task_instance_runner=InMemoryTaskInstanceRunner(),
        blob_store=get_blob_store(),
        event_publisher=get_event_publisher(),
    )
    newly_ready = executor.execute_step(
        tenant_id=uuid.UUID(tenant_id), workflow_id=uuid.UUID(workflow_id), step_name=step_name
    )
    for next_step_name in newly_ready:
        run_pipeline_step.delay(tenant_id=tenant_id, workflow_id=workflow_id, step_name=next_step_name)


@celery_app.task(name="webhooks.apply_partner_callback", bind=True, max_retries=5, default_retry_delay=30)
def apply_partner_callback(
    self, partner_job_id: str, step_name: str, succeeded: bool, result: Any, error: str | None
) -> None:
    """Applies a partner callback off the HTTP request path. `run_with_retry`
    gives a fresh UoW per attempt — that reload is what makes concurrent
    deliveries converge (the loser rejoins on fresh state → already terminal →
    no-op). A CallbackPremature (our side not RUNNING yet) becomes a Celery
    retry with backoff. Finally, any steps unblocked by the callback are
    dispatched (a no-op while external_call is the terminal node)."""
    command = PartnerCallbackCommand(
        partner_job_id=partner_job_id, step_name=step_name, succeeded=succeeded, result=result, error=error
    )
    try:
        callback_result = run_with_retry(
            lambda: ApplyPartnerCallbackUseCase(
                new_unit_of_work(), get_blob_store(), get_event_publisher()
            ).execute(command)
        )
    except CallbackPremature as exc:
        _log.info("partner callback premature, retrying", extra={"partner_job_id": partner_job_id})
        raise self.retry(exc=exc)

    _log.info(
        "partner callback applied",
        extra={
            "doc_id": str(callback_result.workflow_id),
            "partner_job_id": partner_job_id,
            "succeeded": succeeded,
            "already_processed": callback_result.already_processed,
        },
    )
    for next_step_name in callback_result.newly_ready:
        run_pipeline_step.delay(
            tenant_id=str(callback_result.tenant_id),
            workflow_id=str(callback_result.workflow_id),
            step_name=next_step_name,
        )
