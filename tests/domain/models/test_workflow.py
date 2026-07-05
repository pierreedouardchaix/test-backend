import uuid

import pytest

from src.domain.models.task import TaskStatus
from src.domain.models.workflow import Workflow, WorkflowStatus
from src.domain.models.workflow_definition import StepDefinition, WorkflowDefinition


def make_definition(**step_overrides: int) -> WorkflowDefinition:
    """t1 is the root; t2 and t3 fan out from it; t4 fans back in on both.
    step_overrides maps a step name to a custom max_attempts."""
    return WorkflowDefinition(
        name="test_pipeline",
        steps=(
            StepDefinition(name="t1", max_attempts=step_overrides.get("t1", 3)),
            StepDefinition(name="t2", depends_on=frozenset({"t1"}), max_attempts=step_overrides.get("t2", 3)),
            StepDefinition(name="t3", depends_on=frozenset({"t1"}), max_attempts=step_overrides.get("t3", 3)),
            StepDefinition(
                name="t4", depends_on=frozenset({"t2", "t3"}), max_attempts=step_overrides.get("t4", 3)
            ),
        ),
    )


def make_workflow(**step_overrides: int) -> Workflow:
    return Workflow.create(id=uuid.uuid4(), tenant_id=uuid.uuid4(), definition=make_definition(**step_overrides))


def run_step(workflow: Workflow, step_name: str, result):
    workflow.start_task(step_name)
    return workflow.on_task_succeeded(step_name, result)


# --- fan-out / fan-in -------------------------------------------------------

def test_fan_out_after_root_succeeds():
    workflow = make_workflow()
    ready = run_step(workflow, "t1", "r1")
    assert ready == frozenset({"t2", "t3"})


def test_fan_in_not_ready_with_only_one_branch_done():
    workflow = make_workflow()
    run_step(workflow, "t1", "r1")

    ready = run_step(workflow, "t2", "r2")
    assert "t4" not in ready

    workflow_other_branch = make_workflow()
    run_step(workflow_other_branch, "t1", "r1")
    ready = run_step(workflow_other_branch, "t3", "r3")
    assert "t4" not in ready


@pytest.mark.parametrize("order", [("t2", "t3"), ("t3", "t2")])
def test_fan_in_ready_regardless_of_completion_order(order):
    workflow = make_workflow()
    run_step(workflow, "t1", "r1")

    first, second = order
    run_step(workflow, first, f"r_{first}")
    ready = run_step(workflow, second, f"r_{second}")

    assert ready == frozenset({"t4"})


def test_fan_in_sibling_already_dispatched_is_not_rediscovered_as_ready():
    """Regression: in real multi-worker execution, t2 and t3 are both
    dispatched (start_task) before either completes — they run concurrently
    on different workers. When t2 finishes first, ready_steps() must not
    re-list t3 (it's already RUNNING, just not finished yet) — otherwise the
    caller would enqueue a second, duplicate execution of t3."""
    workflow = make_workflow()
    run_step(workflow, "t1", "r1")

    workflow.start_task("t2")
    workflow.start_task("t3")  # both dispatched before either finishes

    ready = workflow.on_task_succeeded("t2", "r2")
    assert "t3" not in ready


def test_fan_in_blocked_by_terminal_failure_of_one_branch():
    workflow = make_workflow(t2=1)
    run_step(workflow, "t1", "r1")

    workflow.start_task("t2")
    workflow.on_task_failed("t2", "boom")

    assert workflow.status == WorkflowStatus.FAILED
    assert workflow.failed_step == "t2"
    assert workflow.ready_steps() == frozenset()


def test_transient_failure_does_not_affect_workflow_state():
    workflow = make_workflow(t2=3)
    run_step(workflow, "t1", "r1")

    workflow.start_task("t2")
    workflow.on_task_failed("t2", "transient error")

    assert workflow.status == WorkflowStatus.RUNNING
    assert workflow.failed_step is None
    assert workflow.failure_reason is None
    assert "t2" not in workflow.results
    assert workflow.tasks["t2"].status == TaskStatus.RETRYING


