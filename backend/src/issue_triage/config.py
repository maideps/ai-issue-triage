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
    # The repository the review queue serves.
    repository: str = "astral-sh/uv"
    # Local embedding model (D-007); its size must match EMBEDDING_DIM in the migrations.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    docs_base_url: str = "https://docs.astral.sh/uv/"
    # Committed evaluation artefacts: small samples and results summaries.
    evals_dir: Path = REPO_ROOT / "evals"
    # Matches the local docker-compose `db` service; demo credentials only.
    database_url: str = "postgresql+psycopg://triage:triage@localhost:5433/issue_triage"
    github_token: SecretStr | None = None
    # Live mode (D-014): sandboxes whose new issues are triaged and which may receive
    # approved replies. Empty means live mode is off. The evaluation target
    # (`repository`) is always refused, even if listed.
    sandbox_repositories: list[str] = []
    webhook_secret: SecretStr | None = None
    # Model per pipeline step, e.g. {"triage": "anthropic/claude-haiku-5-5"} as JSON.
    # Everything defaults to the deterministic fake until a key is configured (D-007).
    llm_default_model: str = "fake/default"
    llm_models: dict[str, str] = {
        "triage": "fake/triage-keywords",
        "query_rewrite": "fake/query-keyphrases",
        "answer": "fake/answer-extractive",
        "judge": "fake/judge-overlap",
    }
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    pseudonym_salt: SecretStr = SecretStr("local-dev-salt")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, created once on first use."""
    return Settings()
