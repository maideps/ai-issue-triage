"""PostgreSQL trace store."""

from collections.abc import Sequence
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.tracing import Span, SpanKind, SpanStatus
from issue_triage.infrastructure.db.models import TraceSpanRow


class PostgresTraceStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def add(self, span: Span) -> None:
        with self._sessions.begin() as session:
            session.add(
                TraceSpanRow(
                    trace_id=span.trace_id,
                    kind=span.kind,
                    name=span.name,
                    parent=span.parent,
                    status=span.status,
                    started_at=span.started_at,
                    duration_ms=span.duration_ms,
                    model=span.model,
                    input_tokens=span.input_tokens,
                    output_tokens=span.output_tokens,
                    cost_usd=span.cost_usd,
                    error=span.error,
                )
            )

    def spans(self, trace_id: str | None = None, limit: int = 5000) -> Sequence[Span]:
        query = select(TraceSpanRow).order_by(TraceSpanRow.id.desc()).limit(limit)
        if trace_id is not None:
            query = query.where(TraceSpanRow.trace_id == trace_id)
        with self._sessions() as session:
            rows = list(session.scalars(query))
        return [
            Span(
                trace_id=r.trace_id,
                kind=cast(SpanKind, r.kind),
                name=r.name,
                started_at=r.started_at,
                duration_ms=r.duration_ms,
                status=cast(SpanStatus, r.status),
                parent=r.parent,
                model=r.model,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cost_usd=r.cost_usd,
                error=r.error,
            )
            for r in reversed(rows)
        ]
