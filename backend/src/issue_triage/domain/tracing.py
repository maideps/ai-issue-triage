"""Trace spans: one per agent node and one per model call, grouped by issue run."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

SpanKind = Literal["node", "llm"]
SpanStatus = Literal["ok", "error", "interrupted"]


@dataclass(frozen=True)
class Span:
    trace_id: str  # the issue run, "owner/name#number"
    kind: SpanKind
    name: str  # node name, or the LLM step
    started_at: datetime
    duration_ms: float
    status: SpanStatus = "ok"
    parent: str | None = None  # the node an LLM call happened in
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class TraceSink(Protocol):
    def add(self, span: Span) -> None: ...


class TraceStore(TraceSink, Protocol):
    def spans(self, trace_id: str | None = None, limit: int = 5000) -> Sequence[Span]: ...
