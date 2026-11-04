"""FastAPI application and static web client."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from campus_app.config import APP_ENV, APP_SECRET, PROJECT_ROOT, SEED_DEMO_DATA
from campus_app.db import engine
from campus_app.routers import agent, auth, m1, m2, m3, shared
from campus_app.seed import seed_demo_data

if APP_ENV == "production" and (
    APP_SECRET == "local-development-only-change-me" or len(APP_SECRET) < 32
):
    raise RuntimeError("production requires a random APP_SECRET of at least 32 characters")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Optionally populate fictional development records after schema migration."""
    if SEED_DEMO_DATA:
        seed_demo_data()
    yield


app = FastAPI(
    title="校园资源服务平台",
    version="0.1.0",
    description="自习座位预约、整教室审批和物业报修",
    docs_url="/api/docs" if APP_ENV != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

for route_group in (auth.router, m1.router, m2.router, m3.router, shared.router, agent.router):
    app.include_router(route_group)


@app.get("/health", tags=["运行状态"])
def health() -> dict[str, str]:
    """Expose a lightweight health signal for local and container checks."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok", "service": "campus-resource-platform"}


app.mount("/", StaticFiles(directory=PROJECT_ROOT / "demo", html=True), name="web")
