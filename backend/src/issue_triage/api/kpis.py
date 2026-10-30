"""KPI and trace endpoints (feat-017)."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from issue_triage.services.kpis import KpiService

router = APIRouter(prefix="/api", tags=["observability"])


def service(request: Request) -> KpiService:
    provider = request.app.state.kpi_provider
    if provider is None:
        raise HTTPException(503, "observability is not configured")
    result: KpiService = provider()
    return result


@router.get("/kpis")
def kpis(request: Request) -> dict[str, Any]:
    return service(request).kpis()


@router.get("/traces/{owner}/{name}/{number}")
def trace(request: Request, owner: str, name: str, number: int) -> list[dict[str, Any]]:
    spans = service(request).trace(f"{owner}/{name}#{number}")
    return [{**asdict(s), "started_at": s.started_at.isoformat()} for s in spans]
