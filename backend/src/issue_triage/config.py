"""Typed application settings, read from ``ISSUE_TRIAGE_*`` environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/issue_triage/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration. Secrets belong in the environment, never in the repository."""

    model_config = SettingsConfigDict(
        env_prefix="ISSUE_TRIAGE_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "AI Issue Triage"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Raw GitHub data stays here, outside git (D-003).
    data_dir: Path = REPO_ROOT / "data"
    # Committed evaluation artefacts: small samples and results summaries.
    evals_dir: Path = REPO_ROOT / "evals"
    # Matches the local docker-compose `db` service; demo credentials only.
    database_url: str = "postgresql+psycopg://triage:triage@localhost:5433/issue_triage"
    github_token: SecretStr | None = None
    pseudonym_salt: SecretStr = SecretStr("local-dev-salt")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, created once on first use."""
    return Settings()
