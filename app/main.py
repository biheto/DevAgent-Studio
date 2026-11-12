from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as project_router
from app.api.recovery import router as recovery_router
from app.core.config import settings
from app.security import IdentityMiddleware, current_principal
import os

app = FastAPI(title=settings.app_name, version="0.1.0")
if settings.app_env in {"prod", "production"} and os.getenv("DEV_AGENT_AUTH_MODE") != "required":
    raise RuntimeError("Production requires DEV_AGENT_AUTH_MODE=required")
app.add_middleware(IdentityMiddleware)
if os.getenv("DEV_AGENT_AUTH_MODE") == "required":
    # Older versions published confirmed memories with the default public ACL.
    # Tighten those notes before accepting any shared-deployment requests.
    from app.api.routes import memory_store, rag_store
    memories = {f"memory/{item['memory_id']}": item for item in memory_store.list_memories()}
    for document in rag_store.list_documents():
        memory = memories.get(document["path"])
        if memory:
            principals = [memory["scope_id"]] if memory["scope"] == "user" else [f"{memory['scope']}:{memory['scope_id']}"]
            rag_store.set_document_acl(document["collection"], document["path"], principals)


@app.get("/api/v1/auth/me")
def authenticated_identity():
    principal = current_principal()
    return {"actor_id": principal.actor_id, "role": principal.role}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


app.include_router(project_router)
app.include_router(recovery_router)

web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if web_dist.exists():
    assets_dir = web_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

    @app.get("/", include_in_schema=False)
    def web_index() -> FileResponse:
        return FileResponse(web_dist / "index.html")
