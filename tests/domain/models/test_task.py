import uuid

import pytest

from src.domain.models.task import Task, TaskStatus


def make_task(max_attempts: int = 3) -> Task:
    return Task.create(workflow_id=uuid.uuid4(), step_name="t1", max_attempts=max_attempts)


def test_start_sets_running_and_increments_attempts():
    task = make_task()
    task.start()
    assert task.status == TaskStatus.RUNNING
    assert task.attempts == 1
    assert task.started_at is not None


def test_start_does_not_reset_started_at_on_retry():
    task = make_task(max_attempts=3)
    task.start()
    first_started_at = task.started_at
    task.fail("boom")
    task.start()
    assert task.started_at == first_started_at
    assert task.attempts == 2


def test_succeed_from_running_sets_finished_at():
    task = make_task()
    task.start()
    task.succeed()
    assert task.status == TaskStatus.SUCCEEDED
    assert task.finished_at is not None


def test_fail_with_attempts_remaining_moves_to_retrying():
    task = make_task(max_attempts=3)
    task.start()
    can_retry = task.fail("transient error")
    assert can_retry is True
    assert task.status == TaskStatus.RETRYING
    assert task.last_error == "transient error"
    assert task.finished_at is None


def test_errors_accumulate_across_attempts_instead_of_being_overwritten():
    task = make_task(max_attempts=3)
    task.start()
    task.fail("first failure")
    task.start()
    task.fail("second failure")

    assert [e.error for e in task.errors] == ["first failure", "second failure"]
    assert [e.attempt for e in task.errors] == [1, 2]
    assert all(e.occurred_at is not None for e in task.errors)
    assert task.last_error == "second failure"


def test_fail_at_max_attempts_boundary_is_terminal():
    task = make_task(max_attempts=2)
    task.start()
    assert task.fail("first failure") is True
    assert task.status == TaskStatus.RETRYING

    task.start()
    assert task.attempts == 2
    can_retry = task.fail("second failure")
    assert can_retry is False
    assert task.status == TaskStatus.FAILED
    assert task.last_error == "second failure"
    assert task.finished_at is not None


@pytest.mark.parametrize("bad_status", [TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED])
def test_start_from_invalid_state_raises(bad_status):
    task = make_task()
    task.status = bad_status
    with pytest.raises(ValueError):
        task.start()


def test_succeed_from_pending_raises():
    task = make_task()
    with pytest.raises(ValueError):
        task.succeed()


def test_fail_from_pending_raises():
    task = make_task()
    with pytest.raises(ValueError):
        task.fail("error")


def test_fail_from_already_failed_raises():
    task = make_task(max_attempts=1)
    task.start()
    task.fail("terminal")
    with pytest.raises(ValueError):
        task.fail("again")
