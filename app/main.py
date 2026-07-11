from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as project_router
from app.core.config import settings
from app.persistence.sqlite_store import task_store

app = FastAPI(title=settings.app_name, version="0.1.0")


@app.on_event("startup")
def startup_seed_skills() -> None:
    task_store.seed_builtin_skills()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


app.include_router(project_router)

web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if web_dist.exists():
    assets_dir = web_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

    @app.get("/", include_in_schema=False)
    def web_index() -> FileResponse:
        return FileResponse(web_dist / "index.html")
