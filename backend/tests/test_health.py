from typing import Literal

from fastapi.testclient import TestClient

from issue_triage import __version__
from issue_triage.main import create_app

from .helpers import IsolatedSettings


def make_client(environment: Literal["development", "test", "production"] = "test") -> TestClient:
    return TestClient(create_app(IsolatedSettings(environment=environment)))


def test_health_returns_ok_with_version_and_environment() -> None:
    response = make_client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "environment": "test"}


def test_health_reports_the_configured_environment() -> None:
    response = make_client("production").get("/health")

    assert response.json()["environment"] == "production"


def test_unknown_route_returns_404() -> None:
    assert make_client().get("/does-not-exist").status_code == 404


def test_openapi_schema_documents_the_health_endpoint() -> None:
    schema = make_client().get("/openapi.json").json()

    assert "/health" in schema["paths"]
