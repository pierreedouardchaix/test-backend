from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from src.application.apply_partner_callback import WorkflowNotFound
from src.application.get_document import DocumentNotFound
from src.auth import AuthContext, get_current_user
from src.domain.errors import TaskNotFound
from src.routers import auth, dev, documents, webhooks

app = FastAPI()
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(dev.router)
app.include_router(webhooks.router)


# Not-found domain errors → 404, in one place, so routers just let them
# propagate instead of each wrapping the same try/except. Other DomainErrors are
# unexpected and keep surfacing as 500 (no handler registered for the base).
@app.exception_handler(DocumentNotFound)
@app.exception_handler(WorkflowNotFound)
@app.exception_handler(TaskNotFound)
async def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc) or "Not found"})


@app.get("/healthcheck")
def healthcheck():
    return {"status": "ok"}


@app.get("/me")
def me(auth_ctx: AuthContext = Depends(get_current_user)):
    return {
        "user_id": str(auth_ctx.user.id),
        "first_name": auth_ctx.user.first_name,
        "last_name": auth_ctx.user.last_name,
        "tenant_id": str(auth_ctx.tenant_id),
    }
