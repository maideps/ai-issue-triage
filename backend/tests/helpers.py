"""Test helpers."""

from pydantic_settings import SettingsConfigDict

from issue_triage.config import Settings


class IsolatedSettings(Settings):
    """Settings that ignore any local ``.env`` file, so tests do not depend on the machine."""

    model_config = SettingsConfigDict(env_file=None)
