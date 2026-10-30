"""Tracing for the agent: node spans, and LLM calls attributed to the node they ran in.

A context variable carries the current (run, node) while a node executes, so the LLM client
does not need to know about runs: its call records are attached to whatever node is active.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from langgraph.errors import GraphInterrupt

from issue_triage.domain.tracing import Span, SpanStatus, TraceSink
from issue_triage.services.llm import CallRecord

_current: ContextVar[tuple[str, str] | None] = ContextVar("current_node", default=None)


class Tracer:
    def __init__(self, sink: TraceSink) -> None:
        self._sink = sink

    @contextmanager
    def node(self, trace_id: str, name: str) -> Iterator[None]:
        token = _current.set((trace_id, name))
        started = datetime.now(UTC)
        start = time.perf_counter()
        status: SpanStatus = "ok"
        error: str | None = None
        try:
            yield
        except GraphInterrupt:
            status = "interrupted"  # the human gate pausing is not a failure
            raise
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            _current.reset(token)
            self._sink.add(
                Span(
                    trace_id=trace_id,
                    kind="node",
                    name=name,
                    started_at=started,
                    duration_ms=round((time.perf_counter() - start) * 1000, 3),
                    status=status,
                    error=error,
                )
            )

    def llm_call(self, record: CallRecord) -> None:
        context = _current.get()
        if context is None:
            return  # calls outside an agent run (evaluations) are only in the ledger
        trace_id, node = context
        self._sink.add(
            Span(
                trace_id=trace_id,
                kind="llm",
                name=record.step,
                started_at=datetime.now(UTC),
                duration_ms=record.latency_ms,
                status="ok" if record.ok else "error",
                parent=node,
                model=record.model,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cost_usd=record.cost_usd,
            )
        )


class MemoryTraceStore:
    def __init__(self) -> None:
        self.items: list[Span] = []

    def add(self, span: Span) -> None:
        self.items.append(span)

    def spans(self, trace_id: str | None = None, limit: int = 5000) -> list[Span]:
        matching = [s for s in self.items if trace_id is None or s.trace_id == trace_id]
        return matching[-limit:]
