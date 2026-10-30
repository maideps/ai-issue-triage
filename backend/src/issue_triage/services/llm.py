"""LLM calls the way the rest of the system makes them: per-step model routing,
validated structured output, and a ledger of tokens, cost and latency for every call.
"""

import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import quantiles
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from issue_triage.domain.llm import Completion, LLMProvider, Message

T = TypeVar("T", bound=BaseModel)
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class StructuredOutputError(RuntimeError):
    """The model never produced JSON matching the schema."""


class ModelRouter:
    """Which model serves which step; changing a step's model is a settings change."""

    def __init__(self, models: Mapping[str, str], default: str) -> None:
        self._models = dict(models)
        self._default = default

    def model_for(self, step: str) -> str:
        return self._models.get(step, self._default)


@dataclass(frozen=True)
class CallRecord:
    step: str
    model: str
    prompt: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    ok: bool


@dataclass
class CallLedger:
    records: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> None:
        self.records.append(record)

    def summary(self) -> dict[str, Any]:
        def stats(records: list[CallRecord]) -> dict[str, Any]:
            latencies = sorted(r.latency_ms for r in records)
            p50, p95 = _percentiles(latencies)
            return {
                "calls": len(records),
                "failed": sum(1 for r in records if not r.ok),
                "input_tokens": sum(r.input_tokens for r in records),
                "output_tokens": sum(r.output_tokens for r in records),
                "cost_usd": round(sum(r.cost_usd for r in records), 6),
                "latency_ms_p50": p50,
                "latency_ms_p95": p95,
            }

        by_step: dict[str, list[CallRecord]] = defaultdict(list)
        for record in self.records:
            by_step[record.step].append(record)
        return {
            "total": stats(self.records),
            "by_step": {step: stats(records) for step, records in sorted(by_step.items())},
        }


def _percentiles(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], values[0]
    cuts = quantiles(values, n=100, method="inclusive")
    return round(cuts[49], 1), round(cuts[94], 1)


def extract_json(text: str) -> str:
    """Strip code fences and surrounding prose around the first JSON object."""
    stripped = FENCE.sub("", text.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start : end + 1] if start != -1 and end > start else stripped


class LLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        router: ModelRouter,
        ledger: CallLedger | None = None,
        *,
        max_validation_retries: int = 1,
        on_call: Callable[[CallRecord], None] | None = None,
    ) -> None:
        self.provider = provider
        self.router = router
        self.ledger = ledger if ledger is not None else CallLedger()
        self.on_call = on_call
        self._max_validation_retries = max_validation_retries

    def complete(
        self,
        step: str,
        messages: Sequence[Message],
        *,
        prompt: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> Completion:
        model = self.router.model_for(step)
        completion = self.provider.complete(messages, model=model, response_schema=response_schema)
        self._record(step, prompt, completion, ok=True)
        return completion

    def structured(
        self,
        step: str,
        messages: Sequence[Message],
        schema: type[T],
        *,
        prompt: str | None = None,
    ) -> tuple[T, Completion]:
        """Ask for JSON matching ``schema``; on invalid output, show the error and retry."""
        conversation = list(messages)
        json_schema = schema.model_json_schema()
        error: ValidationError | None = None
        for _ in range(self._max_validation_retries + 1):
            model = self.router.model_for(step)
            completion = self.provider.complete(
                conversation, model=model, response_schema=json_schema
            )
            try:
                parsed = schema.model_validate_json(extract_json(completion.text))
            except ValidationError as exc:
                error = exc
                self._record(step, prompt, completion, ok=False)
                conversation += [
                    Message("assistant", completion.text),
                    Message(
                        "user",
                        "Your reply did not match the required JSON schema: "
                        f"{json.dumps(exc.errors(include_url=False), default=str)[:800]}. "
                        "Reply with a single JSON object only.",
                    ),
                ]
                continue
            self._record(step, prompt, completion, ok=True)
            return parsed, completion
        raise StructuredOutputError(f"step {step!r}: no valid JSON after retries: {error}")

    def _record(self, step: str, prompt: str | None, completion: Completion, *, ok: bool) -> None:
        usage = completion.usage
        record = CallRecord(
            step=step,
            model=completion.model,
            prompt=prompt,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
            latency_ms=usage.latency_ms,
            ok=ok,
        )
        self.ledger.add(record)
        if self.on_call is not None:
            self.on_call(record)
