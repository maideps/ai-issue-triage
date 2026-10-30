"""PostgreSQL store for the review queue."""

from collections.abc import Sequence
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.reviews import FeedbackRecord, ReviewDecision, RunRecord, RunState
from issue_triage.infrastructure.db.models import ReviewFeedbackRow, TriageRunRow


def _to_record(row: TriageRunRow) -> RunRecord:
    return RunRecord(
        repository=row.repository,
        number=row.number,
        title=row.title,
        status=cast(RunState, row.status),
        action=row.action,
        confidence=row.confidence,
        needs_attention=row.needs_attention,
        proposal_text=row.proposal_text,
        citations=tuple(row.citations),
        evidence=dict(row.evidence),
        final_text=row.final_text,
        updated_at=row.updated_at,
    )


class PostgresRunStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _row(session: Session, repository: str, number: int) -> TriageRunRow | None:
        return session.scalar(
            select(TriageRunRow).where(
                TriageRunRow.repository == repository, TriageRunRow.number == number
            )
        )

    def upsert(self, run: RunRecord) -> None:
        with self._sessions.begin() as session:
            row = self._row(session, run.repository, run.number) or TriageRunRow(
                repository=run.repository, number=run.number
            )
            row.title = run.title
            row.status = run.status
            row.action = run.action
            row.confidence = run.confidence
            row.needs_attention = run.needs_attention
            row.proposal_text = run.proposal_text
            row.citations = list(run.citations)
            row.evidence = dict(run.evidence)
            row.final_text = run.final_text
            session.add(row)

    def get(self, repository: str, number: int) -> RunRecord | None:
        with self._sessions() as session:
            row = self._row(session, repository, number)
            return _to_record(row) if row else None

    def list(self, status: RunState | None = None, limit: int = 100) -> list[RunRecord]:
        query = select(TriageRunRow).order_by(
            TriageRunRow.needs_attention.desc(), TriageRunRow.updated_at.desc()
        )
        if status is not None:
            query = query.where(TriageRunRow.status == status)
        with self._sessions() as session:
            return [_to_record(row) for row in session.scalars(query.limit(limit))]

    def add_feedback(self, feedback: FeedbackRecord) -> None:
        with self._sessions.begin() as session:
            row = self._row(session, feedback.repository, feedback.number)
            if row is None:
                raise ValueError(f"no run for {feedback.repository}#{feedback.number}")
            session.add(
                ReviewFeedbackRow(
                    run_id=row.id,
                    decision=feedback.decision,
                    original_text=feedback.original_text,
                    final_text=feedback.final_text,
                    reviewer=feedback.reviewer,
                )
            )

    def feedback(self, repository: str, number: int) -> Sequence[FeedbackRecord]:
        query = (
            select(ReviewFeedbackRow)
            .join(TriageRunRow, ReviewFeedbackRow.run_id == TriageRunRow.id)
            .where(TriageRunRow.repository == repository, TriageRunRow.number == number)
            .order_by(ReviewFeedbackRow.id)
        )
        with self._sessions() as session:
            return [
                FeedbackRecord(
                    repository,
                    number,
                    cast(ReviewDecision, row.decision),
                    row.original_text,
                    row.final_text,
                    row.reviewer,
                    row.created_at,
                )
                for row in session.scalars(query)
            ]
