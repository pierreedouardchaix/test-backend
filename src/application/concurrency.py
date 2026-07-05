from typing import Callable, TypeVar

T = TypeVar("T")


class ConcurrencyError(Exception):
    """Raised by a repository when an optimistic-locked write finds the row
    changed under it (version mismatch). The caller must reload and retry —
    see run_with_retry."""


def run_with_retry(operation: Callable[[], T], *, max_attempts: int = 3) -> T:
    """Run `operation`, retrying on ConcurrencyError.

    `operation` must perform the whole load → mutate → commit inside itself
    (opening its own UnitOfWork each call), so that a retry re-reads fresh
    state — that re-read is exactly what makes the fan-in resolve correctly
    (the branch that commits second sees both results). Kept out of the
    UnitOfWork on purpose: the UoW stays a plain transaction boundary, the
    retry policy is a separate, reusable concern.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except ConcurrencyError:
            if attempt == max_attempts:
                raise
    raise AssertionError("unreachable")
