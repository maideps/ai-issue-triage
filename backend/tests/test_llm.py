import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from issue_triage.domain.llm import Completion, Message, Usage
from issue_triage.infrastructure.llm.fake import FakeLLMProvider, minimal_instance
from issue_triage.infrastructure.llm.litellm_provider import LiteLLMProvider, RoutingProvider
from issue_triage.services.llm import (
    LLMClient,
    ModelRouter,
    StructuredOutputError,
    extract_json,
)
from issue_triage.services.prompts import load_prompt
from issue_triage.wiring import build_llm_client

from .helpers import IsolatedSettings


class Verdict(BaseModel):
    label: Literal["bug", "question"]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = []


class Scripted:
    """A provider that replies with the given texts in order."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.seen: list[list[Message]] = []
        self.models: list[str] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        self.seen.append(list(messages))
        self.models.append(model)
        return Completion(self.replies.pop(0), model, Usage(10, 5, 0.001, 20.0))


def client(provider: Scripted, models: dict[str, str] | None = None) -> LLMClient:
    return LLMClient(provider, ModelRouter(models or {}, "fake/default"))


def test_router_uses_per_step_models_with_a_default() -> None:
    router = ModelRouter({"triage": "anthropic/claude-haiku-5-5"}, "fake/default")

    assert router.model_for("triage") == "anthropic/claude-haiku-5-5"
    assert router.model_for("answer") == "fake/default"


def test_switching_a_step_model_is_a_settings_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISSUE_TRIAGE_LLM_MODELS", '{"triage": "fake/other"}')
    llm = build_llm_client(IsolatedSettings())

    completion = llm.complete("triage", [Message("user", "hi")])

    assert completion.model == "fake/other"
    assert llm.complete("answer", [Message("user", "hi")]).model == "fake/default"


def test_structured_output_is_validated() -> None:
    provider = Scripted('```json\n{"label": "bug", "confidence": 0.9}\n```')

    verdict, completion = client(provider).structured(
        "triage", [Message("user", "x")], Verdict, prompt="triage@v1"
    )

    assert verdict == Verdict(label="bug", confidence=0.9)
    assert completion.usage.input_tokens == 10


def test_invalid_output_is_retried_with_the_validation_error() -> None:
    provider = Scripted(
        '{"label": "feature", "confidence": 2}', '{"label": "question", "confidence": 0.4}'
    )
    llm = client(provider)

    verdict, _ = llm.structured("triage", [Message("user", "x")], Verdict)

    assert verdict.label == "question"
    retry_messages = provider.seen[1]
    assert retry_messages[-2].role == "assistant"
    assert "did not match" in retry_messages[-1].content
    assert [r.ok for r in llm.ledger.records] == [False, True]


def test_gives_up_after_retries() -> None:
    llm = client(Scripted("not json", "still not json"))

    with pytest.raises(StructuredOutputError):
        llm.structured("triage", [Message("user", "x")], Verdict)


def test_ledger_summarises_tokens_cost_and_latency_per_step() -> None:
    llm = client(Scripted("a", "b", "c"))
    llm.complete("triage", [Message("user", "x")], prompt="triage@v1")
    llm.complete("triage", [Message("user", "x")])
    llm.complete("answer", [Message("user", "x")])

    summary = llm.ledger.summary()

    assert summary["total"]["calls"] == 3
    assert summary["total"]["cost_usd"] == pytest.approx(0.003)
    assert summary["by_step"]["triage"]["input_tokens"] == 20
    assert summary["by_step"]["answer"]["latency_ms_p95"] == 20.0
    assert llm.ledger.records[0].prompt == "triage@v1"


def test_extract_json_strips_fences_and_prose() -> None:
    assert extract_json('Sure! ```json\n{"a": 1}\n``` done') == '{"a": 1}'
    assert extract_json('{"a": {"b": 2}}') == '{"a": {"b": 2}}'


def test_fake_provider_is_deterministic_and_satisfies_any_schema() -> None:
    fake = FakeLLMProvider()
    schema = Verdict.model_json_schema()

    first = fake.complete([Message("user", "issue")], model="fake/default", response_schema=schema)
    second = fake.complete([Message("user", "issue")], model="fake/default", response_schema=schema)

    assert first.text == second.text
    assert Verdict.model_validate_json(first.text).label == "bug"
    assert first.usage.cost_usd == 0.0 and first.usage.input_tokens > 0


def test_fake_provider_uses_registered_handlers() -> None:
    fake = FakeLLMProvider({"echo": lambda messages, schema: messages[-1].content.upper()})

    assert fake.complete([Message("user", "hi")], model="fake/echo").text == "HI"


def test_minimal_instance_handles_refs_optionals_and_nesting() -> None:
    class Inner(BaseModel):
        name: str

    class Outer(BaseModel):
        inner: Inner
        note: str | None
        count: int = 3

    assert minimal_instance(Outer.model_json_schema()) == {"inner": {"name": ""}, "note": ""}


def test_litellm_provider_maps_requests_usage_and_cost() -> None:
    calls: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"label": "bug"}'))],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=8),
        )

    provider = LiteLLMProvider(completion_fn=completion, cost_fn=lambda response: 0.0042)
    result = provider.complete(
        [Message("system", "s"), Message("user", "u")],
        model="anthropic/claude-haiku-5-5",
        response_schema={"type": "object"},
    )

    assert calls[0]["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert result.usage.input_tokens == 120 and result.usage.cost_usd == 0.0042


def test_litellm_provider_survives_unknown_pricing() -> None:
    def completion(**kwargs: Any) -> Any:
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))])

    def no_price(response: Any) -> float:
        raise ValueError("model not mapped")

    result = LiteLLMProvider(completion, no_price).complete([Message("user", "u")], model="x/y")

    assert result.text == "" and result.usage.cost_usd == 0.0


def test_routing_provider_keeps_fake_models_off_the_network() -> None:
    real = Scripted("real")
    routed = RoutingProvider(fake=FakeLLMProvider(), real=real)

    routed.complete([Message("user", "x")], model="fake/default")
    routed.complete([Message("user", "x")], model="anthropic/claude-haiku-5-5")

    assert real.models == ["anthropic/claude-haiku-5-5"]


def test_prompts_are_versioned_and_render_every_placeholder(tmp_path: Path) -> None:
    (tmp_path / "triage" / "v1").mkdir(parents=True)
    (tmp_path / "triage" / "v1" / "system.txt").write_text("You triage issues.", encoding="utf-8")
    (tmp_path / "triage" / "v1" / "user.txt").write_text("Title: $title\n$body", encoding="utf-8")

    prompt = load_prompt("triage", "v1", tmp_path)
    messages = prompt.render(title="Crash", body="uses ${HOME} and {braces}")

    assert prompt.ref == "triage@v1"
    assert messages[1].content == "Title: Crash\nuses ${HOME} and {braces}"
    with pytest.raises(KeyError):
        prompt.render(title="only title")
    with pytest.raises(FileNotFoundError):
        load_prompt("triage", "v9", tmp_path)


def test_settings_parse_model_map_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISSUE_TRIAGE_LLM_MODELS", json.dumps({"answer": "openai/gpt-x"}))

    assert IsolatedSettings().llm_models == {"answer": "openai/gpt-x"}
