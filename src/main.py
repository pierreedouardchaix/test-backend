from fastapi import Depends, FastAPI

from src.auth import AuthContext, get_current_user
from src.routers import auth, dev, documents

app = FastAPI()
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(dev.router)


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
