import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Self


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskAttemptError:
    attempt: int
    error: str
    occurred_at: datetime


@dataclass
class Task:
    """The execution record for one step of a Workflow.

    Owns attempt-level lifecycle (pending/running/retrying) so that transient
    failures never reach the Workflow — only a terminal outcome (succeeded, or
    failed after exhausting max_attempts) does.
    """

    id: uuid.UUID
    workflow_id: uuid.UUID
    step_name: str
    max_attempts: int
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    errors: list[TaskAttemptError] = field(default_factory=list)
    # The external system's correlation id for a deferred step (partner job id).
    # Set once the step has handed off and is awaiting its callback.
    partner_job_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def last_error(self) -> str | None:
        return self.errors[-1].error if self.errors else None

    @classmethod
    def create(cls, *, workflow_id: uuid.UUID, step_name: str, max_attempts: int) -> Self:
        return cls(
            id=uuid.uuid4(),
            workflow_id=workflow_id,
            step_name=step_name,
            max_attempts=max_attempts,
        )

    def start(self) -> None:
        """Call right before executing the step's function — once per attempt."""
        if self.status not in (TaskStatus.PENDING, TaskStatus.RETRYING):
            raise ValueError(f"Cannot start a task that is {self.status}")
        self.status = TaskStatus.RUNNING
        self.attempts += 1
        if self.started_at is None:
            self.started_at = datetime.now(timezone.utc)

    def succeed(self) -> None:
        if self.status != TaskStatus.RUNNING:
            raise ValueError(f"Cannot succeed a task that is {self.status}")
        self.status = TaskStatus.SUCCEEDED
        self.finished_at = datetime.now(timezone.utc)

    def fail(self, error: str) -> bool:
        """Record a failed attempt.

        Returns True if another attempt remains (caller should reschedule),
        False if attempts are exhausted and the failure is now terminal.
        """
        if self.status != TaskStatus.RUNNING:
            raise ValueError(f"Cannot fail a task that is {self.status}")
        self.errors.append(
            TaskAttemptError(attempt=self.attempts, error=error, occurred_at=datetime.now(timezone.utc))
        )
        if self.attempts < self.max_attempts:
            self.status = TaskStatus.RETRYING
            return True
        self.status = TaskStatus.FAILED
        self.finished_at = datetime.now(timezone.utc)
        return False

    def mark_deferred(self, partner_job_id: str) -> None:
        """Record that this running step has handed off to an external system
        and is awaiting its callback. The task stays RUNNING — only the
        correlation id is attached; nothing else changes."""
        if self.status != TaskStatus.RUNNING:
            raise ValueError(f"Cannot defer a task that is {self.status}")
        self.partner_job_id = partner_job_id

    def fail_terminally(self, error: str) -> None:
        """Fail with no retry, regardless of remaining attempts — for an
        outcome declared final by an external authority (e.g. the partner
        webhook reports the job failed; the partner does its own retrying)."""
        if self.status != TaskStatus.RUNNING:
            raise ValueError(f"Cannot fail a task that is {self.status}")
        self.errors.append(
            TaskAttemptError(attempt=self.attempts, error=error, occurred_at=datetime.now(timezone.utc))
        )
        self.status = TaskStatus.FAILED
        self.finished_at = datetime.now(timezone.utc)
