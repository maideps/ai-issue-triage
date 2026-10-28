"""Liveness endpoint."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from issue_triage import __version__
from issue_triage.config import Settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Report that the service is up, with its version and environment."""
    settings: Settings = request.app.state.settings
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)
