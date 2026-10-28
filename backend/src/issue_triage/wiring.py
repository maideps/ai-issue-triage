"""Composition root helpers shared by the CLI and the HTTP app."""

from collections.abc import Mapping

from issue_triage.config import Settings
from issue_triage.infrastructure.llm.fake import FakeHandler, FakeLLMProvider
from issue_triage.infrastructure.llm.litellm_provider import LiteLLMProvider, RoutingProvider
from issue_triage.services.llm import CallLedger, LLMClient, ModelRouter


def build_llm_client(
    settings: Settings,
    fake_handlers: Mapping[str, FakeHandler] | None = None,
    ledger: CallLedger | None = None,
) -> LLMClient:
    provider = RoutingProvider(fake=FakeLLMProvider(fake_handlers), real=LiteLLMProvider())
    router = ModelRouter(settings.llm_models, settings.llm_default_model)
    return LLMClient(provider, router, ledger)
