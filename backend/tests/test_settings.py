import pytest
from pydantic import ValidationError

from issue_triage.config import get_settings

from .helpers import IsolatedSettings


def test_defaults() -> None:
    settings = IsolatedSettings()

    assert settings.app_name == "AI Issue Triage"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISSUE_TRIAGE_ENVIRONMENT", "production")
    monkeypatch.setenv("ISSUE_TRIAGE_LOG_LEVEL", "DEBUG")

    settings = IsolatedSettings()

    assert settings.environment == "production"
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("variable", "value"),
    [("ISSUE_TRIAGE_ENVIRONMENT", "staging"), ("ISSUE_TRIAGE_LOG_LEVEL", "VERBOSE")],
)
def test_invalid_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        IsolatedSettings()


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()
