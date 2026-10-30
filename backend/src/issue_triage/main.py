"""Application factory and ASGI entry point."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from issue_triage import __version__
from issue_triage.api.health import router as health_router
from issue_triage.api.kpis import router as kpis_router
from issue_triage.api.review import router as review_router
from issue_triage.api.webhooks import router as webhooks_router
from issue_triage.config import Settings, get_settings
from issue_triage.services.kpis import KpiService
from issue_triage.services.live import LiveIntake
from issue_triage.services.review_queue import ReviewQueue

QueueProvider = Callable[[], ReviewQueue]
LiveProvider = Callable[[], LiveIntake]
KpiProvider = Callable[[], KpiService]


def create_app(
    settings: Settings | None = None,
    queue_provider: QueueProvider | None = None,
    live_provider: LiveProvider | None = None,
    kpi_provider: KpiProvider | None = None,
) -> FastAPI:
    """Build the app. Tests pass explicit settings and a queue; production builds the real
    queue lazily on first use (models, BM25 index and the checkpointer are slow to load)."""
    resolved = settings if settings is not None else get_settings()
    lazy = None
    if queue_provider is None and resolved.environment != "test":
        from issue_triage.wiring import LazyReviewQueue

        lazy = LazyReviewQueue(resolved)
        queue_provider = lazy
        live_provider = lazy.live if resolved.sandbox_repositories else None
        kpi_provider = lazy.kpis

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if lazy is not None:
            lazy.close()

    app = FastAPI(title=resolved.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = resolved
    app.state.queue_provider = queue_provider
    app.state.live_provider = live_provider
    app.state.kpi_provider = kpi_provider
    app.include_router(health_router)
    app.include_router(review_router)
    app.include_router(webhooks_router)
    app.include_router(kpis_router)
    return app


app = create_app()
