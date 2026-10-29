"""Issue embeddings in pgvector, searched with an as-of filter."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.infrastructure.db.models import IssueEmbeddingRow, IssueRow


class PostgresIssueVectorIndex:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def upsert(
        self, repository: str, numbers: Sequence[int], embeddings: Sequence[Sequence[float]]
    ) -> int:
        if len(numbers) != len(embeddings):
            raise ValueError("one embedding per issue")
        with self._sessions.begin() as session:
            ids = dict(
                session.execute(
                    select(IssueRow.number, IssueRow.id).where(
                        IssueRow.repository == repository, IssueRow.number.in_(list(numbers))
                    )
                ).all()
            )
            missing = set(numbers) - set(ids)
            if missing:
                raise ValueError(f"issues not loaded: {sorted(missing)[:5]}")
            session.execute(
                delete(IssueEmbeddingRow).where(
                    IssueEmbeddingRow.issue_id.in_([ids[n] for n in numbers])
                )
            )
            session.add_all(
                IssueEmbeddingRow(issue_id=ids[n], embedding=list(vector))
                for n, vector in zip(numbers, embeddings, strict=True)
            )
        return len(numbers)

    def search(
        self,
        repository: str,
        embedding: Sequence[float],
        *,
        before: datetime,
        exclude: int | None = None,
        k: int = 10,
    ) -> list[tuple[int, float]]:
        distance = IssueEmbeddingRow.embedding.cosine_distance(list(embedding))
        query = (
            select(IssueRow.number, distance.label("distance"))
            .join(IssueEmbeddingRow, IssueEmbeddingRow.issue_id == IssueRow.id)
            .where(IssueRow.repository == repository, IssueRow.created_at < before)
            .order_by(distance)
            .limit(k)
        )
        if exclude is not None:
            query = query.where(IssueRow.number != exclude)
        with self._sessions.begin() as session:
            # Keep scanning the HNSW graph until k rows pass the as-of filter (pgvector 0.8).
            session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            rows = session.execute(query).all()
        return sorted(((n, round(1.0 - float(d), 6)) for n, d in rows), key=lambda r: -r[1])

    def count(self, repository: str) -> int:
        with self._sessions() as session:
            return len(
                session.scalars(
                    select(IssueEmbeddingRow.id)
                    .join(IssueRow, IssueEmbeddingRow.issue_id == IssueRow.id)
                    .where(IssueRow.repository == repository)
                ).all()
            )
