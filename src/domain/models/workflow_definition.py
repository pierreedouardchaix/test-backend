from collections import deque
from dataclasses import dataclass

from src.domain.errors import DomainValidationError


@dataclass(frozen=True)
class StepDefinition:
    name: str
    depends_on: frozenset[str] = frozenset()
    max_attempts: int = 3
    """Total number of attempts allowed (including the first), before the step
    is considered a terminal failure."""

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise DomainValidationError(f"Step {self.name!r} must allow at least 1 attempt")


@dataclass(frozen=True)
class WorkflowDefinition:
    """The shape of a DAG: which steps exist and what each depends on.

    Purely structural — no execution state. Fan-out (several steps sharing a
    dependency) and fan-in (a step depending on several others) fall out
    naturally from `depends_on`; loops are rejected at construction time.
    """

    name: str
    steps: tuple[StepDefinition, ...]

    def __post_init__(self) -> None:
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise DomainValidationError(f"Workflow definition {self.name!r} has duplicate step names")

        known = set(names)
        for step in self.steps:
            unknown = step.depends_on - known
            if unknown:
                raise DomainValidationError(
                    f"Step {step.name!r} depends on unknown step(s): {sorted(unknown)}"
                )

        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        dependents: dict[str, list[str]] = {step.name: [] for step in self.steps}
        in_degree = {step.name: len(step.depends_on) for step in self.steps}
        for step in self.steps:
            for dependency in step.depends_on:
                dependents[dependency].append(step.name)

        queue = deque(name for name, degree in in_degree.items() if degree == 0)
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for dependent in dependents[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if visited != len(self.steps):
            raise DomainValidationError(f"Workflow definition {self.name!r} contains a cycle")

    def step_names(self) -> frozenset[str]:
        return frozenset(step.name for step in self.steps)

    def get_step(self, name: str) -> StepDefinition:
        for step in self.steps:
            if step.name == name:
                return step
        raise DomainValidationError(f"Unknown step {name!r} for workflow definition {self.name!r}")

    def roots(self) -> frozenset[str]:
        return frozenset(step.name for step in self.steps if not step.depends_on)

    def ready_steps(self, completed: frozenset[str]) -> frozenset[str]:
        """Steps whose dependencies are all satisfied but that haven't run yet."""
        return frozenset(
            step.name
            for step in self.steps
            if step.name not in completed and step.depends_on <= completed
        )
