"""The LLM provider interface. Implementations live in infrastructure (D-002)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    usage: Usage


class LLMProvider(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        """Return the model's reply. ``response_schema`` asks for JSON matching it."""
        ...
