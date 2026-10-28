"""Application factory and ASGI entry point."""

from fastapi import FastAPI

from issue_triage import __version__
from issue_triage.api.health import router as health_router
from issue_triage.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass explicit settings; production reads the environment."""
    resolved = settings if settings is not None else get_settings()
    app = FastAPI(title=resolved.app_name, version=__version__)
    app.state.settings = resolved
    app.include_router(health_router)
    return app


app = create_app()
