class DomainError(Exception):
    """Base for errors raised by the domain/application layers. FastAPI handlers
    map the typed subclasses to the right status (the not-found ones to 404,
    validation to 422, illegal transitions to 409); any *un*typed DomainError is
    an unexpected condition and still surfaces as a 500."""


class TaskNotFound(DomainError):
    """No task has been dispatched yet for the referenced step — a caller
    reported an outcome (or deferred) a step it never started."""


class DomainValidationError(DomainError, ValueError):
    """An input or invariant the domain refuses to accept — an empty filename,
    a non-positive size, a malformed workflow definition, an unknown step name.
    Also subclasses ValueError so existing callers/tests that catch ValueError
    keep working; the FastAPI handler maps it to 422."""


class InvalidStateTransition(DomainError, ValueError):
    """An operation illegal for the entity's current state — starting a task
    that is already running, applying an outcome to a terminal workflow. Also a
    ValueError (as above); the FastAPI handler maps it to 409 Conflict."""
