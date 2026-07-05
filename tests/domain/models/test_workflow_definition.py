"""Focused tests for how WorkflowDefinition computes which steps are ready —
the pure structural calculation, independent of any Workflow's status or
result accumulation. Structural *validation* (cycles, duplicates...) is
deliberately not tested yet — see dev_considerations.md."""

from src.domain.models.workflow_definition import StepDefinition, WorkflowDefinition


def make_definition() -> WorkflowDefinition:
    """t1 -> {t2, t3} -> t4 : one root, a fan-out and a fan-in."""
    return WorkflowDefinition(
        name="test_pipeline",
        steps=(
            StepDefinition(name="t1"),
            StepDefinition(name="t2", depends_on=frozenset({"t1"})),
            StepDefinition(name="t3", depends_on=frozenset({"t1"})),
            StepDefinition(name="t4", depends_on=frozenset({"t2", "t3"})),
        ),
    )


def test_roots_are_the_only_ready_steps_when_nothing_is_completed():
    definition = make_definition()
    assert definition.roots() == frozenset({"t1"})
    assert definition.ready_steps(completed=frozenset()) == frozenset({"t1"})


def test_completing_the_root_unblocks_both_fan_out_branches():
    definition = make_definition()
    assert definition.ready_steps(completed=frozenset({"t1"})) == frozenset({"t2", "t3"})


def test_fan_in_step_stays_blocked_until_all_its_dependencies_are_completed():
    definition = make_definition()
    assert definition.ready_steps(completed=frozenset({"t1", "t2"})) == frozenset({"t3"})
    assert definition.ready_steps(completed=frozenset({"t1", "t3"})) == frozenset({"t2"})


def test_fan_in_step_becomes_ready_once_every_dependency_is_completed():
    definition = make_definition()
    assert definition.ready_steps(completed=frozenset({"t1", "t2", "t3"})) == frozenset({"t4"})


def test_already_completed_steps_are_never_reported_as_ready():
    definition = make_definition()
    assert definition.ready_steps(completed=frozenset({"t1", "t2", "t3", "t4"})) == frozenset()