def test_workflow_succeeds_only_once_every_step_is_done():
    workflow = make_workflow()
    run_step(workflow, "t1", "r1")
    assert workflow.status == WorkflowStatus.RUNNING

    run_step(workflow, "t2", "r2")
    assert workflow.status == WorkflowStatus.RUNNING

    run_step(workflow, "t3", "r3")
    assert workflow.status == WorkflowStatus.RUNNING  # t4 still pending

    run_step(workflow, "t4", "r4")
    assert workflow.status == WorkflowStatus.SUCCEEDED


# --- terminal-state guards ---------------------------------------------------

def test_cannot_mutate_a_failed_workflow():
    workflow = make_workflow(t1=1)
    workflow.start_task("t1")
    workflow.on_task_failed("t1", "boom")
    assert workflow.status == WorkflowStatus.FAILED

    with pytest.raises(ValueError):
        workflow.on_task_succeeded("t2", "r2")
    with pytest.raises(ValueError):
        workflow.on_task_failed("t2", "boom again")
    with pytest.raises(ValueError):
        workflow.record_step_result("t2", "r2")
    with pytest.raises(ValueError):
        workflow.mark_step_failed("t2", "boom again")


def test_cannot_mutate_a_succeeded_workflow():
    workflow = make_workflow()
    for step, deps_result in [("t1", "r1"), ("t2", "r2"), ("t3", "r3"), ("t4", "r4")]:
        run_step(workflow, step, deps_result)
    assert workflow.status == WorkflowStatus.SUCCEEDED

    with pytest.raises(ValueError):
        workflow.record_step_result("t1", "again")
    with pytest.raises(ValueError):
        workflow.mark_step_failed("t1", "too late")


def test_reporting_a_success_on_the_surviving_branch_after_a_sibling_terminally_failed():
    """Regression for the gap found while writing these tests: t2 and t3 are
    both dispatched (fan-out), t2 fails terminally first. Reporting t3's
    success afterwards must raise cleanly and must not mark t3's task as
    succeeded — otherwise a caller that doesn't handle the exception could
    persist a workflow that is FAILED yet has a task marked SUCCEEDED."""
    workflow = make_workflow(t2=1)
    run_step(workflow, "t1", "r1")
    workflow.start_task("t2")
    workflow.start_task("t3")

    workflow.on_task_failed("t2", "boom")
    assert workflow.status == WorkflowStatus.FAILED

    with pytest.raises(ValueError):
        workflow.on_task_succeeded("t3", "r3")
    assert workflow.tasks["t3"].status == TaskStatus.RUNNING  # untouched by the raised call


# --- start_task -----------------------------------------------------------

def test_start_task_creates_task_with_the_step_max_attempts():
    workflow = make_workflow(t2=7)
    task = workflow.start_task("t1")
    assert task.max_attempts == 3

    workflow.on_task_succeeded("t1", "r1")  # settle t1's dispatch before dispatching t2
    task = workflow.start_task("t2")
    assert task.max_attempts == 7


def test_start_task_reuses_the_same_task_across_retries():
    workflow = make_workflow(t1=3)
    task = workflow.start_task("t1")
    task_id = task.id
    workflow.on_task_failed("t1", "transient")

    retried_task = workflow.start_task("t1")
    assert retried_task.id == task_id
    assert retried_task.attempts == 2


def test_start_task_unknown_step_raises():
    workflow = make_workflow()
    with pytest.raises(ValueError):
        workflow.start_task("unknown_step")


def test_start_task_on_a_terminal_workflow_raises():
    workflow = make_workflow(t1=1)
    workflow.start_task("t1")
    workflow.on_task_failed("t1", "boom")
    assert workflow.status == WorkflowStatus.FAILED

    with pytest.raises(ValueError):
        workflow.start_task("t2")


# --- reporting an outcome without a prior dispatch ---------------------------

def test_on_task_succeeded_without_prior_dispatch_raises_a_clear_error():
    workflow = make_workflow()
    with pytest.raises(ValueError, match="t1"):
        workflow.on_task_succeeded("t1", "r1")


def test_on_task_failed_without_prior_dispatch_raises_a_clear_error():
    workflow = make_workflow()
    with pytest.raises(ValueError, match="t1"):
        workflow.on_task_failed("t1", "boom")
