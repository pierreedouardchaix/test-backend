class DomainError(Exception):
    """Base for errors raised by the domain/application layers. A FastAPI
    handler maps the not-found subclasses to 404; any other DomainError is an
    unexpected condition and surfaces as a 500 (current behaviour)."""


class TaskNotFound(DomainError):
    """No task has been dispatched yet for the referenced step — a caller
    reported an outcome (or deferred) a step it never started."""
