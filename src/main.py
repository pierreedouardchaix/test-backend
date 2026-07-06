import os

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.application.apply_partner_callback import WorkflowNotFound
from src.application.get_document import DocumentNotFound
from src.domain.errors import DomainValidationError, InvalidStateTransition, TaskNotFound
from src.logging_config import configure_logging
from src.routers import auth, dev, documents, webhooks

configure_logging()
app = FastAPI()

# Dev-only permissive CORS so a local browser tool can exercise the API. Read
# from the env directly (not Settings) to keep importing this module free of the
# full config in tests. Off unless DEV_MODE — production stays same-origin only.
if os.getenv("DEV_MODE", "false").lower() == "true":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(dev.router)
app.include_router(webhooks.router)


# Typed domain errors → the right status, in one place, so routers just let them
# propagate instead of each wrapping the same try/except. An *un*typed
# DomainError stays a 500 (no handler registered for the base) — that's the
# "this is a bug" signal, kept distinct from these expected conditions.
@app.exception_handler(DocumentNotFound)
@app.exception_handler(WorkflowNotFound)
@app.exception_handler(TaskNotFound)
async def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc) or "Not found"})


# Invariant/input violation (empty upload, bad definition, unknown step) → 422.
@app.exception_handler(DomainValidationError)
async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc) or "Invalid request"})


# Illegal state transition (e.g. an outcome applied to a terminal workflow) → 409.
@app.exception_handler(InvalidStateTransition)
async def _conflict_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc) or "Conflict"})


@app.get("/healthcheck")
def healthcheck():
    return {"status": "ok"}
