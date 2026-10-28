"""Guardrails applied around every model call (D-002, feat-006).

Input: redact secrets and emails, fence the issue as data, flag injection attempts so the
pipeline routes the issue to a human. Output: the schema was already validated by
``LLMClient.structured``; here every string the model produced is scanned again, because
a model can echo a secret from its context into a draft reply.
"""

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from issue_triage.domain.guardrails import fence_untrusted, injection_signals, redact

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class GuardedInput:
    title: str
    body: str
    redactions: dict[str, int] = field(default_factory=dict)
    injection: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        """Suspected prompt injection: process, but never act without a human."""
        return bool(self.injection)

    @property
    def fenced_body(self) -> str:
        return fence_untrusted(self.body)


def guard_input(title: str, body: str) -> GuardedInput:
    redacted_title, redacted_body = redact(title), redact(body)
    counts = dict(redacted_title.counts)
    for kind, n in redacted_body.counts.items():
        counts[kind] = counts.get(kind, 0) + n
    return GuardedInput(
        title=redacted_title.text,
        body=redacted_body.text,
        redactions=counts,
        injection=injection_signals(title) + injection_signals(body),
    )


@dataclass(frozen=True)
class GuardedOutput(Generic[M]):
    value: M
    redactions: dict[str, int]


def _scrub(value: Any, counts: dict[str, int]) -> Any:
    if isinstance(value, str):
        result = redact(value)
        for kind, n in result.counts.items():
            counts[kind] = counts.get(kind, 0) + n
        return result.text
    if isinstance(value, list):
        return [_scrub(item, counts) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item, counts) for key, item in value.items()}
    return value


def guard_output(value: M) -> GuardedOutput[M]:
    """Redact secrets and emails from every string field, then re-validate the model."""
    counts: dict[str, int] = {}
    scrubbed = _scrub(value.model_dump(), counts)
    return GuardedOutput(type(value).model_validate(scrubbed), counts)
