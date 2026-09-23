"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers.api import router
from app.services.repository import ArtifactRepository
from app.services.scoring import ScoringService

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository = ArtifactRepository(settings.data_dir)
    app.state.repository = repository
    app.state.scorer = ScoringService(repository)
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Read-only, artifact-backed API for historical NYC 311 breach-risk replay. "
        "The SLA is a documented portfolio assumption, not an official NYC target."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)
app.include_router(router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
