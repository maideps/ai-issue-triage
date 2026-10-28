"""Real models through LiteLLM (any provider: ``anthropic/...``, ``openai/...``)."""

import time
from collections.abc import Callable, Sequence
from typing import Any

from issue_triage.domain.llm import Completion, LLMProvider, Message, Usage

CompletionFn = Callable[..., Any]
CostFn = Callable[..., float]


def _litellm_completion(**kwargs: Any) -> Any:
    import litellm  # imported lazily: it is slow to import and unused with the fake

    return litellm.completion(**kwargs)


def _litellm_cost(response: Any) -> float:
    import litellm

    return float(litellm.completion_cost(completion_response=response))


class LiteLLMProvider:
    def __init__(
        self,
        completion_fn: CompletionFn = _litellm_completion,
        cost_fn: CostFn = _litellm_cost,
    ) -> None:
        self._completion = completion_fn
        self._cost = cost_fn

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": response_schema},
            }
        start = time.perf_counter()
        response = self._completion(**kwargs)
        latency_ms = round((time.perf_counter() - start) * 1000, 3)
        try:
            cost = self._cost(response)
        except Exception:  # unknown pricing must not fail the call
            cost = 0.0
        usage = getattr(response, "usage", None)
        return Completion(
            text=response.choices[0].message.content or "",
            model=model,
            usage=Usage(
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                cost_usd=cost,
                latency_ms=latency_ms,
            ),
        )


class RoutingProvider:
    """Sends ``fake/...`` models to the fake provider and everything else to LiteLLM."""

    def __init__(self, fake: LLMProvider, real: LLMProvider) -> None:
        self.fake = fake
        self.real = real

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        target = self.fake if model.startswith("fake/") else self.real
        return target.complete(
            messages, model=model, response_schema=response_schema, temperature=temperature
        )
